# Public Submission Custody Observation

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.2]; docs/specifications/03-Manager_Architecture.md [MA-1.4]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-11.1]; docs/specifications/07-System_Invariants.md [IMMUT.3], [STATE.2], [OBS.6], [OBS.13.7], [LIVENESS.R1], [LIVENESS.R3], [LIVENESS.R5], [LIVENESS.R6]
Superseded by: none

Class: 5. Public observation contract revision crossing submission custody and task observation. Hardening and independent review are required. The user authorized full implementation, including slice 5 and all review gates, on 2026-10-19. Keep status `draft` until implementation and its gates are complete.

## Goal

Expose Weft's existing durable submission custody through its existing full-TID point observation APIs. A caller holding an accepted TID must be able to distinguish a request still queued or reserved from missing evidence before child metadata exists. Consumers such as Enzan must not reconstruct Weft's queue layout, manager registry, lifecycle, or liveness rules.

A separately reviewed fifth slice adds explicit typed runtime-liveness observations
with provenance and freshness. Custody and runtime liveness stay separate: a
queued request has custody without runtime evidence; a completed task can still
have a live runtime during teardown. This slice introduces no implicit probes
into status, terminal snapshots, submission, watches or control decisions.

## Source Documents and Baseline

Plan type: implementation with spec revision. Authoring baseline: `e78ffc41befa628f70fe4bb21a947a3fe765648a` (Weft 0.9.113). Both contract deltas were promoted on 2026-10-19. Exact promotion hashes and rerunnable spec diffs are recorded in the execution checkpoints below; the current source specs govern.

Read [program theory](../program-theory.md), [decision hierarchy](../agent-context/decision-hierarchy.md), [engineering principles](../agent-context/engineering-principles.md), [writing plans](../agent-context/runbooks/writing-plans.md), [hardening plans](../agent-context/runbooks/hardening-plans.md), and [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md). Governing contracts are [manager architecture](../specifications/03-Manager_Architecture.md) [MA-1.4]; [message flow](../specifications/05-Message_Flow_and_State.md) [MF-1], [MF-5], [MF-6]; [Python surfaces](../specifications/14-Python_API_Surfaces.md) [PY-2]; [invariants](../specifications/07-System_Invariants.md) [IMMUT.3], [STATE.1], [STATE.2], [OBS.6], [OBS.13.7], [LIVENESS.R3]; and [testing](../specifications/08-Testing_Strategy.md) [TS-0], [TS-1].

The completed [task-owned observation plan](./2026-10-14-task-monitor-read-separation-and-simplification-plan.md) explains the current ownership split; it is context, not permission to reinstate global-history fallback. Standing refused alternatives [ALT-WT-015], [ALT-WT-016], [ALT-WT-019], and [ALT-WT-020] remain in force: no manager-memory authority, per-dispatch backlog replay, relational replacement for queues, or public MonitorStore fallback.

## Evidence and Current Structure

The causal premise was checked against real temporary SQLite contexts in this session. Submission committed a request and returned its exact TID, with only the manager-start/readiness boundary prevented from launching processes. The private spawn reconciler returned `queued`; public status in 0.9.113 returned `unknown`/`observation_metadata_unavailable`, and the terminal snapshot was unknown and nonterminal. The same custody gap exists in installed 0.9.111. The public result call correctly timed out; this is an observation gap, not evidence of a lost result or lost submission. The durable regression will reproduce this setup inside Weft, without importing Enzan or relying on temporary probe scripts.

| Existing owner | Load-bearing behavior and planned use |
| --- | --- |
| `weft/core/spawn_requests.py::_submit_spawn_request_with_payload` | Writes the accepted request. Its broker message ID is the TID, including the explicit-TID path. Reuse this identity; add no correlation ID. |
| `weft/commands/_spawn_submission.py::_reconcile_submitted_spawn_once` | Checks task state, global history, then exact source/reserved custody. Reuse only the custody leaf, never export this whole reconciler as the public observer. |
| `weft/commands/_spawn_submission.py::_reserved_spawn_request_queue_names` | Currently goes through `manager_runtime.list_manager_records`. Even `prune_stale=False` reaches legacy registry deletion. This is unsuitable for a pure point read. |
| `weft/core/service_convergence.py` | Owns service-record parsing, context keys and manager projection. Reuse its accepted schema and canonical routing rather than copying JSON validation. |
| `weft/core/task_evidence.py::known_tid_evidence` | Owns task-local terminal/runtime precedence. Keep this authority and its result/control consumers intact. |
| `weft/commands/tasks.py::_known_status_record` | Projects full-TID status. Currently overlays terminal or live-PONG evidence; adding a new evidence type alone would not expose custody here. |
| `weft/commands/tasks.py::_terminal_observation_snapshot` | Projects non-consuming terminal snapshots. Its positive-timeout owner already registers task-local interests before checking and waits to terminal evidence or the original deadline. |
| `weft/client/_namespaces.py`, `weft/client/_task.py` | Existing `tasks.status`, `tasks.terminal_snapshot`, retained `Task.snapshot` and `Task.terminal_snapshot` delegate to command owners. CLI task status uses the same point owner. |

Before implementation, record answers to these comprehension checks in the execution log. Wrong answers stop the slice: (1) What does a returned TID prove? Expected: the exact spawn request was committed, not that a manager or worker is ready. (2) Can a retained reservation prove execution has not begun? Expected: no; launch intentionally retains custody until child handoff, so task-local proof wins. (3) Why not publish the private reconciler? Expected: global-history acquisition, manager-specific wait semantics and registry cleanup do not belong in public read-only observation.

## Intended Contract and Boundaries

For custody slices 1–4, use existing APIs and return types. This is an additive set of observed status values, not a new persistent lifecycle state, request identifier, receipt format, service, or public method. Scope is a canonical full TID in the caller's resolved context; short-TID search and bulk inventory remain unchanged.

| Evidence with no stronger task-local proof | Point status | Terminal snapshot |
| --- | --- | --- |
| Exact pending ID in ordinary or internal spawn source | `status="queued"`, `event="submission_queued"`, reconciliation classification `submission_queued` | `status="queued"`, `source="spawn_queue"`, classification `submission_queued`, `terminal=False` |
| Exact pending ID in a canonical manager ordinary or internal reserved queue | `status="reserved"`, `event="submission_reserved"`, reconciliation classification `submission_reserved` | `status="reserved"`, `source="manager_reserved"`, classification `submission_reserved`, `terminal=False` |
| No positive evidence | Existing unknown behavior | Existing unknown, nonterminal behavior |

Custody snapshots expose identity and classification only: no spawn payload, inferred execution time, process identity, error, result, or acknowledgement target. For a custody-only `TaskSnapshot`, use its TID as the fallback name, empty metadata, absent optional runtime/lifecycle fields, and `last_timestamp=0` (no task publication timestamp). For `TaskTerminalSnapshot`, use `observed_at=None`, empty acknowledgement targets, no result fields, and the classification in metadata. The accepted TID is not repurposed as an observation timestamp. Reserved queue names remain internal.

Only pending/unclaimed messages establish custody. Claimed deletion residue, a queue's existence, a live manager, or the accepted handle alone do not. Read source queues in their existing order, then canonical reserved routes; if duplicate custody exists, source evidence wins deterministically. This does not repair duplicate delivery.

Preserve existing task evidence precedence. The status fallback predicate is `base is None and evidence is None` before construction of the synthetic unknown snapshot; the terminal fallback predicate is `evidence is None`. Both use the existing validated task-state reader. An invalid projection inside a valid row keeps [MF-5]'s diagnostic/unknown behavior; a stale valid mapping keeps its current interpretation. Malformed task-state rows keep [OBS.6] meaning: the reader skips them, and a malformed-only queue has no valid state. Do not add a raw malformed-row scan or change reader validation. Terminal and live task evidence must never be replaced by retained reservation evidence.

Point reads are observations across several queues, not a linearizable absence proof. Use one ordered pass: task-local evidence E, source queues S, registry discovery, reserved queues R, then fresh task-local evidence E-prime after the custody lookup whether it hit or missed. Skip registry/R when S hits. E-prime wins when it supplies task evidence or a valid current row; otherwise return the custody hit or unknown. Refresh the status base projection together with E-prime. The source-to-reserved move is atomic, managers register before serving, and child publication precedes reservation ACK; this order follows those forward handoffs. Reverse moves can still escape a pass. Do not repeat the registry/custody pass, introduce a retry loop, or sleep. A continuously moving request can still yield `unknown`; absence does not prove rejection, death, timeout, or authority to resubmit. No cross-queue transaction or lock is added.

Positive-timeout terminal snapshots still wait for terminal evidence or the original caller deadline. Custody transitions need not wake that wait. Existing exact task-state/completion subscriptions preserve terminal wakeups; make the custody fallback only when returning a zero-timeout result or an otherwise unknown snapshot at the original deadline, instead of acquiring/caching custody on each wait iteration. The timeout bounds terminal waiting, not total call duration: the final custody observation and task-evidence refresh may require additional backend I/O. Registry discovery costs O(R + M), so no fixed or necessarily small deadline overrun is promised. A zero-timeout point read performs immediate reads without an activity subscription; it does not promise zero elapsed time. Do not add global spawn/registry/reserved subscriptions simply to observe intermediate states.

### Invariants, hidden couplings and failure ownership

- Acceptance and [IMMUT.3] are unchanged. Template/resolved TaskSpecs, immutable spec/io, transport and queue names are unchanged. No new writes, claims, moves, acknowledgements, managers or cleanup are initiated by observation.
- [STATE.1]/[STATE.2] remain lifecycle rules. `queued` and `reserved` describe custody, not OS liveness or a rollback from terminal state. Existing liveness and LivenessMonitor deletion ownership remain authoritative.
- Registry discovery must be read-only, scoped to the current canonical context, and include stopped/superseded owners whose reservations may remain. Use `iter_broker_queue_json_entries(strict=True)` from `weft/core/queue_window.py` with `closing_queue_iterator` from `weft/helpers/__init__.py`. Project once with `project_manager_service_record(service_key=manager_service_key(ctx))`, fold the latest valid record per owner, then apply `is_canonical_manager_record`. Do not also call `collect_service_owner_records` and parse the same record twice. Malformed JSON/non-object data and unsupported schemas are non-evidence; strict mode propagates broker read failures, not malformed-row errors. Public custody reads do not bootstrap the registry or prove compatibility/readiness. Preserve the private reconciler's existing cleanup/future-schema failure via its explicit bootstrap wrapper, and preserve actual manager bootstrap. Do not call registry maintenance under a public read-only name.
- Expected absence is a value; broker/read failures retain typed command errors. An auxiliary custody lookup must not run after decisive task evidence and thereby downgrade a successful observation. Resources borrow the caller broker and preserve its lifetime, including early returns and failures.
- Cost is independent of spawn backlog size: exact-ID peeks, no backlog payload hydration or body search. Reserved lookup necessarily traverses retained service registry records and checks deduplicated canonical manager routes. Record the number of records/routes inspected as O(R + M), in retained registry records R and distinct manager owners M; do not promise O(1), scan the queue namespace, cap history and miss older owners, or add a cache/index lifecycle.
- Control-surface waits, controllability checks, interactive STOP and status/event watch keep their current acquisition and output behavior, including no new custody/registry scans. The one-shot snapshots attached by `cmd_task_ping` and `_task_control_result` already call public `task_snapshot` and inherit its custody observations; they are not wait/control-decision loops. Their acknowledgement, success and failure decisions stay unchanged. Public bulk list/stats/system inventory still reflects task-state publication. A newly accepted TID can be observable by point query before appearing in bulk inventory. Explicit history/watch streams do not promise every custody transition. No change to result acquisition, control, cleanup or acknowledgement semantics.

### Runtime-liveness extension boundary (slice 5)

The existing `weft/liveness/analysis.py::analyze_liveness` may return `stale`
from a terminal hint, including when runtime identity is unavailable. Its
`attempted` flag belongs to retirement policy, and a missing registered probe
can currently produce a completed `unknown` analysis. Neither is a safe public
proof of runtime death or a public record of whether a probe ran. Reuse the
underlying authority-aware host and extension checks, separating raw runtime
evidence from terminal-hint policy while preserving all existing monitor and
Manager behavior. Do not expose the private reducer or its generation hash as
a public API.

## Proposed Spec Delta

The following records the approved delta, promoted on 2026-10-19 during implementation. Strategy A promoted contract text before implementation symbols; final reciprocal mappings now name the current worktree owners. The source specs govern current behavior.

### MA-1.4: insert after the paragraph ending `by publishing their own current v2 heartbeats.`

> Full-TID public submission-custody observation under [MF-5] is a read-only exception to this bootstrap requirement. It may inspect retained service records without deleting v1 rows. It folds the latest valid v2 record per owner for the resolved context's manager service key before canonical filtering; a newer malformed row does not displace a valid one. It uses the resulting canonical records as reserved-route evidence, ignoring malformed and unsupported-schema rows, including future schemas. It does not claim registry compatibility or manager readiness. Broker read failures remain errors. This exception does not change private submission reconciliation, manager/service bootstrap, system status or Monitor bootstrap, including their existing future-schema rejection and legacy-row cleanup.

### MF-5: replace the sentence `Full-TID snapshot absence is unknown evidence.`

> Full-TID snapshot absence alone does not establish absence of an accepted request. When task-state metadata and positive task-local evidence are absent, known-TID point status and non-consuming terminal snapshots read the exact pending spawn-request ID in ordinary/internal source queues and canonical manager ordinary/internal reserved queues in the resolved context. Source custody reports `queued`; reserved custody reports `reserved`. These are nonterminal observations of broker custody, not TaskSpec lifecycle states or proof that execution has not started. Task-local evidence retains precedence; invalid projection inside a valid row and stale valid task-state metadata retain their existing interpretation. Malformed task-state rows retain [OBS.6] semantics and do not create a new custody suppression rule. Claimed residue and manager liveness alone do not establish submission custody.
>
> Custody reads do not mutate registry or queue state, start services, reconstruct task configuration from spawn payloads, or acquire global task history or Monitor data. Discovery includes retained stopped/superseded canonical manager owners. Source queues are checked before reserved queues. One observation checks task-local evidence, then source queues, then (on source miss) the registry and reserved queues, then rechecks task-local evidence and current projection after the custody lookup whether custody was found or not. Fresh task evidence/current projection wins over custody. There is no second registry/custody pass. Reads do not promise an atomic cross-queue snapshot. Without positive evidence the observation remains unknown, which does not authorize resubmission or imply terminal failure. Database failures remain typed errors.
>
> Positive-timeout terminal observation continues to wait for terminal evidence or the original caller deadline using existing task-local subscriptions. Intermediate custody transitions do not require a wakeup. At the original deadline an otherwise unknown snapshot receives a fresh custody observation. The timeout bounds terminal waiting, not total call duration; the final custody observation and task-evidence refresh may require additional backend I/O, with no fixed overrun bound. Intermediate wait iterations do not acquire custody. Pure point reads create no activity subscription. Control-decision loops, interactive STOP, bulk inventory and status/event watch retain their existing acquisition and output semantics; these paths do not gain custody lookups. One-shot public snapshots attached to ping/stop/kill results inherit the point-snapshot contract without changing command acknowledgement, success or failure decisions. Result acquisition, controls, settlement, liveness and cleanup keep their existing contracts.

### PY-2: insert after the paragraph ending `Bare-TID callers use the task-specific discovery boundary in [MF-5].`

> Full-TID `client.tasks.status(tid)` and `client.task(tid).snapshot()` (including retained accepted handles), and their command/CLI point-status adapters, expose submission custody under [MF-5] before task-state publication. `client.tasks.terminal_snapshot(tid, timeout=...)` and `Task.terminal_snapshot(timeout=...)` expose the same custody through their existing return types. Their timeout bounds terminal waiting; final observation may require additional backend I/O under [MF-5]. No new submission or observation method is required.
>
> Custody-only point snapshots use status `queued`/`reserved`, event and reconciliation classification `submission_queued`/`submission_reserved`, the TID as fallback name, empty metadata, absent optional runtime/lifecycle fields and `last_timestamp=0`. Custody-only terminal snapshots use the same status, source `spawn_queue`/`manager_reserved`, metadata classification `submission_queued`/`submission_reserved`, `terminal=False`, `observed_at=None`, empty acknowledgement targets and no result/error fields. They expose neither spawn payload nor reserved queue names. These statuses are additive public observation values, not new TaskSpec states. A point query may see an accepted request before bulk inventory sees a task publication.

### Runtime-liveness spec delta: slice 5 only

Promotion strategy A in slice 5, after its own independent review and before its
implementation. Slice 1 promotes only the custody paragraphs above. Record a
separate slice-5 promotion baseline and keep specs as the governing contract
after promotion. The following paragraphs and field table are the proposed
normative text; all API/type names in this subsection are **new**.

**PY-2: append after the custody observation contract; PY-1: include the new
public result type in the export contract.**

`client.tasks.runtime_liveness(tid, *, timeout=2.0)` and
`Task.runtime_liveness(*, timeout=2.0)` explicitly request one runtime-liveness
check and return a frozen `RuntimeLivenessSnapshot`, exported from `weft.client`
and backed by `weft.commands.types`. Both delegate to one typed-error command
owner, `weft/commands/tasks.py::task_runtime_liveness` (new). The default uses
`LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS`, not a new constant. Only an unprefixed canonical 19-character ASCII decimal full TID is accepted;
whitespace, a leading `T`, short IDs and zero are rejected. Validate spelling
with `is_task_tid` and range with `normalize_exact_message_id`, requiring a
positive value; translate failure to `InvalidTID` before any probe, without
short-TID lookup. Unlike terminal-snapshot normalization, invalid input raises
rather than returning a synthetic missing result. Timeout must be finite and positive (booleans are
invalid), otherwise raise `CommandUsageError`. Calling this method is the
explicit opt-in; there is no passive mode, automatic PING, new CLI verb, or new
probe flag on existing snapshot methods. Existing observation methods keep
their behavior and gain no extra probes. Accepted TaskSpecs cannot supply
runtime identity; fresh and retained handles read the same task-state owner.

| Field | Type and meaning |
| --- | --- |
| `tid` | `str`: requested canonical full TID. |
| `state` | `Literal["live", "dead", "unknown"]`: observation of the selected runtime identity, not task lifecycle, progress or readiness. `dead` means positively not live at this check; it does not promise that a restartable runtime can never run again. |
| `source` | `Literal["host-pid", "runner", "external-supervisor", "none"]`: selected authority, or `none` when no supported authority is available. |
| `runner`, `runtime_id` | `str | None`: identity from the validated selected runtime handle; both absent without a valid handle. No complete handle or arbitrary provider payload is copied. |
| `provider` | `str | None`: normalized liveness-provider key selected for runner/external-supervisor authority, including when loading fails or host scoping prevents a probe; absent for host-pid or unsupported/missing authority. This can differ from the runner name for aliased providers. |
| `reason` | `str`: one of the stable reason codes enumerated below; no exception text or provider secrets. |
| `snapshot_message_id` | `int | None`: exact selected task-state row ID, absent when no valid row exists. It identifies the input evidence; it is not the time this check ran. |
| `check_started_at_ns`, `check_completed_at_ns` | `int`: observer wall-clock Unix nanoseconds bracketing the check. These are new per-call measurements, not a persisted last-seen timestamp or liveness lease. Use monotonic time for budgets; clock adjustment may invert wall-clock ordering. |

Freshness means only that a check occurred within the reported interval against
the selected evidence. There is no freshness TTL, health score, or guarantee
past the observation. An old task-state publication is not proof of inactivity:
publication remains edge-triggered. No new durable observation store or periodic
republishing is introduced.

**CC-3.2: append after the existing point-in-time authority paragraph.**

Explicit public runtime observation uses raw runtime evidence under
`RunnerHandle.control.authority`, before terminal-hint retirement policy.
The existing CC-3.2 authority paragraph governs host identity reduction and
extension authority; this addition does not create a second host truth table.
First validate the runtime handle: a missing or invalid handle gives
`unknown` / `runtime_handle_unavailable`, with source `none` and absent identity
and provider, even on a foreign-host row. For a valid handle, apply the
publication's host scope: a nonempty
`hostname` string different from `socket.gethostname()` gives `unknown` /
`runtime_unresolved` for every authority, without loading a provider or invoking
any probe. A hostname that is not a nonempty string counts as absent. Derive
the provider key without loading it even when this gate stops an extension
check, so provenance still describes the selected evidence. For host-pid
authority, absent hostname also leaves scope
unproven and gives that same unknown result without a probe. For extension
authorities with no published hostname, the selected provider remains the
scope authority. Keep these public preconditions in the command owner; do not
change the monitor's existing missing-hostname interpretation or move this new
restriction into legacy analysis.

Subject to scope, map raw `live`, `stale`, `unknown` respectively to public
`live` / `runtime_live`, `dead` / `runtime_dead`, and `unknown` /
`runtime_unresolved`. Missing/malformed identity cannot become dead, a reused
PID is not the original identity, and a terminal lifecycle hint alone never
gives dead. For extension authority a live wrapper cannot override the provider.
Use `liveness_provider_key(handle)` for routing and provenance. When its callback
is not registered, the command calls `weft._runner_plugins.get_runner_plugin`
once with that key, then checks registration again. This is the same installed
entry-point loading/registration mechanism used by the monitor; no service,
runtime, SDK preflight or unrelated plugin is started. Preserve already
registered callbacks without loading a factory over them. Any `Exception` raised while loading the provider, or a
still-missing callback, gives `unknown` / `probe_unavailable`; do not catch `BaseException` or convert
interrupt/exit signals to observations. Callback failure or invalid return gives `unknown` / `probe_failed`. No valid runtime handle gives
`unknown` / `runtime_handle_unavailable`; unsupported authority gives `unknown` /
`authority_unsupported`. The public result has no `attempted` flag: absence,
failure and timing are expressed by reason codes, without repurposing the
private retirement-policy flag.
The private liveness analysis, retirement deadlines and deletion policy retain
their existing terminal-hint and attempted semantics.

Timeout is a cooperative probe budget measured monotonically from method entry.
Check the remaining budget before provider loading and again before starting a
host batch or extension callback; pass only the remaining budget to a callback.
Do not start that phase when the budget is exhausted; return `unknown` /
`probe_timeout`, still subject to final revalidation and snapshot_changed
precedence. After a probe batch returns
or raises, compare elapsed monotonic time against the original budget: expiry
gives `unknown` / `probe_timeout` regardless of the raw result or exception.
Apply this rule to host batches as well as extensions; do not introduce a
per-identity retry or budget loop. Within budget, exceptions (including an early
provider-specific timeout exception) give `probe_failed`; do not parse exception
text or guess SDK timeout types. An unavailable provider that finishes loading
after the budget is also `probe_timeout`. This is measured at completion of the
probe/preparation phase, before final broker revalidation. With no initial valid
row, return `runtime_handle_unavailable` immediately without a probe. Other
no-probe branches with a selected row still receive final revalidation.

Result precedence is: a final broker failure raises a typed command error;
otherwise `snapshot_changed` overrides `probe_timeout`, which overrides probe
failure or raw runtime evidence. Final broker I/O alone does not retroactively
expire an in-budget probe. Synchronous OS/provider calls and final broker reads
may overrun the budget; there is no hard wall-time guarantee or thread/process
introduced to enforce one. Execute at most one probe batch without polling or
retries. Provider registration remains process-local.

**MF-5: append after the custody point-observation paragraphs.**

The explicit runtime-liveness command reads the newest valid task-state row
through `read_task_state_snapshot`, probes its validated runtime identity, then
re-reads once using `read_task_state_snapshot(..., previous=selected)`. Use two
`broker=None` reader calls so their existing scopes release connection leases
before the potentially slow probe, preserving any caller-owned session. Do not
wrap both reads and the callback in one broker loan. Use the existing
`@typed_command_errors` boundary for the command owner. Compare selected rows by message ID, never tuple/object identity: changed means
the re-read returns `None` or a different message ID. A changed or removed row
invalidates the result: return `unknown` / `snapshot_changed`, retaining the
original input row ID and identity as provenance. This rule takes precedence
over the probe outcome, including timeout or death. Do not re-probe a replacement
or fall back to an older runtime identity; malformed tails retain [OBS.6]
reader semantics. With no initial valid row, return `unknown` /
`runtime_handle_unavailable` without a probe. A final read cannot provide
linearizability: a subsequent change is outside this point observation.
Broker/read failures remain typed command errors rather than unknown values.

The reason-code vocabulary is exactly `runtime_live`, `runtime_dead`,
`runtime_unresolved`, `probe_unavailable`, `probe_failed`,
`runtime_handle_unavailable`, `authority_unsupported`, `probe_timeout`, and
`snapshot_changed`. The operation does not read spawn custody, service registry,
global lifecycle history or Monitor state; it writes no queue, sends no signal
or control message, and cannot authorize resubmission, cleanup or result
acknowledgement. It never rewrites task lifecycle status. Public custody
acquisition and handoff ordering remain shared as specified for slices 1–4;
this separate runtime probe does not enter that fallback or broaden its callers.

## Implementation Slices

Global style: follow existing typed dataclasses and direct functions; no generic evidence-provider framework, registry cache, new dependency, control channel or Enzan-specific branch. Each slice gets an outside cross-family review via `skills/call-agent/SKILL.md` after its tests, with findings dispositioned before the next slice. Repeat a final review of the resulting change. Preserve observed RED failure and GREEN evidence; do not mock the evidence selector, custody answer or public API being tested.

### 1. Promote the contract and pin the public failure

Read first: [MF-5], [PY-2], `tests/commands/test_submission_custody.py`, `tests/commands/test_task_owned_observation.py`, `tests/helpers/weft_harness.py`. Modify the three source specs (MA-1.4, MF-5 and PY-2), this plan and its index as appropriate. Add **new** `tests/commands/test_submission_observation.py` for the public acceptance-to-observation contract.

Reproduce real public submission with real broker writes and only the manager-start boundary suppressed. Close the submitting client; through a fresh client, assert the committed TID is reported queued by point status and zero-timeout terminal snapshot. Assert empty acknowledgement targets and retained request content. Before code changes this must fail on `unknown`, not setup/import errors. Record promotion baseline, exact RED assertion and existing-contract guards. Do not parse exception text or add an identity protocol.

Done when proposed text is promoted, the baseline identifier is recorded, doc gates pass and the new regression fails for the intended reason. Outside review covers the promoted contract and falsifiability before slice 2. Slices 1 and 2 are review checkpoints, not independently landable commits while this regression is RED. Land slices 1-3 together once GREEN; do not add an xfail, skip, or weaken the assertion to make an intermediate commit pass.

### 2. Share exact custody lookup without read side effects

Read first: `weft/core/spawn_requests.py`, `weft/core/service_convergence.py`, `weft/core/manager_runtime.py::_snapshot_registry`, `weft/commands/_spawn_submission.py`, `tests/commands/test_submission.py`. Also read `weft/core/queue_window.py` and `weft/helpers/__init__.py`. Modify the existing spawn-request module and private reconciler; extend `tests/commands/test_submission.py` and the new submission-observation tests only where they own a distinct contract.

Extract a narrow exact-ID custody helper into `weft/core/spawn_requests.py` (new symbol, name selected locally). It returns queued/reserved/absent plus the reserved route needed privately. Reuse exact broker peeks and the read-only service-schema primitives. Stream registry JSON strictly, project once, fold latest valid manager records per owner, filter canonical routes after that fold, and deduplicate reserved names. Generate ordinary/internal reserved names from the existing suffix constants. Keep latest-owner/canonical filtering: a bare set of historical owner IDs could revive an older canonical route after a later valid noncanonical record. Verify imports remain acyclic; do not import command code or `manager_runtime` into this leaf. The shared exact-ID lookup accepts a lazy iterable of reserved routes, consumed only after source misses. Public calls supply the read-only route iterator. The private `_reserved_spawn_request_queue_names` wrapper retains explicit `discard_v1_service_registry_rows` bootstrap, borrowing/owning the broker as today, then delegates the shared read-only route discovery. Both existing owned-broker and borrowed-broker paths bootstrap: the owned path does so through `manager_runtime._registry_queue`, and the borrowed path does so directly in `_snapshot_registry`. Preserve both, including cleanup-on-error ownership. The private wrapper returns `tuple(sorted(set(...)))` as today; stable ordering is required by `_spawn_reconciliation_queue_specs` monitor-topology comparison. Its exact-ID lookup caller supplies routes lazily so queued submissions still short-circuit before registry access; monitor setup may continue eagerly obtaining its route tuple as today. Preserve its existing future-schema failure including claimed future-schema rows, legacy v1 deletion, spawned/rejected history logic and private `queued_is_terminal`/`reserved_is_terminal` stop conditions. Those flags do not mean task termination.

RED/GREEN: real source/reserved rows in both lanes; retained stopped/replacement manager owners; public reserved-route discovery (source queues miss) leaves legacy and unsupported-schema registry sentinels unchanged; private bootstrap still removes v1 and rejects future schema (including claimed rows); exact-ID distractor rows ignored; claimed residue excluded; broker failures remain failures. Add a paired registry-folding regression after source queues miss, with a real retained reservation and an older valid canonical record for its owner. A newer valid record for that same owner and service key with a noncanonical requests route must suppress the older route, leaving custody unknown. In the paired case, a newer malformed record must not displace the older valid canonical record, so the reservation remains observable. Keep other task evidence absent and generate valid records through the existing service-payload builder; corrupt only the field needed for the malformed case. These cases must fail if canonical filtering precedes latest-valid-owner folding, or if malformed rows displace valid records. Use existing manager custody/move production paths for handoff fixtures. Assert real durable rows before/after, not merely a helper return. Instrument real broker calls and fail if any source/reserved acquisition uses `peek_generator`, `peek_many`, iteration, a missing exact timestamp or a different timestamp. Permit only `peek_one(exact_timestamp=int(tid))` for those queues. Increase unrelated backlog while checking that invariant and bounded probe-call count; merely counting returned rows cannot detect an accidental scan. Registry traversal cost is explicitly separate. Do not build a benchmark framework or alter scheduling.

Done when focused submission tests pass, caller-owned broker remains usable, import checks pass and outside review confirms no mutation/global-history leak. Slice 1's public regression may remain RED until slice 3; do not claim integration ready.

### 3. Expose custody in the existing point adapters

Read first: `weft/core/task_evidence.py`, `weft/commands/tasks.py`, `weft/client/_task.py`, `weft/client/_namespaces.py`, `tests/commands/test_point_snapshot_loans.py`, `tests/commands/test_terminal_snapshot_loans.py`. Modify `weft/commands/tasks.py`, `weft/client/_task.py` and the new submission-observation tests. `weft/client/_namespaces.py` should continue delegating to its existing public point wrappers.

Add one small shared point-observation fallback in `weft/commands/tasks.py` consuming the existing task-evidence owner and shared custody leaf. This command-layer owner must share custody acquisition and the E/S/registry/R/E-prime handoff ordering across status and terminal point adapters, including the final refresh and task-evidence precedence. Sharing only exact-ID peeks while duplicating the ordering in each adapter does not satisfy this constraint. Keep the two output projections thin and distinct, preserve their existing fallback predicates, and reuse the existing task-evidence selector without a generic provider or strategy framework. Gate the status fallback with an internal keyword `include_submission_custody=False` (new parameter) propagated through `_task_status`, `_task_snapshot`, `_task_status_record` and `_known_status_record`. Only public `task_status`, `task_snapshot` and retained `Task.snapshot` opt in; no new public argument. Existing one-shot `cmd_task_ping` and `_task_control_result` calls to public `task_snapshot` intentionally inherit the richer point result; no additional implementation path is needed. Internal control and watch callers retain the false default. In `cmd_task_status`, its `watch=True` preflight uses the internal no-custody path (preserving existing `ping` handling); the non-watch branch uses public point paths. This opt-in keeps one record projection owner without leaking registry scans into loops.

For terminal snapshots, leave `_terminal_observation_snapshot`'s acquisition unchanged because interactive STOP calls it directly. Compose the fallback in `_task_terminal_snapshot` only at zero-timeout return or deadline return of an unknown snapshot. Intermediate wait iterations and interactive STOP never call custody discovery. Do not add custody unconditionally to `known_tid_evidence`: result/control consumers use it too, and this slice does not change their contracts. Reuse `TaskEvidenceSnapshot`/`terminal_snapshot_from_evidence` for the terminal projection where suitable; do not route custody through the live-PONG projection and fabricate execution metadata. Preserve existing invalid/stale-row interpretation. Apply the single ordered E/S/registry/R/E-prime rule to both point adapters; refreshing task state must also refresh the status projection, not reuse a pre-handoff base record.

RED/GREEN: matrix below through public surfaces. Positive timeout with held queued/reserved evidence stays nonterminal to the original deadline; real task-local completion wakes promptly. Release the reservation/child publication at a controlled barrier to exercise handoff; do not use sleep as proof. Read-only calls preserve sentinel queues and start no manager. Preserve existing result timeout behavior before child startup and later result availability. No global subscriptions, retry budget, polling timer or new public method.

Done when slice 1 turns GREEN, point/terminal loan and typed-failure regressions pass, and outside review verifies one shared acquisition/handoff-ordering owner with thin status and terminal projections, without broadening result/control paths.

### 4. Custody acceptance and documentation

Read first: `tests/core/test_public_client_contracts.py`, `tests/core/test_client.py`, `tests/commands/test_task_owned_observation.py`, `tests/commands/test_observer_command_failures.py`, [TS-0]. Modify these tests only to cover missing facade/error contracts; add mappings alongside the promoted paragraphs and module spec references in changed production files. Update this plan with execution/review evidence and close its index row only after both custody and slice-5 implementation gates are met. Custody can ship independently while slice 5 remains draft; its review does not authorize the liveness extension.

Verify full-TID command/CLI status delegation and rendering; do not add CLI flags or duplicate the queue matrix across facades. Verify no accepted-config dependency by using a newly constructed bare-TID handle after submitter close. Run canonical SQLite/Postgres and static gates below. If contention or a real-process failure appears under standard parallel load, stop and re-derive the causal model; do not lower concurrency or relax the assertion. Final outside review evaluates the full diff, the narrowed ownership boundary and the test evidence.

### 5. Explicit typed runtime-liveness observations (separate review gate)

Class 5 with hardening: new public API semantics, probe budgets, runtime identity
and cross-surface ownership. Review this slice and its proposed spec delta
independently before promotion or implementation; the earlier custody reviews
do not cover it. Repeat a scoped cross-family review after the raw-probe
extraction and a final review of this slice's public adapters and evidence.

Read first: [CC-3.2], [MF-5], [PY-1], [PY-2], [LIVENESS.R1]–[LIVENESS.R6];
`weft/liveness/analysis.py`, `weft/liveness/host.py`,
`weft/liveness/registry.py`, `weft/liveness/models.py`,
`weft/core/task_state.py`, `weft/core/tasks/liveness_monitor.py` (host gate and
provider loading in its probe worker), `weft/_runner_plugins.py`,
`weft/helpers/message_ids.py`, `weft/commands/_boundary.py`, and
`tests/architecture/test_liveness_boundaries.py`.
Inspect registered providers and their tests in the Docker, macOS sandbox and
Microsandbox extensions before interpreting raw `stale` as public `dead`.

Implementation ownership and intended files:

- `weft/liveness/analysis.py`, `weft/liveness/registry.py` and
  `weft/liveness/models.py`: extract one narrow broker-free raw runtime-probe
  result/owner (new symbols, names selected locally) before terminal hints.
  Preserve provider absence and failure as distinct facts. Expose a narrow
  callback-presence query from the registry (new symbol, selected locally), so
  the command can load only an absent provider without inspecting registry
  dictionaries or locks directly.
  Existing `analyze_liveness` and registry convenience functions adapt that
  result back to their current semantics, including missing-provider `unknown`
  and failure/invalid-return behavior. Reuse `inspect_host_process`; no second
  PID evaluator, framework, new provider registry or monitor-policy rewrite.
- `weft/commands/tasks.py`: one command owner for full-TID validation, strict
  task-state reads, host-scope gate, installed provider resolution, probe budget,
  final identity revalidation and typed result
  projection. Never put broker or command imports in `weft/liveness/`.
- `weft/commands/types.py`, `weft/client/_types.py`,
  `weft/client/__init__.py`, `weft/client/_namespaces.py` and
  `weft/client/_task.py`: frozen public result type, exports and two thin
  delegates. Existing snapshots remain unchanged; no CLI changes.
- Governing specs: promote only the slice-5 delta into
  `docs/specifications/01-Core_Components.md` [CC-3.2],
  `docs/specifications/05-Message_Flow_and_State.md` [MF-5], and
  `docs/specifications/14-Python_API_Surfaces.md` [PY-1], [PY-2], with backlinks
  and implementation mappings when symbols exist. The invariants are preserved,
  not rewritten to fit a new public interpretation.

Record a difference register before extraction: terminal-hint override remains
private, the public shape omits its attempted flag, missing providers remain
nonfatal, provider failures remain distinct from absence, and the new final-row
fence applies only to public runtime observation. Stop if extraction changes
monitor decisions, adds implicit probes, or needs unbounded background work.
The comprehension checks are: can a terminal task have a live runtime (yes);
can a missing provider prove death (no); does a fresh check refresh publication
age or grant cleanup authority (no).

Verification uses **new** `tests/commands/test_runtime_liveness_observation.py`,
existing `tests/liveness/test_analysis.py`, `tests/liveness/test_registry.py`,
`tests/liveness/test_host.py`, `tests/core/test_public_client_contracts.py`,
`tests/commands/test_observer_command_failures.py`, and
`tests/architecture/test_liveness_boundaries.py`. Extend existing owners only
where they prove a distinct boundary. Keep task-state rows and public adapters
real; use controlled host/provider boundaries for permission, PID reuse,
provider failure and cooperative-timeout cases. Include a harness-owned real
process for live then exited identity, with deterministic synchronization and
cleanup. Do not mock the selected evidence or public API under test.

| Contract | Firing evidence |
| --- | --- |
| Public shape and explicit invocation | Namespace and retained/fresh Task handles return the same frozen exported type. Validate full TID and finite positive non-boolean budget before probes. Ordinary status, terminal snapshots, submission, watch and control gain no additional probe invocations. Existing private callers may delegate to the extracted raw owner with identical behavior and probe count. |
| Raw evidence vs retirement policy | Terminal hint plus absent/unresolved runtime is public unknown while existing private analysis keeps its expected stale classification; terminal hint plus a live scoped runtime is public live without changing terminal task status. |
| Authority and complete state/reason coverage | Cover all three states and every reason code with independent expected literals. Host cases include live, absent, zombie, PID reuse, missing creation time, permission error and mixed dead/unresolved identities. Provider cases include live/stale/unknown, unavailable provider, callback failure and invalid return; a live wrapper cannot override provider evidence. |
| Host scope and cold provider loading | A real foreign-host row with locally absent PID or runtime yields unknown without provider loading or probing for every authority; missing hostname with host-pid also stays unknown; a missing handle on a foreign-host row yields runtime_handle_unavailable before scope checks. Start with an empty registry and load an installed first-party provider through the real entry point; use a controlled SDK boundary to prove its callback is reached. An uninstalled provider key or broken entry point raising ImportError yields probe_unavailable. Already-registered callbacks survive without factory replacement. Alias routing exposes the selected provider key even when foreign-host scoping stops the probe; non-string hostnames follow the absent-hostname rule. |
| Freshness and provenance | Check interval belongs to this invocation; selected row ID, runtime identity and provider key match real stored input, not the check clock. Old unchanged publication can still yield live. Simulated wall-clock rollback cannot change monotonic budget behavior. No liveness TTL is inferred. |
| Budget and changed evidence | No callback starts after budget exhaustion and the result is unknown/probe_timeout; one callback receives the remaining budget; late return or late exception from host/extension checks yields probe_timeout, while an in-budget exception yields probe_failed. Exercise precedence when row change coexists with timeout/failure; final I/O alone cannot expire an in-budget result. Change/remove the row at a controlled barrier during the probe: unknown with snapshot_changed, original provenance and no retry. A malformed tail appended during the probe leaves the selected valid message ID unchanged and preserves the raw result. A broker failure on final revalidation remains typed. |
| Purity and resources | No new queue writes, signals, PING, manager startup, custody/registry/global-log/Monitor reads, result consumption or subscription. Broker leases are not held across callbacks; caller-owned sessions survive success/failure. |
| Preserved private consumers | Existing liveness analysis/registry, Manager and LivenessMonitor contract tests retain their outcomes. Include the first-party provider contract tests and architecture import/deletion guards when extraction touches shared behavior. |

RED must reach a defined public assertion after adding the minimum declaration,
or demonstrate the semantic bug by exercising the extracted raw/public boundary;
an import error alone is not evidence of a correct contract test. Preserve
RED/GREEN results. Reuse the standard SQLite/Postgres, lint, type and full-suite
acceptance gates below, adding the new test file and affected extension tests.
Done only after the separate pre-implementation review, spec promotion, raw
extraction review and completed-slice review have explicit dispositions, all
reason/state contracts fire, and ordinary observers retain their current cost.

Rollout is additive and independent of custody. Document the new method and
its cooperative timeout before consumers require it. Rollback removes the new
public entry points and their exports as one change, coordinating consumers;
no stored data migration is needed. Do not roll back by relabeling unknown as
dead or routing old observers through the new probe. No progress metrics,
heartbeat, polling service, automatic PING, probe-history store or monitor
retirement deadline is part of this slice.

## Custody Acceptance Matrix and Anti-Mocking Rules

| Contract | Firing evidence |
| --- | --- |
| Both statuses and both source lanes | Real committed IDs moved through production custody paths; public status and terminal snapshot values match the table. Include automatic and explicit TID submission without a new identity scheme. |
| Task evidence wins | Terminal task-local completion and a valid live task projection each coexist with a retained reservation. Public response preserves stronger evidence. |
| Malformed/stale metadata | An invalid projection inside a valid row retains its diagnostic; a stale valid mapping retains current behavior even with custody; malformed-only task rows keep OBS.6 absence semantics and allow custody. No older row/history fallback. |
| Registry folding before canonical filtering | After source miss, an older canonical record plus a newer valid noncanonical record for the same owner/service key must not revive the old reserved route. With a newer malformed record instead, the older valid canonical record still exposes the real reservation. Both cases run without stronger task evidence. |
| Absence, isolation and exact identity | Unknown valid TID, same ID in another context, distractor body containing the target ID, and claimed residue do not become custody. Existing invalid-TID validation remains in force. |
| Read purity and errors | Source/reserved/legacy registry sentinels unchanged, no service launch, no raw-history/Monitor reads, and real closed/unavailable broker failure remains typed rather than becoming unknown. Narrow boundary instrumentation may detect forbidden calls; returned evidence is never mocked. |
| Wait and handoff | Real task-local publication wakes the existing waiter; held custody waits until the original deadline, then returns fresh nonterminal state after final backend reads; do not assert a hard elapsed-time ceiling equal to the timeout. Controlled source-to-reserved moves and child publication exercise the shared ordered pass through both public point adapters. Implementation review verifies shared ownership rather than two copies of the ordering. A reserved-to-source reverse move may produce unknown during that call; the next quiescent call must converge. Churn may remain unknown. |
| Ownership and result separation | Borrowed broker survives, owned loans close on success/failure, no acknowledgement target is created, request remains pending, pre-start result wait times out and subsequent real completion remains consumable. |
| Scope isolation | Instrument real control-surface wait, controllability, interactive STOP, Python status watch and CLI watch paths: no new custody/registry lookup and existing outputs preserved. Check explicit point opt-in separately, including the one-shot snapshots returned by ping/stop/kill; those summary snapshots are excluded from the no-lookup assertion, while command decisions remain guarded. |
| Surface and complexity | Fresh client and retained handle agree; command/CLI point path projects the same statuses. Source/reserved reads use only exact-ID peeks, with bounded call count as unrelated backlog grows; no queue-namespace scan or payload search. |

Use existing harness/environment helpers and backend parametrization. Registry and queue data must be real. Process tests should use existing process/task harnesses, with startup disabled only in the specifically pre-child fixture. Do not replace production queue moves with fabricated custody results. Consolidate overlapping tests after inspecting existing coverage; these rows are contracts, not an instruction to create one test per cell.

## Verification, Rollout and Rollback

Plan-only gates: `bin/check-doc-paths`; `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py`; `git diff --check`; mechanical verification of existing file/symbol/spec references and the plan-index count. Source the repository `.envrc` for test commands without printing its contents. No executable backstitch gate was found in this checkout; do not invent one or claim it ran.

Implementation focused gates include the new test file, `tests/commands/test_submission.py`, `tests/commands/test_submission_custody.py`, `tests/commands/test_point_snapshot_loans.py`, `tests/commands/test_terminal_snapshot_loans.py`, `tests/commands/test_task_owned_observation.py`, and `tests/commands/test_observer_command_failures.py`. Slice 5 adds the liveness/public-observation and affected extension test targets listed above. At acceptance run `./.venv/bin/python -m pytest`, slow-inclusive `./.venv/bin/python -m pytest -m ""`, and `bin/pytest-pg --all` with standard repository concurrency. Run `./.venv/bin/ruff check .`, format-check changed Python files, and `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`. Capture observed outputs; unavailable backend/tool gates are explicit blockers to integration-ready claims, not implicit passes.

Ship Weft before a consumer raises its minimum supported version. Document the additional status strings for callers with exhaustive matching. No persisted format migration or data rollback is needed. Reverting code restores unknown before child publication; consumers must tolerate that and must never resubmit on unknown alone. Verify release success with a real accepted request held queued, a reserved request, and a task-local completion through the public APIs on both supported brokers. These are release checks, not new monitoring services or counters.

Enzan follow-up belongs in Enzan: consume these public observations, confirm its existing queued/reserved branches, then reassess its unknown-retry policy against its own spec. Do not implement queue parsing or manager liveness in that consumer. Structured accepted-TID exception recovery is a separate API concern, not part of this plan. Bulk custody listing, orphan-reservation repair, new service registry retention, linearizable absence, and lifecycle/result rewrites are out of scope.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

| [CC-3.2] | Preserve legacy raw probe behavior except the approved typed result extraction. | Invalid unhashable callback results now return failed evidence instead of leaking `TypeError`. | Independent raw review F2 established a boundary bug; the promoted public contract requires invalid returns to remain unknown/probe_failed. Valid callback behavior and private retirement policy are unchanged. | No normative delta needed: this implements the promoted invalid-return contract. |

## Reviews and Execution Evidence

Author fresh-eyes pass and independent review are recorded below before plan delivery. Independent implementation reviews remain required after each slice and before completion.

### Author fresh-eyes review (2026-10-16)

Separate pass after writing the draft, checked against live owners and repository guidance. Findings and incorporated dispositions:

| ID | Finding | Disposition |
| --- | --- | --- |
| FE-1 | Adding custody to `known_tid_evidence` would also alter result/control consumers, exceeding the point-observation contract. | Accepted initially; independent F1 exposed a further shared-command leak. The corrected slice 3 now explicitly gates point-only acquisition and tests control/stop/watch isolation. |
| FE-2 | A custody projection could accidentally fabricate task start time or reuse the TID as a publication timestamp. | Accepted: exact optional-field, timestamp and acknowledgement shapes are specified in the contract and proposed PY-2 text. |
| FE-3 | Registry lookup is not constant-time and the existing helper can mutate legacy rows. | Accepted: require read-only schema reuse, sentinel preservation, and a separate R/M inspection bound instead of an O(1) claim. |
| FE-4 | Treating an invalid current projection as absent would silently weaken existing MF-5 validation. | Accepted and clarified by F4: invalid projection inside a valid row is distinct from malformed-only rows skipped under OBS.6. |

Fresh-eyes verdict: ready for independent plan review. Implementation has not begun.

Session reviewer inventory: Claude Code 2.1.293 is eligible in this session; liveness and write-attempt containment were verified earlier on this host using plan mode and Read/Grep/Glob. The earlier boundary fact-check is supporting evidence, not a substitute for this plan review. Invoke a fresh Claude review under `skills/call-agent/SKILL.md` with the complete plan and baseline; retain findings and dispositions here.

### Cross-family review, round 1 (2026-10-16)

Claude Code 2.1.293, fresh read-only invocation under the call-agent skill; 540-second bound, exit 0. Findings below are verbatim reviewer claims; dispositions follow.

<details>
<summary>Verbatim independent review</summary>

# Plan review: Public Submission Custody Observation (baseline `e78ffc41`, 0.9.113)

I read the code only. I didn't run any tests or gates, and I didn't modify the repo. The only file I wrote is the session plan note.

**Premise verified.** `_reserved_spawn_request_queue_names` calls `manager_runtime.list_manager_records(prune_stale=False, broker=…)` (`_spawn_submission.py:183`). That reaches `_snapshot_registry`, which calls `discard_v1_service_registry_rows(broker=broker)` (`manager_runtime.py:286-287`), and that function deletes v1 rows (`service_convergence.py:310-314`). So the existing helper does mutate, and it can't serve a pure read. All the named files, symbols and tests exist, and `tests/commands/test_submission_observation.py` is correctly new. The anchors STATE.1, STATE.2 and IMMUT.3 exist as bold labels (`07-System_Invariants.md:48,56,59`). The custody leaf's imports into `spawn_requests.py` (`service_convergence`, `queue_window`, `context`, `helpers`) create no cycle.

## Findings

**F1 [P1] The command owners the plan names are also used by control, stop and watch, which contradicts the plan's own boundary (FE-1).**
- Slice 3 puts the fallback "in the command owner for status and terminal snapshots" so that result/control contracts stay untouched. But the functions that own this are shared with other paths:
  - `_known_status_record`, reached through `_task_status` and `_task_status_record`, is also called by:
    - control-surface convergence (`tasks.py:1593`, inside the `_await_control_surface` loop);
    - `_require_controllable_task` (`tasks.py:1854`).
  - `_task_snapshot` is also called by `watch_task_status` (`tasks.py:1043`, exposed via `client/_namespaces.py:115`).
  - `_terminal_observation_snapshot` is also called by the interactive STOP loop (`run.py:536`).
- **Consequences:**
  - Each of these loops would pay an O(R+M) registry traversal per iteration.
  - Watch streams would start emitting `queued`/`reserved` snapshots. That contradicts "no watch expansion" and the plan's claim that event streams take on no new obligation.
  - The slice-3 done criterion ("without broadening result/control paths") can't pass as written.
- I checked the behavior: no terminal decision changes, because those loops test `terminal` or `TERMINAL_TASK_STATUSES`. So the problem is cost, an ambiguous seam, and a contradicted scope.
- **Suggested disposition:**
  - Put the fallback only in the public point entry points: `task_status`, `task_snapshot`/`Task.snapshot`, `cmd_task_status`, and `task_terminal_snapshot`/`_task_terminal_snapshot` (in the loop, after `_terminal_observation_snapshot` returns unknown).
  - Leave the two inner owners unchanged.
  - Add an acceptance row: control, interactive stop and watch behave identically.
  - Whether watch should show custody is an **owner decision**; if yes, say so in MF-5.

**F2 [P2] The refresh rule is ambiguous; ordering the reads in the direction requests move makes it simpler.**
- I verified how a request moves:
  - source → reserved is an atomic broker move;
  - the child publishes task state *before* the manager ACKs the reservation (`_advance_launch_custody`, `manager.py:2246-2261`; `_ack_launch_custody`, `2220-2227`);
  - a manager registers in `__init__` before it serves (`manager.py:732`).
- Because each stage overlaps the next, one pass in this order can't miss a request that only moves forward: **E** (task evidence) → **S** (source queues) → registry → **R** (reserved queues) → **E′**. Return E′ if it is positive, else the custody hit, else unknown.
- The plan's extra "initial miss permits one fresh task-local/custody pass" only helps with a reverse REQUEUE (`manager.py:2472`, `5246`). Even then it guarantees nothing, and it adds a second registry traversal for every truly unknown TID.
- **Suggested disposition:**
  - State the single ordered pass explicitly: registry after S, and E′ always after a custody read, hit or miss.
  - Drop the second pass, or keep it labeled "reverse-move only".
  - Define "refreshing registry discovery as needed".
  - In the MF-5 text, replace "permits" with a normative verb.

**F3 [P2] The registry reuse list is redundant and leaves the error policy ambiguous.**
- `project_manager_service_record` re-parses rows (`service_convergence.py:530`) that `collect_service_owner_records` has already parsed.
- "Fold latest canonical records per owner" isn't needed because every status is included; a set of distinct `owner_tid`s, filtered by `service_key=manager_service_key(ctx)` and the manager `service_type`, is enough. Routes come from the `QUEUE_RESERVED_SUFFIX`/`QUEUE_INTERNAL_RESERVED_SUFFIX` suffixes, as they do today (`_spawn_submission.py:193-199`).
- "Preserve schema/future-version error policy" is ambiguous:
  - Today the future-version `ValueError` comes only from `discard_v1`'s nested scan (`service_convergence.py:299-305`), which also deletes rows.
  - `collect_service_owner_records` silently ignores unsupported schemas (`223-225`).
  - Moving the private reconciler onto read-only discovery also stops its v1 cleanup, and may change the raise behavior. `submission.py:350` catches only `BrokerError`/`OSError`.
- **Suggested disposition:**
  - Remove the projection and fold steps.
  - Decide raise-or-ignore explicitly. Raising needs a scan-only extraction, which is an unlisted change to `service_convergence`.
  - Record the reconciler's behavior change.

**F4 [P2] The fallback condition isn't named, and the "malformed row" wording contradicts OBS.6.**
- The predicates in the code are:
  - status: `base is None and evidence is None` (`tasks.py:602-613`);
  - terminal: `evidence is None` (`tasks.py:406-408`).
- An "invalid projection" only exists inside a *valid* row (`_task_snapshot_reducer.py:505-513`), so it is already excluded.
- `read_task_state_snapshot` skips malformed rows (`task_state.py:61-89`, OBS.6 no-state). So custody *will* show through a malformed-only row.
- "Do not use custody to conceal a malformed current task row" is therefore either vacuous or an invitation to add an unplanned malformed-row scan.
- **Suggested disposition:** write the two predicates into the plan, and rephrase as "invalid projection inside a valid row; malformed rows keep OBS.6 meaning".

**F5 [P2] A RED test spans slice commits.** Slice 1's regression stays RED through slice 2, and each slice gets reviewed and probably committed. The full suite would be red between slices. **Suggested disposition:** use `xfail(strict=True)` and remove it in slice 3, or don't land slice 1 as its own commit.

**F6 [P2] The backlog-independence test doesn't say what it measures, so it may not catch the bug.** `peek_one(exact_timestamp=…)` returns at most one row by construction, so "spawn rows read" can't grow. **Suggested disposition:**
- Assert that spawn source and reserved queues are touched only by exact-ID `peek_one`: no `peek_generator`, `peek_many` or iteration.
- Turn "Record O(R+M)" into one sentence in the spec or plan, not an artifact. The release section rules out counters.

## Observations (non-blocking)

- **Enrichment is harmless.** `_reconciliation_diagnostic` returns `None` for nonterminal statuses (`_task_snapshot_reducer.py:610`), and `_stale_liveness_reason` only acts on `spawning`/`running` (`system.py:482`). So `submission_*` classifications survive `_enrich_known_snapshot`.
- **Reservations last until handoff ACK.** They outlive child startup until the manager ACKs, or until child reap (`manager.py:4415-4422`). That supports the plan's "task evidence wins" requirement.
- **Vocabulary differs.** The services view calls internal source-queue custody `pending` (`system.py:1054-1058`); the plan uses `queued`. This is a taste question for the owner.
- **Orphaned reservations stay unknown.** If a reserved row's manager registry record has been pruned, the request reads as unknown. This falls under the accepted "no orphan repair" limit.
- **`--ping` writes before the child exists.** With retained accepted config, it sends PING to `ctrl_in` for a TID that has no child yet. This predates the change and the plan doesn't make it worse.

## Answers

1. **Can an expert with no prior Weft context implement this confidently?** **BLOCKED.** F1 leaves the seam ambiguous and contradicts the plan's own boundary. F2–F4 make the implementer guess the read order, the registry error policy and the fallback condition.
2. **Does the plan impair correctness, robustness, architecture or performance?** Not correctness: no terminal decision changes. As written it would widen watch and control, adding per-iteration O(R+M) cost and uncommitted watch semantics (F1). Once F1–F4 are resolved, I see no impairment.

**"No new public method" holds.** Every public surface delegates to the two command entry points (`client/_task.py:42-64`; `cmd_task_status`, `tasks.py:2080-2111`).

**Limits that remain after fixes, all of which you accepted:** non-linearizable absence; unknown under reverse churn; O(R+M) registry cost; no bulk inventory or watch guarantee; pruned-owner orphans read as unknown.

</details>

| ID | Verification and disposition | Operative change |
| --- | --- | --- |
| F1 | Accepted. Verified `_await_control_surface`, `_require_controllable_task`, `watch_task_status` and interactive STOP share the named inner owners. | Slice 3 uses default-off internal point opt-in and terminal-return-only composition. CLI watch preflight stays opted out. Added scope-isolation acceptance row. This keeps existing inner projection ownership rather than duplicating a public projection implementation. |
| F2 | Accepted. Verified atomic move, registry-before-service, and publication-before-reservation-ACK ordering. A second scan cannot guarantee absence under reverse movement. | Replaced the retry with explicit E/S/registry/R/E-prime order, a single registry pass, and honest unknown under reverse churn. Updated MF-5 proposal and handoff tests. |
| F3 | Modified. Confirmed redundant parsing and bootstrap coupling. Removing latest-owner/canonical filtering would change existing routing eligibility; keep those checks. Dropping private future-schema rejection or cleanup is unnecessary scope. | Project each row once, strict broker reads, explicit public unsupported-schema policy, and exact MA-1.4 read-only exception. Private wrapper retains bootstrap before shared route discovery. Lazy route input preserves source-first short circuit. Added public/private schema tests. |
| F4 | Accepted. Verified `read_task_state_snapshot` skips malformed rows under OBS.6; an invalid nested projection is different. | Named status/terminal fallback predicates and corrected malformed-row language in contract, spec proposal and tests. |
| F5 | Accepted with no new test suppression. A RED checkpoint is not independently landable. | Slices 1-2 remain review checkpoints; land 1-3 together once GREEN. No xfail/skip workaround. |
| F6 | Accepted. Returned-row count alone cannot exclude scanning. | Spy on real source/reserved broker calls, require exact-ID `peek_one`, forbid scan APIs, and measure bounded calls as backlog grows. No permanent counters/benchmark framework. |

Out-of-scope observations: service inventory's `pending` label remains separate from the public point `queued` contract; pruned-owner reservations remain unknown; existing explicit PING behavior remains unchanged. No standing constraint reversal is proposed. The MA-1.4 amendment expressly scopes the new read-only surface while preserving existing bootstrap owners.

Round-1 verdict was BLOCKED. The operative plan changes above require a scoped round-2 review of F1-F6 before the plan is called review-ready.

### Cross-family review, round 2 (2026-10-16)

Fresh Claude review of F1-F6, 540-second bound, exit 0, verdict PASS with nonblocking clarifications. Verbatim review follows.

<details>
<summary>Verbatim scoped re-review</summary>

# Round-2 review: Public Submission Custody Observation (F1–F6), baseline `e78ffc41`

This review is read-only and based on live code at the baseline. I did not run any tests. The only file I wrote is the session plan note at `~/.claude/plans/you-are-reviewing-do-floating-mitten.md`.

**Verdict: PASS.** I recommend two P2 wording fixes. Neither blocks the plan, and neither requires reconsidering a standing constraint.

## Each fix checked against the code

- **F1 (opt-in): works, with one gap (F1-a below).**
  - These callers keep the default and so get no custody lookup:
    - `_await_control_surface` → `_task_status` (`tasks.py:1593`)
    - `_require_controllable_task` (`tasks.py:1854`)
    - `watch_task_status` → `_task_snapshot` (`tasks.py:1043`)
    - interactive STOP → `_terminal_observation_snapshot` (`run.py:536`)
  - `Task.snapshot` calls private `_task_snapshot` directly (`client/_task.py:44`), so it has to pass the flag explicitly. That is doable.
  - For a full TID, `_known_status_record` never returns `None`; it builds an unknown snapshot instead. So running the watch preflight in `cmd_task_status` without custody can't change whether `TaskNotFound` is raised.
  - On the terminal side, only `task_terminal_snapshot` and `Task.terminal_snapshot` reach `_task_terminal_snapshot`. Composing the fallback at its two return points (`tasks.py:427`, `462–463`) leaves STOP untouched.
- **F2 (single ordered pass): consistent with the code.**
  - The fallback predicate matches the code (`tasks.py:602–613`, `406–408`).
  - `stale_observer_evidence` returns something whenever a valid row exists (`task_evidence.py:1580`). So "E-prime has a valid current row" already means "evidence is not `None`". The rule needs no extra case.
  - `task_evidence` never produces `status="unknown"`. So at the `_task_terminal_snapshot` return point, checking `status=="unknown"` is the same as checking `evidence is None`.
- **F3 (registry discovery): it can be built with the current private callers.**
  - Existing `_snapshot_registry` and `list_manager_records` already do the planned steps, in the same order:
    1. project each row with `manager_service_key(ctx)`;
    2. keep the newest record per owner (`_retain_latest_included_manager_record`);
    3. apply `is_canonical_manager_record`.
  - The `requests`/`role` defaults that `normalize_manager_registry_record` adds are already set by the projection. So the new read-only path picks the same managers as today.
  - `project_manager_service_record` → `parse_service_owner_row` drops v1 and future schemas, which matches the proposed MA-1.4 exception.
  - There are two private callers:
    - `_find_reserved_spawn_request_queue` runs only after the source queues miss, so it is naturally lazy.
    - `_spawn_reconciliation_queue_specs` reads routes eagerly to set up queue subscriptions. It is unchanged and doesn't conflict.
  - The proposed new module imports (`service_convergence`, `queue_window`, `helpers`) don't create an import cycle.
- **F4 (fallback predicates and malformed rows): correct.**
  - An invalid projection inside a valid row yields stale or unknown evidence, so custody stays blocked.
  - A queue with only malformed rows reads as no row (OBS.6), so custody is allowed through.
- **F5 (RED checkpoints): consistent across slices 1–3.**
- **F6 (exact-ID reads only): sound.** `peek_one` defaults to `include_claimed=False` (SimpleBroker 8.7.2), so claimed residue is excluded. Registry reads use `peek_generator` on `weft.state.services` only, which keeps them separate from the instrumented source and reserved queues.

## New findings

**F1-a [P2] Two internal callers of the public `task_snapshot` would gain custody, and the plan doesn't name them.**
- The callers are `cmd_task_ping` (`tasks.py:2159`) and `_task_control_result`, which builds the stop/kill result snapshots (`tasks.py:2244`).
- Turning on custody for the public `task_snapshot` turns it on for these too. Meanwhile the plan says "Internal control … callers retain the false default", and the scope-isolation row doesn't list them, so an implementer would have to guess.
- In practice the effect is near zero: tasks accepted for control and tasks that can be pinged already have task-state or route evidence, so the custody fallback seldom runs.
- **Suggested disposition:** add one sentence saying both inherit public point behavior, as one-shot point reads with no new code. Then exclude them from the "no new lookup" isolation row.

**F3-a [P2] "Retains explicit `discard_v1` bootstrap, borrowing/owning the broker as today" doesn't match today's behavior when the broker is owned.**
- `_snapshot_registry` only runs `discard_v1_service_registry_rows` when a broker is borrowed (`manager_runtime.py:286–287`). With `broker=None` it skips bootstrap entirely. Tests use that path, e.g. `_spawn_reconciliation_queue_specs(context, tid)` in `test_tid_mapping_contracts.py:216` and `test_submission.py:1580`.
- An implementer could reasonably add bootstrap to the owned path, which would delete v1 rows where nothing is deleted today.
- The wrapper also currently returns `tuple(sorted(set(...)))`. The private monitor check `current_specs != queue_specs` (`_spawn_submission.py:332`) relies on that stable order.
- **Suggested disposition:** say "bootstrap only on a borrowed broker, exactly as `_snapshot_registry` does today; the private wrapper keeps its sorted, deduplicated output."

## Observations (no action needed)

- **Strict-read change on the private path is effectively nil.** With `strict=True`, a failed registry open (`BrokerError`/`OSError`/`RuntimeError`) is now raised instead of swallowed. But `discard_v1` already opens the same queue first via `peek_generator` and doesn't swallow errors.
- **Deadline overrun.** The custody read at the deadline can push the return slightly past the caller's original deadline, by one O(R+M) registry read.
- **E-prime scope.** E-prime should rerun the whole existing base/config/row/evidence block, including `config` for `_enrich_known_snapshot`. The plan's wording implies this; it isn't a defect.
- **MA-1.4 insertion point.** The draft backlink line now sits right after the anchor paragraph (it's in the working tree).

## Answers

1. **Can a strong zero-context engineer implement this without guessing at semantics?** Yes, once the two P2 sentences are added. Without them, the two unnamed callers and the owned-broker bootstrap are guesses, though either way the guess does no harm.
2. **Do the fixes hurt correctness, robustness, architecture or performance?** No.
   - Correctness: terminal decisions are unchanged, and task-local evidence still wins.
   - Robustness: the private future-schema rejection and v1 cleanup are preserved, and the public path is pure.
   - Architecture: no `manager_runtime` or command imports in the new code, and no new framework.
   - Performance: the registry walk is skipped when a source queue hits, there are no per-iteration lookups in watch/control/STOP or in intermediate waits, and source/reserved reads stay exact-ID.

</details>

| ID | Verification and disposition | Operative change |
| --- | --- | --- |
| F1-a | Accepted. `cmd_task_ping` and `_task_control_result` call public `task_snapshot` once for attached result snapshots. These are not control-decision/wait loops. | Contract, MF-5 proposal, slice 3 and scope-isolation tests explicitly allow those one-shot snapshots to inherit point custody; command outcomes stay unchanged. |
| F3-a (owned bootstrap) | Rejected as factually incorrect. `manager_runtime._registry_queue` lines 196-204 calls `discard_v1_service_registry_rows` at line 200; `_snapshot_registry` calls that helper when it owns the broker/queue. Both owned and borrowed paths bootstrap today. | Slice 2 now names both paths so the implementer preserves existing cleanup and error ownership. Do not adopt the reviewer's proposed borrowed-only cleanup. |
| F3-a (route ordering) | Accepted. `_spawn_submission.py` compares `current_specs != queue_specs` when deciding whether to replace its monitor. | Slice 2 requires the private wrapper's existing sorted, deduplicated tuple; monitor setup remains eager while exact-ID source hits remain lazy. |

The public registry sentinel test is explicitly exercised after a source miss, so it reaches reserved discovery and can catch the original cleanup side effect. A final scoped verification covers the accepted F1-a and F3-a ordering edits and defects introduced by those edits; the declined bootstrap claim is closed with the cited evidence.

### Cross-family review, round 3 (2026-10-16)

Final scoped verification of accepted F1-a and F3-a ordering: Claude, fresh read-only invocation, exit 0, PASS. Verbatim result:

<details>
<summary>Verbatim final scoped verification</summary>

**PASS.** Both accepted edits match the code at baseline `e78ffc41`, and neither edit introduces a blocking defect. I only read the code; no tests were run.

**F1-a (one-shot ping/control snapshots): verified.**
- `cmd_task_ping` calls the public `task_snapshot` exactly once (`weft/commands/tasks.py:2159`). It computes `acknowledged`/`timed_out` from the `task_ping` payload first, so the snapshot doesn't affect that decision.
- `_task_control_result` builds snapshots only after the accepted/failures split and the `ControlRejected` check are already settled (`weft/commands/tasks.py:2241-2245`). Custody in those snapshots can't change command outcomes.
- The plan states this consistently in four places: the operative contract, the MF-5 proposal, slice 3 and the scope-isolation row. That row correctly treats these summary snapshots as an exception to the no-lookup check while still guarding the command decisions.
- The plan opts public `task_status` in, so `cmd_task_status --ping` without `--watch` also gains custody (`tasks.py:2096`). That matches "non-watch branch uses public point paths", and the watch preflight stays opted out. This is consistent with the plan.
- Under `--all`/`--pattern`, the selected TIDs come from task-state inventory, so the fallback predicate (`base is None and evidence is None`) doesn't fire. There is no per-TID O(R+M) cost on bulk stop/kill.

**F3-a (route ordering): verified.**
- The private wrapper returns `tuple(sorted(set(queue_names)))` (`weft/commands/_spawn_submission.py:200`).
- The monitor topology check `current_specs != queue_specs` (`:332`) uses that order through the eager `_spawn_reconciliation_queue_specs` (`:214`).
- The exact-lookup caller `_find_reserved_spawn_request_queue` (`:168`) only runs after source queues miss. "Lazy" here means the registry call is deferred, not that the route list is unsorted, so there is no conflict with keeping the sort.
- The public path has no ordering requirement: any reserved hit produces the same `reserved` status, and queue names stay internal.

**Declined bootstrap claim:** this stays closed. The code agrees with your disposition: the owned path bootstraps through `_registry_queue` (`weft/core/manager_runtime.py:196-204`) and the borrowed path at `:286-287`. The only path that skips bootstrap is when a caller passes a `queue` explicitly, and the private wrapper never does.

**New issues:** none blocking.
- **Observation (suggested: no change):** the attached snapshot now runs after a stop/kill/ping has already succeeded, so a strict registry read failure inside it could surface as a typed error. It uses the same broker as the snapshot's existing reads, which can already fail that way, so this isn't materially worse.
- **Optional wording fix (owner decision):** slice 2 doesn't say whether the public route iterator is ordered. Since nothing depends on it, one sentence saying "public route order is unspecified" would stop an implementer from adding a sort.

No standing constraints are affected, and I have no reconsideration requests.

</details>

Disposition: no further operative change required. Public route ordering has no contract; only the private wrapper needs stable sorting, already explicit in slice 2. The optional public-order sentence is declined as redundant. Post-operation attached snapshots retain existing typed read-error behavior; command acknowledgement/acceptance decisions are unchanged, but a subsequent observation can still fail. Do not add a new suppression or retry policy for that existing boundary.

### Plan delivery evidence (2026-10-16)

The author fresh-eyes pass is complete; cross-family rounds 1-3 used Claude through the repository call-agent skill (reported model `claude-opus-5-5[1m]`). Round 1's blockers were dispositioned and the operative body changed. Rounds 2 and 3 returned PASS. The bootstrap counterclaim was checked against live code and rejected with evidence rather than applied by authority.

Verification: `bin/check-doc-paths` and `git diff --check` passed. `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py` passed with 6 tests at repository-standard concurrency. Existing referenced paths, symbols, spec identifiers and local links were checked mechanically; the absent submission-observation test is explicitly declared new. The index's 44-plan count matches the directory. The implementation suite, backend acceptance and static production-code gates have not run because this request changes only planning documents and proposal backlinks.

Guidance evaluation: the writing-plans and call-agent workflows supplied the needed scope, fresh-eyes and disposition gates; no guidance change is warranted by this plan-only work. The reusable correction is captured in the operative invariants: a non-pruning registry read can still bootstrap/mutate, and a command helper can be shared by point and loop callers. Verify actual ownership and call sites before widening observation.

Plan review is complete. Implementation remains proposed, with the metadata/index status intentionally `draft`. Future implementation must perform the slice reviews and acceptance gates above before changing that status.

### Owner-approved review amendments (2026-10-19)

Class: 5, inherited from this plan's public-contract revision. This amendment
updates the draft only; spec promotion and implementation remain in their
existing slices. The owner accepted the following findings and constraint from
the read-only review and requested their incorporation before implementation.

| ID | Disposition | Operative change |
| --- | --- | --- |
| A1 | Accepted: final registry discovery cannot promise a fixed or necessarily small timeout overrun. | Intended contract and proposed MF-5/PY-2 text distinguish terminal waiting from final backend I/O; the wait acceptance row excludes a hard elapsed-time ceiling equal to the timeout. The earlier review's word “slightly” is not a bound. |
| A2 | Accepted: latest-valid-owner folding needs an explicit ordering regression. | Slice 2 and the acceptance matrix require paired newer-noncanonical and newer-malformed records with real retained custody, including the wrong implementations each case detects. |
| A3 | Accepted: custody acquisition and handoff ordering must stay shared. | Slice 3 assigns one command-layer owner to acquisition, refresh and precedence; status and terminal adapters retain thin distinct projections. Acceptance and slice review check this boundary. |

The read-only review reproduced the queued/public-unknown gap with a real
SQLite submission after submitter closure, and its selected existing custody,
observation and error tests passed (27 tests). Those results establish the
premise and baseline only; the new regression cases remain implementation work.

Amendment verification: fresh-eyes inspection confirmed all three dispositions
change the operative plan body. `bin/check-doc-paths` and whitespace checks
passed; plan metadata and spec hygiene checks passed (6 tests). No production
code, test implementation, or governing spec text was changed by this amendment.


### Runtime-liveness extension review (2026-10-19)

Owner request: add a separately reviewed slice for typed runtime-liveness
observations with provenance and freshness; keep probing explicit and unknown
distinct from dead. This is a Class-5 extension to the draft, not authorization
to implement or promote specs. The original custody contract remains scoped to
slices 1–4; slice 5 has its own proposed spec delta and promotion baseline.

Author fresh-eyes review: kept the new operation explicit, separated raw probe
facts from terminal-hint retirement policy, kept the private attempted flag out
of the public shape, specified cooperative budgets and a final row fence,
and kept the liveness package broker-free. No new invariant reversal is proposed.

Reviewer inventory: Claude Code 2.1.293, 2026-10-19, Tier 1 cross-family.
Liveness probe exited 0 with PROBE-OK; write-attempt probe under plan mode with
Read/Grep/Glob exited 0, refused the repository write, and created only its
external session plan note. The target probe file was absent and existing
tracked/untracked repository file hashes were unchanged. Each probe was bounded
at 120 seconds. Scoped review and dispositions follow.


#### Runtime-liveness review round 1 and dispositions

Claude, read-only Tier 1, 540-second bound, exit 0. The review returned
`blocker: F1, F2`; F3 was a clarification. Verbatim review follows.

<details>
<summary>Verbatim slice-5 review</summary>

# Slice 5 review: runtime-liveness observations

**Verdict: blocker: F1, F2.** F3 is a P2 clarification. The other findings are P3 and none of them block.

I read the code at baseline `e78ffc41`: `weft/liveness/{analysis,host,models,registry}.py`, `weft/core/task_state.py`, `weft/core/tasks/liveness_monitor.py` (the probe-worker section), the three first-party provider plugins, `weft/_runner_plugins.py`, `weft/commands/tasks.py` (the point snapshot and TID validators), `weft/client/_task.py`, `tests/architecture/test_liveness_boundaries.py`, CC-3.2 and LIVENESS.R1–R9. I ran no tests and wrote no files.

## Verified claims

- **The reducer behaves as the boundary text says.** `analyze_liveness` turns a terminal hint into `stale` even when there is no handle (`analysis.py:101-107`), and also when a probe returned non-live (`:148-151`).
- **The `attempted` flag really is retirement-policy shaped.** A missing provider currently comes back as `"unknown"` with `attempted=True` (`registry.py:57-58`, then `analysis.py:139-142`). A callback that fails gives `None`, which becomes `attempted=False` (`:135-138`).
- **The task-state reader already provides the fence.** `read_task_state_snapshot(previous=...)` returns the previous row when the newest message ID is unchanged, and walks past malformed tails (`task_state.py:73-82`).
- **Leases are released between reads for free.** With `broker=None`, each call opens and closes its own `ctx.broker()` scope (`task_state.py:34-40`). Calling the reader twice therefore satisfies "release before probe" without new lease code.
- **Every named identifier exists:** `LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS`, `InvalidTID`, `CommandUsageError`, `liveness_provider_key`, `inspect_host_process`, the CC-3.2 point-in-time paragraph, and the listed tests. `tests/commands/test_runtime_liveness_observation.py` is correctly declared as new.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P1 | Slice 5 CC-3.2 delta ("Preserve host visibility/scoping requirements"), reason vocabulary, host acceptance row | **Foreign-host evidence can be reported as `dead`.** <br>• Host scoping is not done in the code the plan reuses (analysis, host, registry). It lives in the callers. `LivenessMonitor` returns unknown with reason `foreign_hostname:<h>` when the payload's `hostname` is set and differs from `socket.gethostname()`, and it does this before `analyze_liveness` runs (`liveness_monitor.py:366-378`). The same gate is repeated at `manager.py:2183` and `_spawn_submission.py:407`. <br>• Suppose a client on host B shares a Postgres broker with host A and probes a host-A task. For a `host-pid` task, `psutil.NoSuchProcess` gives `stale`, which maps to public `dead`. Docker gives "container not found locally", so `stale` and then `dead`. The macOS sandbox provider uses psutil, with the same result. <br>• This breaks the slice's central rule that unknown and dead stay distinct. <br>• The "exactly" reason vocabulary has no code for this case, and the host case list leaves it out. The vague "preserve scoping" sentence will not lead an implementer to the right check. | Name the gate in CC-3.2: a nonempty published `hostname` that differs from the local host gives `unknown` with no probe and `probe_attempted=false`, for every authority. <br>• Either add a reason code (for example `foreign_host`) or map it explicitly to `runtime_unresolved`. <br>• Add an acceptance row with a real row carrying a foreign hostname and a PID that is absent locally; the expected result is unknown, never dead. <br>• Prefer putting the check in the shared broker-free raw owner, so the monitor and the command share one copy. The monitor's outcomes stay unchanged. |
| F2 | P1 | Slice 5 CC-3.2 delta ("Provider registration remains process-local; do not start services…") | **Extension providers are never registered in an ordinary client process.** <br>• Each first-party plugin registers its probe only inside its `get_runner_plugin()` (`weft_docker/plugin.py:709-715`, `weft_macos_sandbox/plugin.py:341-349`, `weft_microsandbox/plugin.py:558-567`). <br>• `LivenessMonitor` loads the plugin through `weft._runner_plugins.get_runner_plugin(liveness_provider_key(handle))` before analyzing, and treats a load failure as not attempted (`liveness_monitor.py:383-389`). <br>• The plan never says to load the plugin. Read literally, a client that has not touched the plugin would return `probe_unavailable` for every docker, macos-sandbox and microsandbox task. That leaves both `runner` and `external-supervisor` non-functional. <br>• Tests that register a fake provider directly would still pass. | State that the command resolves the plugin exactly as the monitor does. This is entry-point import plus registration only; it starts no service or runtime. <br>• A load failure gives `probe_unavailable` with `probe_attempted=false`. <br>• Add acceptance with an empty registry plus an installed first-party provider (reachable), and with an uninstalled key (`probe_unavailable`). |
| F3 | P2 | Slice 5 "Timeout is a cooperative probe budget…" | **"Callback timeout" can't be detected the way the plan implies, and the reason precedence isn't fully stated.** <br>• No provider signals a timeout as such. Microsandbox returns `"unknown"`, Docker's client raises (so `probe_failed` today), and macOS discards the timeout argument (`plugin.py:357`). <br>• The plan names "callback timeout" as a separate cause but doesn't say that elapsed monotonic time is the only detector. It also doesn't rank `probe_timeout` against `probe_failed` or `runtime_unresolved`. <br>• It doesn't say whether a slow host-pid batch is relabeled, or whether the budget is checked between host identities. | Make it one rule, mirroring `liveness_monitor.py:401`: after the batch returns or raises, if elapsed time since entry exceeds the budget, the result is `probe_timeout`. <br>• Precedence: `snapshot_changed` > `probe_timeout` > `probe_failed` / raw outcome. <br>• The rule applies to host batches as well. Identities are not budget-checked one by one. |
| F4 | P3 | Field table, `probe_attempted` | **`probe_attempted` is ambiguous for host-pid and nearly redundant with `reason`.** <br>• `analyze_liveness` calls `inspect_host_process` even when `create_time` is `None`. That call returns before any OS lookup (`host.py:28-29`). Does it count as "a host inspection invoked"? <br>• Apart from that, `probe_attempted` follows from `reason` except for `probe_timeout`, `snapshot_changed`, and the identity-less `runtime_unresolved` case. | Define it as "an OS process lookup or extension callback actually started". <br>• Better: drop the field if no consumer needs it. The owner asked for provenance and freshness, and `reason` plus `snapshot_message_id` plus the check interval already carry them. |
| F5 | P3 | PY-2 delta: TID validation | **The accepted TID form is underspecified.** <br>• Existing surfaces strip a leading `T` (`_command_tid`, `tasks.py:2041`), and `task_terminal_snapshot` returns `status="missing"` instead of raising. <br>• `_command_tid(allow_unknown_full=True)` is not directly reusable: on non-full input it falls into short-TID resolution, which the plan forbids. | State whether `T<tid>` is accepted, and validate with `is_task_tid` after that strip rule. <br>• Note the deliberate raise-versus-`missing` difference from `terminal_snapshot`. |
| F6 | P3 | Slice 5 CC-3.2 append | **The host-pid text largely restates the existing CC-3.2 paragraph** (`01-Core_Components.md:1038-1044`). Two copies of normative text will drift. | Have the append cite the existing paragraph. Add only the public state/reason mapping, the rule that a terminal hint never gives `dead`, and the F1/F2/F3 rules. |
| F7 | P3 | Slice 5 implementation notes | **The plan doesn't name the primitives that already exist.** Without names, an implementer may hand-roll fence and lease logic. | Name `read_task_state_snapshot(..., previous=selected)` as the fence, two `broker=None` calls as the lease release, and `@typed_command_errors` as the error boundary. |

## Observations (not actionable this pass)

- **Docker statuses.** Docker's mapping (`_docker_container_liveness`, `plugin.py:772-788`) turns any status other than running into `stale`, including `created`. That becomes public `dead`. This is covered by the accepted wording that `dead` does not promise a restartable runtime can never run again, but it is worth stating in the consumer documentation.
- **Manager-supervisor handles outside Docker** key to `manager-supervisor`. No provider is registered for that key, so they always return `probe_unavailable`. This is consistent with the rule that a missing provider is unknown.
- **Budget start point.** The budget starts at method entry, so a slow initial broker acquisition can use it up and produce `probe_timeout` before any probe runs. The monitor measures from probe start instead. This is within the accepted cooperative-budget risk.

## Answers

1. **Can a strong engineer with no prior context implement this confidently?** Not yet.
   - F1 and F2 involve load-bearing behavior that lives only in the monitor, not in the modules the plan says to reuse, so following the plan faithfully reproduces both bugs.
   - F3 leaves a reason-code choice to guesswork.
   - With those three fixed, yes. The extraction boundary, the fence, the difference register and the test matrix are otherwise concrete.
2. **Does this change meaningfully degrade robustness?**
   - Existing paths: no. The slice is additive, the private reducer and monitor keep their semantics, and the liveness package stays broker-free.
   - As written, F1 would give consumers false `dead` results in multi-host Postgres deployments. That is exactly the unknown-versus-dead degradation the slice exists to prevent.
   - F2 makes the feature silently useless for extension authorities.
   - After F1 and F2 are fixed: no meaningful degradation. No standing constraint needs reversing, and I have no reconsideration request.

</details>

| ID | Disposition and verified evidence | Operative change |
| --- | --- | --- |
| F1 | Accepted. The monitor checks hostname before analysis; the host helper alone has no origin-host guard. Kept caller-specific scoping outside shared legacy analysis to preserve private behavior. | CC-3.2 proposal and slice-5 tests now prohibit probes on foreign-host evidence for every authority; host-pid with missing hostname also remains unknown. |
| F2 | Accepted. First-party factories register probes, and the monitor explicitly loads the provider. | Command owner loads the selected installed provider once only if its callback is absent, preserves existing registrations, exposes the provider key, and tests cold loading plus unavailable providers. |
| F3 | Modified. Accepted elapsed-budget precedence for both host and extension batches. The review's claim that Microsandbox returns unknown on timeout is inaccurate: `MicrosandboxRuntime.liveness` delegates through `asyncio.run` and `_liveness_async` re-raises non-not-found errors. | Use only elapsed monotonic time for probe_timeout; in-budget callback exceptions give probe_failed. Define final-read error, snapshot change, timeout and raw-result precedence. No SDK-specific timeout decoding. |
| F4 | Accepted simplification. No consumer requirement needs a public invocation boolean. | Removed public probe_attempted; reason codes distinguish unavailable, failed and timed-out observations. Private attempted semantics remain unchanged. |
| F5 | Accepted clarification. is_task_tid checks spelling, not full message-ID range. | Explicitly reject prefix/whitespace/short IDs/zero, reuse spelling and exact-ID validation, and document InvalidTID rather than a synthetic missing snapshot. |
| F6 | Accepted. CC-3.2 already owns host reduction semantics. | Cite that paragraph and specify only public mapping, scoping, loading, hints and budgets. |
| F7 | Accepted. Existing reader scopes and previous-row parameter implement the needed boundary. | Name both broker=None reader calls, previous=selected revalidation and typed_command_errors; no new lease framework. |

Provider provenance is an author clarification within the requested scope: a
provider alias can differ from runner, so the typed result includes the selected
provider key. The existing authority is still the sole runtime evidence source.
Round 2 verifies these accepted changes and new defects they introduce; earlier
custody findings remain closed.


#### Runtime-liveness review round 2 and final dispositions

Claude, fresh read-only Tier 1 invocation, 540-second bound, exit 0, PASS.
The scope was F1–F7, the provider-provenance clarification, and defects introduced
by those edits. Verbatim review follows.

<details>
<summary>Verbatim scoped re-review</summary>

**Verdict: PASS.** I checked F1–F7 and the provider-key clarification against the code at `e78ffc41` (HEAD matches). Every accepted fix is grounded in code that exists, and none of them introduces a defect that breaks correctness. I found three low-severity wording gaps that could lead an implementer astray; each needs at most one sentence or one test row. Nothing here reverses a prior decision.

### Verification of accepted fixes

- **F1 (hostname gate) — verified.** The monitor checks hostname before analysis: it requires a nonempty string that differs from `socket.gethostname()` (`weft/core/tasks/liveness_monitor.py:366-378`). The shared `inspect_host_process` has no host check (`weft/liveness/host.py:20`). Task-state rows always publish `socket.gethostname()` (`weft/core/tasks/base.py:2548`), so the real-process live-then-exited test will pass the gate on the local host. Keeping the new rule inside the command owner leaves `analyze_liveness` and the monitor unchanged.
- **F2 (cold provider loading) — verified.** All three first-party factories register their probe inside `get_runner_plugin()`:
  - `extensions/weft_docker/weft_docker/plugin.py:711-715`
  - `extensions/weft_macos_sandbox/.../plugin.py:340-350`
  - `extensions/weft_microsandbox/.../plugin.py:558-567`

  `_PLUGIN` is built at import time, and construction is plain object creation. Loading through `weft._runner_plugins.get_runner_plugin` (`_runner_plugins.py:41`) is the same path the monitor uses (`liveness_monitor.py:384-388`). The real alias case is manager-supervisor with `liveness_provider="docker"` (`weft/core/manager.py:3290`). Its key is an entry-point name, so loading the alias works and `provider ≠ runner` is a real, testable difference. Only loading when the callback is absent avoids `register_runtime_liveness_probe` replacing an existing callback (`registry.py:42-45`).
- **F3 (budget and precedence) — verified, and the causal premise holds.** The monitor already applies the "elapsed time after the probe overrides the result" rule (`liveness_monitor.py:400-403`). The new public rule is that rule plus an explicit precedence order, with no SDK-specific decoding. "Within budget, an exception gives `probe_failed`" is consistent with the registry: it turns callback exceptions and invalid returns into `None`, which is separate from an absent callback (`registry.py:57-69`). That is the absence/failure split the extraction must keep.
- **F4 (no public `attempted`) — verified.** Nothing public needs it. The private `attempted` field in `LivenessObservation` and the terminal-hint override (`analysis.py:138-151`) are explicitly kept.
- **F5 (strict full TID) — verified.**
  - `is_task_tid` rejects non-strings, whitespace, a leading `T` and short IDs (`message_ids.py:16-27`).
  - `normalize_exact_message_id` checks range, and the plan explicitly adds the zero/positive check.
  - `InvalidTID` inherits from both `WeftError` and `ValueError` (`_exceptions.py:48`), so `typed_command_errors` passes it through unchanged (`_boundary.py:20-21`).
  - The plan correctly avoids the lenient `normalize_tid`, which strips whitespace and `T` (`weft/commands/submission.py:82-91`).
- **F6 (cite existing host table) — verified.** The delta defers host reduction to the existing CC-3.2 paragraph and reuses `inspect_host_process`. It defines no second PID evaluator.
- **F7 (reader scopes and fence) — verified.**
  - `_state_broker` with `broker=None` opens and closes its own `ctx.broker()` scope per call (`task_state.py:34-40`, `context.py:172-176`), so no lease is held during the probe.
  - `previous=` is a real parameter (`task_state.py:54-90`).
  - `typed_command_errors` exists (`_boundary.py:107`).
- **Provider key — verified.** `liveness_provider_key` (`registry.py:27-33`) is the routing key and the key the monitor loads, so reporting it adds no extra probes.

### Findings (all low; suggested dispositions only)

1. **The `snapshot_changed` comparison needs to say "by message ID".** Severity: low (wording, plus one test).
   - **Code fact:** `read_task_state_snapshot(..., previous=selected)` returns the same `previous` object only when the newest row's ID matches (`task_state.py:74-75`). If a malformed row is appended after the selected row, the reader walks back past it and returns a **new** tuple with the **same** ID (`task_state.py:76-89`).
   - **Failure scenario:** an implementation that compares by object identity or by `!=` on the whole tuple reports `snapshot_changed` when only a malformed tail was added. The delta says malformed tails keep OBS.6 reader semantics, i.e. the result should not count as changed.
   - **Suggested disposition:** add to MF-5: "changed means the re-read returns `None` or a different message ID." Add a test row: appending a malformed tail during the probe still gives the raw result.
2. **"Loading failure" must cover any exception from `get_runner_plugin`, not the monitor's exception tuple.** Severity: low (wording).
   - **Code fact:** the monitor catches only `(RuntimeError, OSError)` (`liveness_monitor.py:387`). Anything else, such as an `ImportError` from `entry_point.load()` or a plugin-name mismatch error, falls through to its broad `probe_internal_error` handler (`:397-399`).
   - **Failure scenario:** a command owner that copies that tuple lets `ImportError` escape. `typed_command_errors` then turns it into `CommandExecutionError` (`_boundary.py:28`) instead of `unknown` / `probe_unavailable`.
   - **Suggested disposition:** say "any `Exception` raised while loading the provider gives `probe_unavailable`." Optionally add a test for a broken entry point. This matches the delta's intent and adds no new behavior.
3. **Two scope edge cases are unstated.** Severity: low (wording).
   - (a) **Non-string `hostname`:** the delta says "nonempty hostname different from…" and "absent/empty". A non-string value fits neither, while the monitor treats it as absent. Suggest: "a hostname that is not a nonempty string counts as absent."
   - (b) **`provider` on foreign-host extension rows:** the key can be derived without loading anything, and the field table says it is present for extension authority "including when loading fails". It does not say whether that also applies when the host gate stops the check. Suggest stating that it is populated, which matches "provenance of the selected evidence", and asserting it in the foreign-host test.

### Observations (not actionable)

- The F1 foreign-host gate makes a manager-supervisor running inside a Docker container (its hostname is the container's) show as `unknown` to an observer on the host that shares the broker, even though the Docker probe could answer from there. This matches existing monitor behavior and is conservative (never a false `dead`), so it falls under the accepted risks.
- F5 calls both helpers, but `normalize_exact_message_id` already calls `is_task_tid` for strings. The separate `is_task_tid` call earns its place only because it rejects integer input, which `normalize_exact_message_id` would accept. That is fine as specified.

</details>

| ID | Disposition | Operative change and verification |
| --- | --- | --- |
| R2-1 | Accepted message-ID comparison and malformed-tail test. The suggestion that whole-tuple inequality necessarily fails here is overbroad: separately decoded equal tuples compare equal. Object-identity comparison is the concrete risk. | MF-5 proposal explicitly compares message IDs and the acceptance row covers a malformed tail during the probe. Verified against the reader's unchanged-row fast path and malformed-tail traversal. |
| R2-2 | Accepted. Plugin loading can raise ImportError as well as the monitor's locally caught types. | The contract covers any Exception from loading as probe_unavailable, preserves BaseException, and adds broken-entry-point coverage. It does not change monitor exception policy. |
| R2-3 | Accepted both clarifications. | Non-string/empty hostname counts as absent; selected provider provenance is populated even when host scoping prevents the probe. Both are included in the host/provider acceptance row. |

The reviewer returned PASS before these low-severity wording clarifications.
Author fresh-eyes verification checked their operative edits against the live
reader, provider loader and hostname gate; they make the intended behavior
explicit without broadening the slice. Review is complete for this draft;
implementation and spec promotion remain unstarted and require their own gates.

Plan verification (2026-10-19): plan metadata and spec hygiene tests passed
(6 tests); `bin/check-doc-paths`, `git diff --check`, and a direct whitespace
check of this new plan passed. Baseline liveness/architecture and selected
first-party provider tests passed (73 tests) with the repository toolchain
and standard concurrency. Repository file hashes confirmed that this request
changed only this draft plan and added no repository files. No production code,
new test implementation or governing spec text was changed. These are planning
and baseline checks, not acceptance evidence for the proposed API.


## Implementation execution (2026-10-19)

Class 5: user authorized all five slices and their defined review gates.
The indexed plan is the execution record. Existing planning edits are preserved;
no independent commit is made for a RED checkpoint.

Comprehension checks: a returned TID proves committed acceptance only; retained
reservations can overlap execution, so task evidence wins; the private reconciler
is not a public observer because it owns history/bootstrap/wait policy. Terminal
publication can precede process exit; missing providers cannot prove death; a
fresh check grants no cleanup authority and does not refresh publication age.

Custody promotion baseline: HEAD e78ffc41befa628f70fe4bb21a947a3fe765648a plus
the promoted custody text in the three source specs. Reproduce the promotion
with `git diff e78ffc41 -- docs/specifications/03-Manager_Architecture.md
docs/specifications/05-Message_Flow_and_State.md
docs/specifications/14-Python_API_Surfaces.md`. Slice 5 remains unpromoted.

### Slice 1 checkpoint

Promoted custody text only into MA-1.4, MF-5 and PY-2. The initial public
regression covers automatic and explicit TIDs; both failed on the intended
assertion `('unknown', 'unknown') == ('queued', 'queued')` after proving the
exact request remained pending, acknowledgement targets were empty, and both
public APIs ran through a fresh client after submitter closure. The delegating
agent inspected the complete RED transcript at
`/tmp/weft-custody-slice1-red.log`; this temporary log is supporting session
evidence, not a durable retrieval cue. Metadata/spec hygiene checks passed
(6 tests), and doc paths passed. No production code changed at this checkpoint.


### Slice 1 review dispositions

<details><summary>Verbatim custody promotion review</summary>

I made no edits and ran no tests. I compared the spec diffs against `e78ffc41`, read the new test, and checked what it depends on: `submission.ensure_manager_after_submission` (called at `weft/commands/submission.py:616`), the `ManagerEnsureResult` fields, the harness (it doesn't auto-start a manager), the `echo_payload` target, and the `shared` marker.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 | `03-Manager_Architecture.md:618`, `05-Message_Flow_and_State.md:2052`, `14-Python_API_Surfaces.md:222` | **The backlinks contradict the promotion.** All three say "not yet promoted", but the custody text is now normative directly above them. MA-1.4 says it in the line right after the paragraph it calls unpromoted. A zero-context reader can't tell whether the spec governs. | Reword as "Custody contract promoted 2026-10-19; implementation pending (slices 2–3)". Keep "runtime liveness (slice 5) not promoted" separate. |
| F2 | P2 | MA-1.4 inserted paragraph | **The spec doesn't require keeping only each owner's latest record.** It says "uses only valid v2 canonical manager records", which allows an older canonical record to count even after the same owner publishes a newer valid noncanonical record. The plan (A2, slice 2) needs that older record suppressed, and its paired regression would fail an implementation that follows only the spec text. As written, the behavior is tested but not part of the contract, so the test can't be justified from the spec. | Add one clause: "the latest valid v2 record per owner for the context's manager service key, then canonical filtering; a malformed newer row does not displace a valid one". |
| F3 | P3 | `05-Message_Flow_and_State.md` ~1069–1073 | **The insertion split an existing paragraph.** "Unresolved task control routes produce a typed error before any write (CLI exit 1)" belonged with the result-acquisition sentences. It now ends the wait/scope paragraph, where it reads like part of the custody wait contract. | Put that sentence back before the new custody paragraphs, or give it its own line. Don't change the wording. |
| F4 | P3 | `tests/commands/test_submission_observation.py` | **The RED test pins only part of the promoted shape.** It checks status, `source`, `terminal`, `ack_targets` and that the request is kept. It doesn't check the other fields PY-2 now lists: `event`/classification `submission_queued`, fallback name = TID, empty metadata, `last_timestamp=0`, `observed_at=None`, and the terminal metadata classification. The handoff checklist requires a firing test for every listed contract element. | Not needed for the RED proof. Add these assertions in slice 3 (in this test or its matrix successor), or record the plan to do so in the execution log so slice 3 doesn't treat "status == queued" as complete. |
| F5 | P3 | Execution log, promotion baseline | **The recorded baseline can't be reproduced later.** It is `e78ffc41` plus an uncommitted `git diff`, and that diff will change as slices 2–4 touch the same specs. | Record a content hash of the promoted spec diff, or the exact promoted paragraphs, in the execution log. |

## How the slice-1 checks hold up

- **The RED proof fails for the right reason.** The test confirms setup before it gets to the status assertion:
  - the request was actually committed (`peek_one(exact_timestamp=int(tid)) is not None`);
  - the request is unchanged after observation;
  - the terminal snapshot is nonterminal with no ack targets;
  - the explicit TID is the one returned.

  Only after all that does it compare `(point.status, terminal.status)`. So a failure of `('unknown','unknown') == ('queued','queued')` can't come from setup, an import error, or a lost request. Both public owners run through a fresh client after the submitting client closes, and both TID modes are covered. The test also needs no import that doesn't exist yet.
- **Only the manager-start boundary is replaced.** `ensure_manager_after_submission` runs after the broker commit and after `observe_manager_availability`. The patch reaches it through the module global that `submission.py:616` calls, and returns a valid `not_needed` result. Persistence, the public API and the evidence selector are not mocked, which matches the anti-mocking rule.
- **No robustness regression.** The slice changes no production code. The spec now runs ahead of the code, which is the in-flight state CLAUDE.md allows for an approved slice, and slices 1–3 land together once GREEN. There's no xfail or skip.
- **Standing constraints.** ALT-WT-015/019/020 still hold: the promoted text rules out manager memory, global history and Monitor. IMMUT.3, STATE.1/2, OBS.6 and OBS.13 are preserved explicitly. Runtime liveness is not promoted, so the LIVENESS.R1–R6 rules are untouched. Nothing here reverses an earlier decision, so there's no RECONSIDERATION REQUESTED.
- **Python craft.** The test is idiomatic: parametrized, uses the harness, closes its context managers, and uses exact-ID peeks with no sleeps.

## Observations (not actionable)

- The test registers a TID that never launched. Harness cleanup will try to clean it up, which is harmless as far as I can see.
- Coverage for the internal spawn lane, the reserved lane and retained handles comes in slices 2–3 as planned.

**Verdict: no blocker.** A strong developer with no prior context can continue to slice 2 with confidence after F1 and F2. Both are one-line spec edits. F2 matters most, because without it slice 2's folding regression has no contract behind it. This slice doesn't make anything less robust.

</details>

F1 accepted: backlink status now reflects custody promotion and separate slice-5 pending state. F2 accepted: MA-1.4 and the proposed delta explicitly fold latest valid records before canonical filtering. F3 accepted: restored the unresolved-controls sentence before custody paragraphs. F4 accepted for slice 3: complete public shape assertions join the matrix. F5 accepted: custody-promotion diff SHA-256 is `39f7de8a06efb10f50cd4f8795a90f8d4f50cd7079dee46b590663d05c282ffc`; per-file promoted contents below identify the checkpoint independently of later diffs.

- `docs/specifications/03-Manager_Architecture.md`: SHA-256 `40b2db688b6e35025d28ca0a70831d1555d24ce6b4ed09231e59e1720622e8e2`.

- `docs/specifications/05-Message_Flow_and_State.md`: SHA-256 `4900d2ceaf2b24776cbdfbdea6231632e910bebaee74a019affb3e3557ec5ef9`.

- `docs/specifications/14-Python_API_Surfaces.md`: SHA-256 `0151b2f4633cee4e5e5638b9addc695ad5c932afe4c2e14febcb6de53c3aac20`.

Gate 1: no blocker, with dispositions applied; proceed to slice 2.


### Slice 5 contract promotion checkpoint

The independently reviewed slice-5 proposal (round 2 PASS plus recorded
clarifications) is authorized by the implementation request. Promoted its
contract into CC-3.2, MF-5 and PY-1/PY-2 without claiming implementation exists.
No liveness production/test code changed. Custody and runtime-liveness gates
remain separately owned.


### Slice 2 checkpoint

Extracted exact pending custody and read-only retained-manager route discovery
in core spawn requests, retaining explicit private bootstrap and stable sorted
private routes. Initial read-purity regression failed because private discovery
deleted the legacy sentinel. Parent inspected that assertion and both folding
sensitivity failures: early canonical filtering revived an older route, and
malformed eviction hid a valid route. Controls were restored exactly.
Submission and run tests passed (162); restored submission tests passed (76).
Focused mypy and Ruff passed. The initial public observation test remains RED
until slice 3. An owned-lease overlap found during implementation was corrected
by releasing the source scope before lazy registry traversal; the route iterator
fully releases registry resources before yielding route names.


### Slice 5 promotion review dispositions

<details><summary>Verbatim runtime-liveness promotion review</summary>

# Review: slice-5 runtime-liveness spec promotion (CC-3.2 / MF-5 / PY-1 / PY-2)

**Verdict: no blocker.** I didn't edit anything or run any tests. The promotion is a verbatim copy of the approved delta. The only difference is that the "(new)" markers in PY-2 were removed, which is correct for normative text. A zero-context developer can implement the raw/public boundary from this text. This slice changes no code, so it can't make anything less robust.

## What I checked

- **Text fidelity.** I compared every proposed slice-5 paragraph and the field table against the spec diff from `e78ffc41`. All of them appear verbatim, in the specified places:
  - after the authority paragraph in CC-3.2;
  - after the custody paragraphs in MF-5 and PY-2;
  - after the implementation mapping in PY-1.
- **Every identifier the text names exists, and does what the text assumes:**
  - `LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS` is 2.0 (`weft/_constants.py:608`).
  - `read_task_state_snapshot(..., previous=)` returns `previous` when the newest message ID matches. Otherwise it scans backward past malformed rows, so a malformed tail still resolves to the same message ID. That is the behaviour the fence relies on.
  - `_state_broker` with `broker=None` opens and releases its own `ctx.broker()` scope, so a connection isn't held across the probe.
  - `is_task_tid` and `normalize_exact_message_id` match the stated validation.
  - `typed_command_errors` re-raises any `WeftError` unchanged, so a raised `InvalidTID` stays `InvalidTID` rather than becoming `CommandUsageError`.
  - `liveness_provider_key`, `weft._runner_plugins.get_runner_plugin` and `inspect_host_process` all match.
- **Causal premises hold:**
  - Each first-party extension registers its probe only inside its own `get_runner_plugin()` (docker `plugin.py:709-715`, microsandbox `:558-567`, macOS `:341-349`). That makes the "load the plugin only if the callback is missing" rule necessary, and enough.
  - `BaseTask._build_tid_state_payload` always publishes `hostname` (`base.py:2548`). So the stricter "host-pid with no hostname gives unknown" rule doesn't disable local checks.
  - `RunnerHandle` accepts any non-empty authority string, so `authority_unsupported` can be reached with real rows.
  - Today's `attempt_runtime_liveness_from_registered_probe` returns `"unknown"` when no probe is registered and `None` when a probe fails. Splitting the raw evidence out is therefore a real separation, not a relabel.
- **Private policy is preserved.** The monitor's own hostname gate, its `(RuntimeError, OSError)` handling when a provider fails to load, the attempted/terminal-hint reducer, and the LIVENESS.R1–R6 deletion and retirement semantics are all left alone. Nothing in the text adds persistence, periodic republishing or implicit probes.
- **No standing constraint is reversed.** IMMUT.3, STATE.1/2, OBS.6, OBS.13, ALT-WT-002/015/019/020 and LIVENESS.R1–R6 are consistent with the text, so there is no RECONSIDERATION REQUESTED.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 | CC-3.2, timeout paragraph ("Do not start that phase when the budget is exhausted") | **A reachable branch has no defined reason code.** The budget starts at method entry, so the first broker read can use all of it. That is easy with a tiny but valid timeout such as `1e-9`, or a slow backend. In that case the spec forbids starting provider loading, the host batch or the callback, but never says what reason to return. The text only covers "after a batch returns or raises" and "a provider that finishes loading late". The vocabulary is declared "exactly" nine codes, and the acceptance matrix requires an independent expected literal for every branch. So the "no callback starts after budget exhaustion" row can't assert a literal, and two implementers could choose differently (`probe_timeout` or `probe_unavailable`). | Add one clause: "A phase not started because the budget is exhausted gives `unknown` / `probe_timeout`, still subject to final revalidation and `snapshot_changed` precedence." Have the matrix row assert that literal. |
| F2 | P2 | Plan execution log, "Slice 5 contract promotion checkpoint" | **The slice-5 promotion baseline wasn't recorded.** The plan says "Record a separate slice-5 promotion baseline". The checkpoint gives no diff identifier or hashes and no doc-gate evidence (`check-doc-paths`, the plan-metadata and spec-hygiene tests). The custody per-file SHA-256 values for 05 and 14 were recorded at slice 1 and no longer match the files, because the slice-5 text changed them. Nothing in the log says so. The plan body still presents both deltas as "proposed … not yet promoted", and line 873 still says "Slice 5 remains unpromoted". A zero-context reader can't tell which text is the checkpoint. | Record the slice-5 diff hash and per-file hashes for 01, 05 and 14, plus the doc-gate results. Note that the slice-1 hashes for 05 and 14 are superseded. Add one line saying both deltas are now promoted and the specs govern. |
| F3 | P3 | CC-3.2 host-scope paragraph versus "No valid runtime handle gives `runtime_handle_unavailable`" | **The order between the host gate and the missing-handle rule is unclear.** Take a valid row with a foreign `hostname` and a missing or invalid `runtime_handle`. "A public check first applies … host scope … for every authority" points to `runtime_unresolved`. But with no handle there is no authority, which points to `runtime_handle_unavailable`. The `source` and `provider` values also depend on which rule wins. | State the order: handle validity first, then host scope. That keeps "runner/runtime_id absent without a valid handle" consistent. The reverse order is also fine; the point is to pick one. Pin it with one matrix case. |
| F4 | P3 | PY-1 inserted sentence (`14-Python_API_Surfaces.md:144`) | **The PY-1 export claim has no pending marker.** "The public client exports the frozen `RuntimeLivenessSnapshot`" sits right after an implementation mapping that lists existing files, and PY-1 has no plan backlink. The "implementation pending" note exists only in the PY-2 and MF-5 footers. An agent reading only PY-1 will look for an export that doesn't exist yet. | Append the same backlink: "(slice 5 promoted 2026-10-19; implementation pending)". Add the mapping entries when the symbols land. |
| F5 | P3 | MF-5, `05-Message_Flow_and_State.md:1072-1104` | **Unrelated paragraphs now split the result-acquisition text.** The two runtime-liveness paragraphs were added to the custody block that already sits between "Without timeout, unresolved result acquisition…" and "The observation owner registers interest in its exact result…". That second paragraph's antecedent (result acquisition) is now five paragraphs above it. The plan said "append after custody", so the text is where the plan put it, but it reads badly. Round-1 F3 was the same kind of problem. | Move the custody and liveness block after the result-wait paragraphs, before "Bulk status/list/stats…", or give each block its own labelled sub-paragraph. Don't change the wording. |

## Observations (not actionable)

- Public scoping adds a fourth copy of the hostname gate, alongside the monitor (`liveness_monitor.py:366`), the Manager (`manager.py:2183`) and the spawn reconciler (`_spawn_submission.py:360`). This follows round-1's disposition: the public rule is deliberately stricter, so it stays in the command owner.
- Docker returns "container not found" as `stale`, which becomes public `dead`. That assumes the observer's Docker endpoint is the one that ran the task. The monitor already relies on the same assumption, so this slice doesn't make it worse.
- The signature literal `timeout=2.0` will drift if the constant changes. It's a trivial point, given the spec says the constant governs.
- The `check_started_at_ns` / `check_completed_at_ns` bracket doesn't say whether it covers the initial broker read. That's harmless, since neither field drives the budget.
- How often a running task republishes its state row decides how often the result is `snapshot_changed`. This is an accepted risk.

## Can a zero-context developer continue?

Yes, once F1 is resolved. F1 is the only gap that leaves a reachable branch without a contract literal, and it's a one-clause fix. Each part of the boundary has one stated owner:

- the raw owner in `weft/liveness/`, with no broker access;
- a callback-presence query on the registry;
- one command owner holding validation, scope, loading, budget and fence logic;
- thin client delegates.

The text also gives an explicit precedence order: a broker failure raises, then `snapshot_changed`, then `probe_timeout`, then a probe failure or raw evidence. Final disposition is yours.

</details>

F1 accepted: exhausted-before-start explicitly returns probe_timeout. F2 accepted: both contract deltas are promoted; the source specs now govern, and earlier proposed/unpromoted text records historical planning state only. F3 accepted: validate the handle before applying host scope, including a foreign-host/missing-handle case. F4 accepted: PY-1 has its own implementation-pending backlink. F5 accepted: result-wait paragraphs remain contiguous, followed by custody/liveness paragraphs.

Slice-5 promotion baseline: HEAD e78ffc41 plus the promoted four-spec diff, SHA-256 `49561249caa7065963cf6503f5f5effa14bf7910a5cd0cb226b92c36ac3e81bb`. This supersedes slice-1 file hashes as the active baseline (they still identify the earlier checkpoint).

- `docs/specifications/01-Core_Components.md`: SHA-256 `8725f78a1e2449663ff87d1a0de5386d0b90af715fe7b6077a65a51d16432e9c`.

- `docs/specifications/03-Manager_Architecture.md`: SHA-256 `b6e236e1d78fbf86e2cba7362fa15c51d1b2f87e46b9faa30915504c0a5ba734`.

- `docs/specifications/05-Message_Flow_and_State.md`: SHA-256 `d3363bd733c1e36a80d77b0a602fcba5a3a2d7e7b6f5358ae605717cab22f659`.

- `docs/specifications/14-Python_API_Surfaces.md`: SHA-256 `d22b2920f5b11648f4693282d5af8111d444aa1527345c7737a5e7ba3611767f`.

Separate pre-implementation gate: no blocker, all dispositions applied.


### Slice 2 review dispositions

<details><summary>Verbatim custody extraction review</summary>

**Verdict: no blocker.** A strong developer with no prior context can carry on to slice 3 with confidence. This slice doesn't weaken robustness: the private reconciler's results, bootstrap order, cleanup ownership and stop conditions are all unchanged.

I read the production diff, the five new `test_submission.py` tests, the slice-1 test, and the helpers they use: `queue_window`, `service_convergence` (parser, `project_manager_service_record`, `discard_v1_service_registry_rows`), `manager_runtime._snapshot_registry`, `list_manager_records` and `_registry_queue`. I also checked imports for cycles. I edited nothing and ran no tests.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P3 | `tests/commands/test_submission.py` `test_custody_reads_only_exact_pending_id_and_defers_registry`, `assert len(calls) <= 4` | **The call-count bound is too loose.** The expected exact-peek counts are fixed: 1 (ordinary queued), 2 (internal queued), 3 (ordinary reserved, since `T…reserved` is checked first) and 4 (internal reserved). With `<= 4`, a regression that checks a source twice, or keeps checking routes after a hit, still passes in three of the four cases. The plan wants a bounded probe count that is independent of backlog; an exact count gives that. | Assert the exact expected count for each `(lane, reserved)` case. You could also assert the `calls` order (source(s), then the route), which pins the short-circuit and the source-before-reserved rule directly. |
| F2 | P3 | Same file: route-discovery, folding and exact-ID tests | **No test has more than one owner.** Each discovery test uses a single stopped or superseded owner. The plan's "retained stopped/replacement manager owners" case (an older stopped owner whose reservation is still held while a newer active owner exists) never runs. That is exactly the case where folding per owner, deduplicating and iterating across owners matter. A bug that kept only the newest canonical owner overall would pass every current test. | Add one case with two owners: the older one stopped and holding the reservation, the newer one active. Custody should resolve to the older owner's route. If inline is too much, add it as a slice-3 matrix row. |
| F3 | P3 | Same file: folding and exact-ID tests | **Handoff fixtures use raw `move_one` with hand-written bodies, not the production custody path.** The plan says to "use existing manager custody/move production paths for handoff fixtures". The leaf only looks at IDs, so this doesn't invalidate what the tests prove now. It does leave the internal-lane reservation (`internal_reserved`) without any production-path evidence. | Accept as-is for the leaf contract. Record that slice 3's wait/handoff matrix must create reservations through the real Manager reserve path for both lanes. |
| F4 | P3 | `weft/core/spawn_requests.py` `iter_spawn_reserved_routes` loop | `manager_service_key(context)` is recomputed for every registry row. It is loop-invariant work over R rows. | Compute it once before the `with`. |
| F5 | P3 | `_spawn_submission.py::_reserved_spawn_request_queue_names` → `iter_spawn_reserved_routes(strict=True)` | **Undocumented change: the private registry read is now strict.** Before, it went through `_snapshot_registry` with the default non-strict mode. The practical effect is close to zero: `discard_v1_service_registry_rows` runs an unwrapped `peek_generator` on the same queue immediately beforehand, so open failures already propagated. Even so, it is a change in private failure behaviour that the plan implies but no difference register records. | Add a one-line entry to the slice-2 checkpoint log. No code change. |

## How the requested checks hold up

- **Lease closure**
  - **Owned source scope:** it exits before the loop over `reserved_routes` asks for its first route, and the code has a comment saying so.
  - **Public route iterator:** it finishes the registry `with` (session, queue and connection) before the first `yield`.
  - **Per-route peeks:** each opens its own scope, and the iterator only advances between scopes.
  - **Private owned path:** `context.session()` and `_registry_queue` match the old `_snapshot_registry` ownership. That includes `_registry_queue` closing the queue when bootstrap fails.
  - **Borrowed brokers:** they pass through untouched, and the tests show they stay usable after both success and failure.
  - **Abandoned iterators:** `observe_spawn_request_custody` returns without closing the iterator it was given. Neither current iterator holds resources at that point (see the closing-iterator observation below).
- **Strict reads:** the public path uses `iter_broker_queue_json_entries(strict=True)`. `parse_service_owner_row` requires the exact key set, so the indexing inside `project_manager_service_record` can't raise on malformed rows. Malformed JSON, non-object rows and unsupported schemas (v1 and v3) are skipped without counting as evidence. The failure test shows an `OSError` from either a source or a registry read propagates rather than coming back as absent.
- **No change to the private bootstrap:**
  - **Borrowed path:** it calls `discard_v1_service_registry_rows(broker=…)`, as `_snapshot_registry` did.
  - **Owned path:** it goes through `_registry_queue`.
  - **Bootstrap timing:** it is still deferred until both sources miss, because `_private_reserved_spawn_routes` is a lazy generator. That matches the old order in `_reconcile_submitted_spawn_once`.
  - **Monitor topology:** the eager `tuple(sorted(set(…)))` is kept, so the queue-spec comparison is unchanged.
  - **No new probes:** the old call used `prune_stale=False` / `probe_stale=False`, so dropping it loses no probes.
  - **Bootstrap test coverage:** `test_private_reserved_spawn_routes_preserve_bootstrap_schema_rules` covers borrowed and owned paths, claimed future-schema rows, and no partial v1 deletion before a rejection.
- **Exact-ID source short circuit:** sources are peeked with `exact_timestamp=int(tid)` only. On a source hit the function returns before the route iterator is first advanced. The `registry_scan` guard in the test proves the registry isn't touched on a queued hit, and claimed residue reads as absent.
- **Latest-valid folding:** malformed rows are dropped by projection before folding. The fold keeps the newest valid record per owner, and the canonical filter runs only afterwards. This matches the old `_snapshot_registry` then `canonical_only` order and the promoted MA-1.4 text. The paired folding test is sensitive to both mutations, and the log says both mutation failures were inspected.
- **Imports:** `spawn_requests` now imports `context`, `queue_window`, `service_convergence` and `helpers`. None of them imports `spawn_requests`, and there are no command-layer or `manager_runtime` imports. The graph is acyclic.
- **Standing constraints:** ALT-WT-015/019/020 hold: no manager memory, no global history in the shared leaf, no Monitor fallback. IMMUT.3, STATE.1/2 and OBS.6 are untouched, and the slice makes no lifecycle or task-state reads. OBS.13 and LIVENESS.R1–R6 are not reached. No earlier decision is reversed, so there's no RECONSIDERATION REQUESTED.
- **RED evidence:** the read-purity RED failed on a deleted legacy sentinel, which is a semantic assertion and not an import error. Both folding mutations failed for the intended reasons. The slice-1 public test stays RED on `('unknown','unknown')`, which is correct for this checkpoint and is not an integration-ready claim.
- **Python craft:** the frozen, slotted `SpawnRequestCustody` with a `Literal` state is fine. Taking routes as an iterable rather than as a strategy object is the minimal shape and adds no framework. Docstrings carry spec tags. Removing `_queue_contains_exact_message` and `_find_reserved_spawn_request_queue` takes duplicated acquisition logic out of the reconciler.

## Observations (not actionable)

- **Unclosed route iterators:** `observe_spawn_request_custody` doesn't close an abandoned route iterator. That's safe today, because both iterators release everything before yielding. If a future iterator held a lease across its `yield`, it would leak until garbage collection. Wrapping the iterable in `closing_queue_iterator` would make this robust, but it isn't needed now.
- **`reserved_queue` invariant:** `SpawnRequestCustody` doesn't enforce that `reserved_queue` is set exactly when the state is `reserved`. The constructor calls are the only producers, so this is acceptable.
- **Owned-mode connection cost:** each route peek opens its own session and connection, O(M) opens in total. That's the same cost as before and within the accepted O(R+M) risk.
- **Private cross-module symbol:** `manager_runtime._registry_queue` is used from `commands/`. The plan explicitly calls for this to keep the bootstrap where it lives, so it isn't a new layering break.

**Verdict: no blocker.** F1–F5 are all P3 and are the author's call. F1 and F2 are the most worthwhile, because they turn bounds that pass too easily into contracts that fail when broken.

</details>

Gate 2: no blocker. F1 accepted: pin exact contractual source/reserved probe counts in the matrix. F2 accepted: include retained old stopped owner alongside newer active owner. F3 modified: leaf fixtures remain real exact moves; slice3 handoff acceptance uses the Manager reserve path for both lanes. F4 accepted: hoist the service key. F5 accepted: private shared route reading is now strict; public errors must propagate, and existing bootstrap already raises the same queue-open errors before traversal. No private outcomes, cleanup or future-schema policy change.

### Slice 5 raw extraction checkpoint

Raw authority reduction now returns typed completed/unavailable/failed evidence;
legacy adapters preserve terminal overrides, reason strings, attempted flags and
registry return values. Existing extension-analysis tests register callbacks at
the actual registry boundary. Root inspected the semantic-control failures: raw
unresolved host evidence must remain unknown, and absent provider must remain
unavailable rather than completed unknown. Candidate bytes were restored exactly.
Liveness/architecture tests passed before and after controls (46 tests); focused
mypy and Ruff passed. No public liveness adapter exists yet.


### Slice 5 raw review dispositions

<details><summary>Verbatim raw extraction review</summary>

**Verdict: no blocker.** I read the full diff from `e78ffc41` for `weft/liveness/{models,registry,analysis}.py` and `tests/liveness/`, plus every caller of the changed symbols and `inspect_host_process`. I edited nothing and ran no tests.

**Private behaviour is unchanged.** I checked each `analyze_liveness` branch against the original:

| Case | Before | After |
|---|---|---|
| Missing provider | `unknown` / `extension_unknown` / attempted `True` | Raw `unavailable`, mapped back to `extension_unknown`, attempted `True` |
| Callback raises or returns an invalid value | `unknown` / `extension_probe_failed` / attempted `False` | Same |
| Probe completes | `extension_{x}` / attempted `True` | Same |
| Host-pid and unknown-authority branches | — | Moved verbatim |
| Missing handle and terminal-hint override | — | Untouched; terminal still resets attempted to `True` |

The registry wrappers also behave the same:
- `attempt_runtime_liveness_from_registered_probe` still returns `"unknown"` when no provider is registered and `None` on failure.
- `runtime_liveness_from_registered_probe` is unchanged.

Callers I checked: the Manager, the LivenessMonitor, the spawn reconciler, endpoints, `manager_runtime`, and the three extension test suites. They reach this code only through `analyze_liveness` or the unchanged wrappers. No test still patches the removed `analysis.attempt_runtime_liveness_from_registered_probe` name.

The `weft/liveness/` package still imports no broker or core modules, so the architecture guard holds. No implicit probes were added. Nothing reverses IMMUT.3, STATE.1/2, OBS.6, OBS.13, LIVENESS.R1–R6 or ALT-WT-002/015/019/020, so there is no RECONSIDERATION REQUESTED.

**Are the two semantic controls falsifiable?** Yes:
- **Unavailable vs completed.** The parametrized rows `None` and `"unknown"` assert different raw `outcome` values while expecting the same legacy reason. If absence were collapsed into completed `unknown`, the raw assertion fails, and the legacy assertions catch any drift in the reverse direction.
- **Unresolved host vs stale.** This test pins raw `unknown` / `host_identity_unresolved` from the same handle for which legacy analysis still returns `stale` / `terminal_hint`. That pairing documents the difference register.

Both tests now go through the real registry instead of patching the function, which is less mocking than before.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 | `analysis.py::probe_runtime_liveness`, unknown-authority branch; `RuntimeProbeResult.outcome` | **The raw result can't express "unsupported authority" in its type.** An unsupported authority comes back as `completed` / `unknown` with reason `"unknown_control_authority"`. The public owner must map this to `authority_unsupported`. To do that it has two choices: match on that private reason string, or re-dispatch on authority itself, which copies `{"host-pid", "runner", "external-supervisor"}` into `weft/commands/tasks.py`. The command already has to branch on authority (host-scope gate, loading providers only for extension authorities, `source`), so the set will exist twice. The two copies could drift, and the result would be a supported authority wrongly mapped to `runtime_unresolved`. | Before the public adapter, add one supported-authority constant in `weft/liveness`, imported by both `probe_runtime_liveness` and the command. Or add an `"unsupported"` outcome and have the legacy adapter fold it back to completed `unknown` with the same reason. Either way, the command must not match reason strings. |
| F2 | P3 | `registry.py::probe_registered_runtime`, `result not in {"live","stale","unknown"}` | **An invalid return can still escape as an exception.** If a callback returns an unhashable value (a list or dict), the set-membership check raises `TypeError` outside the `try`. The promoted contract says an invalid return gives `probe_failed`, and the public owner will rely on this raw owner for that. This code predates the slice, but the slice makes it the raw contract. | Use `isinstance(result, str) and result in (...)`, or move the check inside the guard. Add one parametrized row returning `[]`. |
| F3 | P3 | `registry.py` callback `except Exception:  # pragma: no cover` | The pragma is now wrong: `failing_probe` and the `"raises"` row run this branch deliberately. | Remove the pragma. |
| F4 | P3 | `tests/liveness/test_analysis.py`, `test_registry.py` | **Raw coverage is incomplete for what the public mapping will need.** No raw test covers the unsupported-authority result (F1) or host-pid with no scoped identities (`missing_exact_host_identity`, which must never become dead). The alias test proves routing to the provider key works. It doesn't prove the reverse: that registering only the runner name leaves `has_runtime_liveness_probe` false. That reverse case is the one that would load the wrong plugin. | Add these as rows in the existing tests. No new file is needed. |

## Observations (not actionable)

- `inspect_host_process` catches only `psutil` errors, and the host loop has no guard. An unusual `OSError` therefore propagates out of `probe_runtime_liveness`. The command owner must wrap the host batch to produce the `probe_failed` / `probe_timeout` reasons the spec requires; this belongs to the next slice.
- `RuntimeProbeResult` doesn't enforce that `unavailable` and `failed` always carry `unknown`. That's acceptable while the registry is the only producer.
- `has_runtime_liveness_probe` followed by `probe_registered_runtime` is a benign check-then-act race. The spec accepts process-local registration, and probe-time absence already yields `unavailable`.
- `RuntimeProbeResult`, `probe_runtime_liveness` and `has_runtime_liveness_probe` are not re-exported from `weft.liveness.__init__`. That's fine while the command imports the submodules directly.

**Can a zero-context developer continue?** Yes. The raw owner is broker-free, separates absence from failure, and the legacy adapter is a thin, readable fold. Fixing F1 first keeps the public adapter free of a second authority dispatch table and of string matching on reasons. This slice does not make anything less robust.

</details>

Gate: no blocker. F1 accepted: raw outcome explicitly represents unsupported authority, so public projection does not parse private reason strings. F2 accepted as a correctness correction: unhashable invalid provider returns formerly leaked TypeError outside the guard; the raw owner now treats them like other invalid returns (failed), with legacy wrappers conservatively projecting failure. This is the sole legacy edge correction and conforms to the existing invalid-return intent rather than preserving the incidental exception. F3 accepted: remove the exercised no-cover pragma. F4 accepted: add missing raw authority/empty-identity and alias reverse cases. No terminal-hint, deadline or deletion policy changes.


### Slice 3/4 custody checkpoint

Public point adapters now opt into one shared custody-and-handoff-refresh owner;
internal control/watch/interactive STOP paths retain default-off acquisition.
The fresh point row feeds the existing pure projection reducer, keeping base,
routing and evidence on one selected row. Zero-timeout and deadline terminal
returns share that same owner; intermediate waits remain task-local.
The initial accepted-submission RED cases are GREEN. The combined focused
custody/loan/error/submission and raw-liveness tests passed (179 tests), including
full shapes, both lanes, old stopped owner plus newer active owner, real Manager
reservation handoff, task-publication barriers, custom routes, malformed/stale
metadata, original wait budget and six internal scope-isolation paths.

### Public runtime-liveness RED checkpoint

The new frozen type/delegates and minimum command declaration run through a
real task-state row and registered callback. Initial public assertion failed
with unknown/runtime_handle_unavailable/none rather than the required
live/runtime_live/runner; this is a semantic failure after declaration, not an
import/setup error. The declaration is replaced by the real owner after this
checkpoint. Root inspected the complete assertion transcript.


### Slice 3/4 custody checkpoint

Public point adapters now opt into one shared custody-and-handoff-refresh owner;
internal control/watch/interactive STOP paths retain default-off acquisition.
The fresh point row feeds the existing pure projection reducer, keeping base,
routing and evidence on one selected row. Zero-timeout and deadline terminal
returns share that same owner; intermediate waits remain task-local.
The initial accepted-submission RED cases are GREEN. The combined focused
custody/loan/error/submission and raw-liveness tests passed (179 tests), including
full shapes, both lanes, old stopped owner plus newer active owner, real Manager
reservation handoff, task-publication barriers, custom routes, malformed/stale
metadata, original wait budget and six internal scope-isolation paths.

### Public runtime-liveness RED checkpoint

The new frozen type/delegates and minimum command declaration run through a
real task-state row and registered callback. Initial public assertion failed
with unknown/runtime_handle_unavailable/none rather than the required
live/runtime_live/runner; this is a semantic failure after declaration, not an
import/setup error. The declaration is replaced by the real owner after this
checkpoint. Root inspected the complete assertion transcript.

### Public adapter checkpoint and sensitivity evidence

Custody adapter review dispositions: F1 accepted with a canonical positive,
in-range full-TID guard before custody discovery; F2 accepted with explicit
custody on the point observation value; F3 accepted with typed error boundaries
for public status and retained snapshots. Regression RED: 10 failed, 3 passed
(`/tmp/weft-custody-review-red.log`); corrected combined suite: 178 passed
(`/tmp/weft-observers-review-green.log`). The first public runtime run exposed
three fixture defects (nonexistent queue deletion helper and incomplete Docker
runtime identity); corrected fixtures preserve the original assertions.

Sensitivity controls each failed as intended: early handoff refresh (4 failures),
unknown-to-dead mapping (2 failures), and disabled selected-row fence (4 failures).
The candidate was restored byte for byte, followed by 109 passing public tests
(`/tmp/weft-observers-restored.log`). Earlier joint custody/raw coverage passed
179 tests. Full mypy passed 478 files and Ruff passed before final documentation.

<details>
<summary>Independent custody adapter review, verbatim</summary>

**Verdict: no blocker.** A zero-context developer could continue from here with confidence. The slice doesn't make robustness worse, apart from F1, a narrow regression in how bad input is handled. I read the code but didn't run any tests.

## Findings

| ID | P | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 | `weft/commands/tasks.py::_submission_point_observation` → `weft/core/spawn_requests.py::observe_spawn_request_custody` (`int(tid)` → `peek_one(exact_timestamp=...)`) | **Bad TIDs now raise instead of returning `unknown`.** The custody lookup runs for any TID that reaches the fallback without its range being checked first. SimpleBroker's `normalize_message_id` raises `ValueError` for IDs ≥ 2^63−1.<br>• `client.tasks.status("9999999999999999999")` passes `is_task_tid`. It used to return an `unknown` snapshot; it now raises a raw `ValueError`, because `task_status`/`task_snapshot` have no `@typed_command_errors`. `cmd_task_status` raises `CommandUsageError` instead.<br>• `task_terminal_snapshot` only checks `isdecimal()`. So a 20+ digit input goes from `unknown` to `CommandUsageError`.<br>• A short decimal like `"123"` now probes custody (source peeks plus the registry scan). That is outside the contract's "canonical full TID" scope. | Accept. In the shared owner, return `fresh` without custody unless `is_task_tid(tid)` and `normalize_exact_message_id` accepts a positive value. Both adapters then inherit the guard. Add one public regression covering both adapters for out-of-range and non-canonical decimal input. |
| F2 | P3 | `tasks.py::_known_status_record` (set `{"submission_queued", "submission_reserved"}`) vs `_submission_point_observation` (`f"submission_{custody.state}"`) | The status projection works out that an evidence object is custody by matching classification strings. The shared owner builds those same strings with an f-string, so the two spellings are kept in sync only by convention, and mypy can't catch drift between them. | Carry the custody fact explicitly, e.g. a `custody: Literal["queued","reserved"] \| None` on `_PointObservation`, and project from that. No new framework needed. |
| F3 | P3 | `tests/commands/test_submission_observation.py` | The only new failure path this slice adds is the strict registry/reserved read. No test drives a real broker failure there through either public adapter. The matrix row "Read purity and errors" requires such failures to stay typed, not turn into `unknown`. Slice 2 tested only the leaf. | One parametrized test (status/terminal) that injects a real broker failure at the registry read. Assert typed `CommandExecutionError` on the terminal path and the status path's existing error behaviour. If the pending facade assertions already cover this, record that instead. |

## Checks that held (verified in code)

- **One shared ordering owner.** Both adapters use `_submission_point_observation` for the ordered pass: initial task-local check, source queues, registry/reserved (lazy, and only after a source miss), then a fresh task-local re-read and its projection. The re-read happens whether custody hit or missed. `fresh.base`/`fresh.evidence` win over custody. There is one pass, with no retry and no sleep.
- **Status and terminal outputs stay separate and thin.**
  - The status fallback gate is exactly `base is None and evidence is None`.
  - The terminal fallback gate on `status != "unknown"` is equivalent to "evidence is None". The only `unknown` the terminal path produces is the `observer` sentinel, and with a valid row, evidence is never `None`.
- **Projection now comes from one read.** The old code projected through `collect_known_tid_record` and read the state row separately. `_read_point_observation` now feeds a single `read_task_state_snapshot` row to `reduce_task_state_snapshot`, the same reducer and `timestamp` injection the bulk path uses. That removes a race between two reads.
- **Positive-timeout terminal waits** do custody only at the zero-timeout return or the deadline return. Middle iterations stay task-local, and `_terminal_observation_snapshot` (which interactive STOP calls directly) is unchanged.
- **Opt-in is wired correctly.** Only public `task_status`, `task_snapshot` and `Task.snapshot` opt in. `_require_controllable_task`, `_await_control_surface`, `watch_task_status` and `cmd_task_status(watch=True)` keep the default-off path; the six-surface test covers them. The ping/stop/kill one-shot snapshots inherit custody, as planned.
- **Broker loans.** The borrowed broker is threaded through. The owned source lease is released before the lazy registry iterator opens.
- **Test value.**
  - The handoff tests move the request with the real `Manager._fetch_next_message`, in both lanes, at the source-peek point. Publishing at that point covers both custody present and absent.
  - The deadline test checks custody timing against a controlled clock.
  - Malformed tails, stale rows and invalid projections are covered with real rows.
  - The RED→GREEN target was a semantic assertion (`unknown` vs `queued`), not an import failure.

## Observations (not actionable)

- The final re-read forwards `ping`. If a caller ever combined `ping=True` with an accepted payload, a task with no evidence could PING twice. No public path reaches that today: public `task_status` passes no accepted payload, and an unpublished task has no ctrl routes to ping.
- `events.py` still uses `collect_known_tid_record`. Its acquisition is unchanged, which matches the plan's no-custody rule for watches.
- The runtime-liveness stub, its imports and the `weft.ext` import ordering belong to slice 5, so I didn't assess them.

</details>

### Completed runtime slice and integrated independent review

Tier 1 Claude review completed with `no blocker` and no P1/P2 findings. The
reviewer inspected the current implementation and test contracts without running
tests or changing files. Full output follows. F1 accepted: spec mappings now
name exact owners by section. F2 retained: noncanonical identities cannot enter
custody; their extra local read preserves the existing local-evidence refresh
without adding optional return states or threading a prior observation into the
shared acquisition owner. This rare invalid-input cost is bounded and does not
add registry I/O. F3 accepted: document isolated clock seams. F4 retained: two
small parsing guards have distinct ownership (private retirement and public
observation); sharing them would export an otherwise private policy helper for
six lines of parsing. No new framework or abstraction is warranted.

The initial fast acceptance run was 5896 passed, 37 skipped, one failure:
the independently maintained public export inventory needed the approved new
value. Added only `RuntimeLivenessSnapshot` to that exact inventory. Full mypy
also identified a test module-import annotation issue; changed it to an explicit
`import weft_docker.plugin`. The corrected focused inventory/runtime run passed
120 tests; full mypy now passes all 478 files. Standard concurrency is retained.

Review observation on committing is advisory; this implementation request does
not authorize a commit. Plan closure will record verified uncommitted work only
after the remaining acceptance gates pass.

<details>
<summary>Completed runtime slice and integrated review, verbatim</summary>

**Verdict: no blocker.** I found no P1 or P2 defects in the current worktree. A strong developer with no prior context could pick this up, and the slice does not make the system less robust. I ran no tests; for test results I relied on your execution log.

## What I checked in the code

- **Shared custody ordering.** There is exactly one owner: `_submission_point_observation` (`weft/commands/tasks.py`). It reads the source queues, then lazily walks the registry and reserved routes, then always does one fresh task-local re-read (E′), with no retry. Status reaches it only when both the base row and evidence are missing. The terminal path reaches it only from the zero-timeout return or the deadline return of an `unknown` snapshot. Intermediate waits and the interactive STOP path call `_terminal_observation_snapshot` directly, so they never trigger custody. The two output views stay thin and separate. In the status path, E′ refreshes the base row, routing and evidence together from a single row (`_read_point_observation`).
- **Terminal check uses status, not evidence.** The terminal fallback tests `snapshot.status != "unknown"` rather than `evidence is None`. I confirmed these are equivalent: `stale_observer_evidence` and `_nonterminal_mapping_evidence` never produce `unknown`, so no task evidence can be downgraded.
- **Replacing the status base read is safe.** Swapping `collect_known_tid_record` for `reduce_task_state_snapshot` over `read_task_state_snapshot` gives the same result (same newest-valid-row reader rule, `include_terminal=True`). It also removes the old two-read skew between `base` and `row`.
- **Registry folding.** `iter_spawn_reserved_routes` reads strictly and projects each record once. It keeps the latest valid record per owner before applying `is_canonical_manager_record`, and skips malformed or unsupported-schema rows. It releases the registry lease before yielding any routes. The private wrapper keeps the existing `discard_v1_service_registry_rows` bootstrap on both the owned and borrowed paths, still returns `tuple(sorted(set(...)))`, and still bootstraps only after the source queues miss. This matches the old `_snapshot_registry` behaviour with `prune_stale=False`.
- **Liveness extraction.** `analyze_liveness` keeps its current behaviour for each case: `extension_unknown`/attempted for a missing provider, `extension_probe_failed`/not attempted for a failure, `unknown_control_authority` for unknown authority, and the terminal-hint override. The only change is unhashable provider returns, which is the accepted F2.
- **Public liveness command.** The handle is validated first, then host scope is checked (a non-string hostname counts as absent, and host-pid needs a hostname to scope). The provider is loaded only if not already registered, catching `Exception` but not `BaseException`. The budget is checked before loading, again before the probe, and after it. `snapshot_changed` compares message IDs and takes precedence. Both reads use `broker=None`, so no connection is held across the callback. A malformed tail still returns the row that was originally selected.
- **Error boundaries.** Wrapping `_task_snapshot` in `@typed_command_errors` is safe: the translation is idempotent for `WeftError`, and `watch_task_status` was already wrapped.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P3 | `docs/specifications/01-Core_Components.md` [CC-3.2], `03-Manager_Architecture.md` [MA-1.4], `05-Message_Flow_and_State.md` [MF-5], `14-Python_API_Surfaces.md` [PY-1/PY-2], implementation-mapping paragraphs | The same mapping paragraph is pasted into every section, so it doesn't send a reader to the right code. MA-1.4's read-only registry exception is actually implemented by `weft/core/spawn_requests.py::iter_spawn_reserved_routes`, which no spec names. MF-5 says only "`tasks.py` owns … final handoff refresh" and never names `_submission_point_observation`, the single shared-ordering owner a reviewer would need to verify. CC-3.2 carries custody text it doesn't need. Code→spec docstrings are correct; only the spec→code direction is weak. | Give each section its own owner(s): MA-1.4 → `iter_spawn_reserved_routes` (and the private bootstrap wrapper); MF-5 → `observe_spawn_request_custody` + `_submission_point_observation`; CC-3.2 → `probe_runtime_liveness`/`probe_registered_runtime` + `task_runtime_liveness`; PY-1/PY-2 → types, exports and the two delegates. |
| F2 | P3 | `weft/commands/tasks.py::_submission_point_observation`, non-canonical branch | When the TID is not canonical, this branch repeats a full task-local read (discovery and evidence) just to return it. In the status path the caller already holds that same observation, so the second round of I/O is wasted. The canonical-TID check (`is_task_tid` + `normalize_exact_message_id` + `> 0`) is also written out again in `task_runtime_liveness`. | Do the canonical check at the call sites, or return `None` so the caller keeps its existing observation. Optionally put the check in one small private predicate. This removes work rather than adding structure. |
| F3 | P3 | `weft/commands/tasks.py` imports (`from time import monotonic as _runtime_liveness_monotonic`, `time_ns as …`) | These aliases sit next to `import time` and exist only so tests can patch the liveness clocks without affecting the broker or harness clocks. That's a reasonable choice, but nothing says so. | Accept, and add a one-line comment saying they are per-observation clock seams, so nobody later "dedupes" them. |
| F4 | P3 | `weft/commands/tasks.py::_runtime_liveness_from_row` | It repeats the handle parsing in `weft/liveness/analysis.py::_handle_from_snapshot` (Mapping check, `from_dict`, catching `TypeError`/`ValueError`). | Optional: expose that parser from `weft/liveness/` and reuse it, or accept the duplication. |

## Observations (not actionable)

- **Second PING on refresh.** E′ passes `ping=ping` through. So `task_status(..., ping=True)` could send a second keyed PING and wait a second probe timeout, but only if route config is discovered while there is no row and no evidence. I couldn't find a realistic way to reach that: a ctrl_out envelope with a taskspec would itself count as terminal evidence. If you want to rule it out entirely, use `ping=False` on the refresh. It doesn't need to be fixed now.
- **Specs claim more than the plan index yet.** All four specs say "implemented 2026-10-19", but the plan index row and the plan's `Status:` are still `draft`, and the work is uncommitted. Before claiming completion, flip both to `completed` and commit. If the full or Postgres runs fail, the spec wording overclaims.
- **Private reconciler now reads strictly.** It used to iterate the registry non-strictly; now it raises on a broker read failure mid-traversal. This was the accepted Gate 2 F5, and I'm not reopening it.
- **Test value.** The exact-ID probe test pins `peek_one(exact_timestamp=int(tid))` for each queue, its call counts, and the absence of registry access when the source queue hits. The folding test covers both the noncanonical and the malformed-newer cases. The scope-isolation test covers six internal paths. The host-scope test proves no plugin load and no callback for blocked scope. All of these would fail under the mutations they target, which fits your sensitivity-control evidence. The backlog in the probe test is a fixed 40 rows rather than growing, but the exact-timestamp assertion is what catches a scan, so that's enough.

</details>

### All-root acceptance counterexample and Django fixture correction

The standard 12-worker slow-inclusive command `python -m pytest tests extensions
integrations -m ""` completed: 6275 passed, 53 skipped, two failures in Django
HTTP/SSE tests (`/tmp/weft-api-allroots.log`). This is not classified pre-existing.
The original helper `_wait_for_snapshot` returned on any non-unknown point value.
New queued custody therefore satisfied its implicit metadata-readiness premise;
the detail name remained the TID fallback, and the task-owned stream correctly
ended unknown before its metadata existed. Governing [PY-2], [MF-5], and
`13C-Using_Weft_With_Django.md` [DJ-11.1] keep point custody separate from task
publication and stream following. No contention hypothesis or load reduction
was used.

Renamed the helper `_wait_for_published_task_snapshot` and excluded unknown,
queued and reserved from its readiness condition. All task-name, state, stdout,
stderr, result-retention and end-event assertions remain unchanged. Clarified
the inherited point-custody behavior in [DJ-11.1]. This changes the setup to wait
for the evidence the existing assertions require, not the behavior being tested.
Source comment for integrated review F3 now documents isolated observation clocks.

### Django compatibility follow-up independent review

Tier-1 Claude verified the original counterexample, unchanged stream owner and
unchanged assertions; verdict `no blocker`. F1 retained the explicit statuses:
they are the approved finite observation contract, and this correction does not
introduce a broader timestamp-as-publication contract. F2 accepted for the next
stable source checkpoint: make timeout diagnostics name published metadata.
F3 retained core public-surface coverage because the Django view is a direct
pass-through and has no custody branching of its own. The unchanged integration
assertions continue to protect real stream output after publication.

<details>
<summary>Django compatibility follow-up review, verbatim</summary>

**Verdict: no blocker.**

This is a setup fix that matches the contract. It doesn't hide a stream regression. A zero-context developer can pick this up with confidence, and the slice doesn't make anything less robust. I didn't run any tests.

## What I checked

- **The failures match the claimed cause.** The log at `/tmp/weft-api-allroots.log` shows exactly two failures:
  - `test_http_detail_and_sse_views_are_read_only_diagnostics` failed at `:1601` because `name` was the fallback TID, `'1792422253770829824'`, not `'testapp.echo_task'`. That is the custody-only `TaskSnapshot` shape PY-2 defines.
  - `test_sse_stream_emits_stdout_and_stderr_events_without_consuming_result` failed at `:1746`. The stream emitted `snapshot` and then `end {"status": "unknown"}` about 31 ms later, with no `stdout`.

  Both are what you'd expect if the old helper returned on `queued` before the task published its metadata.
- **The stream code is unchanged.** `git diff` shows no change to `weft/commands/events.py`. `iter_task_realtime_events` builds its snapshot through `_task_snapshot_event`, which uses `collect_known_tid_record` plus task-local terminal evidence. It never touches custody. So the early `end unknown` before publication is behaviour that already existed at HEAD. The old `!= "unknown"` helper used to mask it, and this slice doesn't change it. That fits the MF-5 rule that streams and watches gain no custody lookups.
- **The point path is the only thing that changed.** `Task.snapshot` (`weft/client/_task.py:48`) now passes `include_submission_custody=True`. The Django detail view's `_task_snapshot_or_404` delegates to it, so detail returning `queued` with a TID name is the intended PY-2/DJ-11.1 behaviour.
- **The assertions are untouched.** The diff only renames the helper, narrows its readiness check and adds a docstring. Every assertion is the same: task name, `event: snapshot/state/result/stdout/stderr/end`, the output text, the result value or status, the 403 checks and the transport 404. If the stream really regressed after publication, these tests would still fail.
- **The old helper had no other users.** `_wait_for_snapshot` doesn't appear anywhere else. The other `status == "unknown"` assertions (`:694`, `:696`, `:1382`, `:1478`) are separate contracts and the change doesn't affect them.
- **The DJ-11.1 clarification is consistent.** It doesn't redefine anything: detail inherits custody from PY-2, snapshot availability is not readiness, and streams get no new wait or probe. Pointing the spec at the test file follows an existing pattern in 13C (lines 688, 789, 1163 and 1276 do the same). It also links back to the plan.

## Findings

| ID | Severity | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P3 | `integrations/weft_django/tests/test_weft_django.py:899` | The readiness check is a denylist: `status not in {"unknown", "queued", "reserved"}`. If another pre-publication status is added later, this mask comes back silently. PY-2 already defines a positive marker: custody-only snapshots have `last_timestamp=0` and the TID as their fallback name. Checking `snapshot.last_timestamp` (non-zero means a task row was published) would say "published metadata" directly, with no list of statuses to keep up to date. | Optional. Either form is correct today. |
| F2 | P3 | same helper, its `AssertionError` message | The message still says "waiting for snapshot". After the rename it should say "waiting for published task snapshot", so a timeout doesn't send whoever triages it looking for a missing snapshot. | Optional wording fix. |
| F3 | P3 | `integrations/weft_django/tests/` | No Django-level test exercises the new DJ-11.1 sentence that detail returns `queued`/`reserved` with a TID fallback name. Core PY-2 tests cover the behaviour, and the Django view is a thin pass-through. A Django test would need the manager-start suppression fixture. | Author's call. Either accept that core coverage is enough because the view only delegates, or add one detail-view test using the existing pre-child setup. Not required for this checkpoint. |

## Observations (not actionable)

- If you call the SSE stream right after enqueue, before task publication, it currently ends at once with `end unknown`. That already happened at HEAD and this slice doesn't make it worse: the stream doesn't consult custody, as MF-5 requires. Product UIs that subscribe straight after submitting will see this. Whether streams should keep following through a custody window is a separate contract question, outside this plan.
- On the requirement that every enumerable element has a firing test: the custody status values do have firing tests at the core public surfaces. The Django layer only inherits them.

## Answers to the checkpoint questions

- **Is this a contract-correct setup change?** Yes. The helper's job was always to wait for the evidence the later assertions need, which is published task metadata. Custody broke the old proxy, "not unknown", and the new condition restores what the helper meant without loosening any assertion.
- **Is it hiding a stream regression?** No. `events.py` is unchanged, the stream evidence path is still custody-free, and the stdout, result and end assertions still fire after publication.
- **Can a zero-context developer continue?** Yes. The rename, the docstring, the DJ-11.1 sentence and the plan log all say the same thing about the cause and the fix.

</details>

### Full backend acceptance evidence

Slow-inclusive SQLite across all CI roots (`python -m pytest tests extensions
integrations -m ""`): **6277 passed, 53 skipped**, 252.94 seconds, standard
12-worker concurrency. Log: `/tmp/weft-api-allroots-final.log`.
PostgreSQL across the same roots (`bin/pytest-pg --all tests extensions
integrations`): **6228 passed, 31 skipped**, 373.79 seconds, standard concurrency.
Log: `/tmp/weft-api-postgres.log`. The wrapper owns backend startup and cleanup.
Skipped gates are declared backend-specific or opt-in live-provider/Microsandbox
integration tests; no new skips or weakened expectations were added.

Final static verification: Ruff clean, all 18 changed Python files formatted,
full mypy clean (478 source files). `bin/check-doc-paths` and `git diff --check`
pass. Follow-up review F2 timeout wording now names published task snapshots;
the complete Django suite has also passed (148 passed, one SQLite-run skip).
The final default fast acceptance run is in progress before plan closure.

### Completion and handoff (2026-10-19)

Final default acceptance (`./.venv/bin/python -m pytest`): **5897 passed,
37 skipped**, 224.26 seconds, standard concurrency. Log:
`/tmp/weft-api-fast-final.log`. Both full backend runs and all static gates above
passed on this implementation. All delegated work was received, verified and
integrated. All required independent checkpoints, including the separate
runtime-liveness contract/raw/public reviews and final integrated review, have
no remaining blockers. The Django setup correction received an additional
cross-family review.

All five implementation slices are complete. The plan and its index row close
in this change. Source specs, implementation mappings, README usage and
Unreleased migration notes are synchronized. No TaskSpec lifecycle states,
bulk/watch semantics, result ownership or monitor retirement rules changed.
Runtime observations are explicit, read-only, cooperative-budget observations;
unknown is distinct from dead and a changed selected row invalidates the probe.

Work remains uncommitted for owner review. No consumer repository was changed,
no release was published, and no commit or push was performed.

Post-closure documentation verification: plan metadata and spec hygiene **6 passed**; `bin/check-doc-paths` and `git diff --check` passed.
