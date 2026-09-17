# Manager Discovery and Durable Submission

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-3], [MF-6], [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.8]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1]; docs/specifications/14-Python_API_Surfaces.md [PY-2], [PY-3]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.4], [DJ-9.1]
Superseded by: none

Class: 5, hardening required. Manager recovery, post-enqueue custody, and public submission success semantics change across core, commands, CLI, client, and Django adapters. No process-policy modifier.

## Goal

Prevent a short failed manager proof from immediately authorizing a competing manager, and prevent manager-readiness failure from cancelling already accepted work. Retain bounded recovery for truly absent or unresponsive managers, exact TID custody, and existing work-stealing/convergence. This document plans implementation; it does not authorize deployment or make its proposed spec text normative.

Implementation state: behavior and documentation are implemented, verified, independently reviewed, and committed under the repository completion gate.

Outcome checklist:

- [x] Discovery distinguishes ready / policy-absent / uncertain, retaining the existing 300-second unknown-row cutoff without calling expiry proof of death.
- [x] Automatic helper startup requires a caller-owned observation interval and fresh final evidence.
- [x] Probe failures retain their reason; final matching reads cannot discard valid proof.
- [x] Successful enqueue returns an accepted TID despite readiness/startup failure; no automatic rollback deletion.
- [x] CLI, Python, and Django agree on acceptance versus execution completion.
- [x] Real broker/process regressions, migration notes, and reciprocal spec/code traceability cover the changed contracts.

## Source Documents and Spec Baseline

Plan type: implementation with spec revision. Baseline: `eff8eb408fdb8ae2c5a2705f8f72c015973dc88d` for all source specs and code below. Promotion baseline: the six source-spec changes are the diff from `eff8eb408fdb8ae2c5a2705f8f72c015973dc88d`; they were applied and reviewed before the runtime slices, then promoted with the implementation completion commit.

Required sources:

- [Manager architecture](../specifications/03-Manager_Architecture.md), [MA-1] item 4 and [MA-3]: selection, bootstrap, convergence.
- [Message flow](../specifications/05-Message_Flow_and_State.md), [MF-1], [MF-3], [MF-6], [MF-7]: enqueue, keyed replies, reconciliation, bootstrap.
- [Invariants](../specifications/07-System_Invariants.md), [MANAGER.1] through [MANAGER.8a], plus queue/state/liveness invariants.
- [CLI](../specifications/10-CLI_Interface.md), [CLI-1.1.1]; [Python API](../specifications/14-Python_API_Surfaces.md), [PY-2], [PY-3].
- [Django integration](../specifications/13C-Using_Weft_With_Django.md), [DJ-8.4], [DJ-9.1]: captured preparation and transaction hooks. This is the integration contract; do not adopt its whole document as a new core contract.
- [Architecture](../specifications/00-Overview_and_Architecture.md); [agent entry](../../AGENTS.md); [decision hierarchy](../agent-context/decision-hierarchy.md); [engineering principles](../agent-context/engineering-principles.md).
- Runbooks: [writing plans](../agent-context/runbooks/writing-plans.md), [hardening](../agent-context/runbooks/hardening-plans.md), [review](../agent-context/runbooks/review-loops-and-agent-bootstrap.md), [runtime custody](../agent-context/runbooks/runtime-and-context-patterns.md), [testing](../agent-context/runbooks/testing-patterns.md), [adversarial acceptance](../agent-context/runbooks/adversarial-acceptance-probes.md).

## Incident Evidence and Limits

Read-only inspection on 2026-09-17 found deployed Weft 0.9.100 attempting detached startup from a container while host manager T1789606530160398336 remained dispatch-capable. A valid PONG remained at broker timestamp 1789647224241258496 (12:13:44.241258 UTC); replacement invocation T1789647224319049728 followed. The launcher failed opening its stderr file on a read-only filesystem before creating the manager runtime. The incumbent spawned children in the same second.

The failed caller's request ID, deadline, probe outcome, and registry snapshot were not retained. This does NOT prove a timeout rather than broker error, link the retained PONG to that caller, or establish which discovery branch authorized startup. Regressions must prove the code-level failure modes below, not pretend to reproduce unknown historical timing. No production replay, receipt repair, or governance change belongs in this plan.

## Context and Key Files

| Owner / files | Current behavior and required change |
| --- | --- |
| `weft/core/manager_runtime.py` | Registry disposition can omit old unknown rows; active selection requires runtime/PONG proof. Namespace fallback measures registry age, not observation duration. `ensure_manager` returns a tuple, including an uncertain record as if reusable. Add explicit recovery evidence/results and bounded automatic-start decision here. Keep explicit `start_manager` and foreground replacement semantics separate. |
| `weft/core/control_probe.py`, `weft/_constants.py` | Shared keyed probe owns reply reads/deletion; final timeout sweep deletes without accepting valid proof. Manager probe budget borrows 0.5-second competing-startup grace. Correct the final read and reuse the existing control-surface timeout and namespace-grace constants under their distinct owners. |
| `weft/commands/submission.py`, `weft/commands/_spawn_submission.py` | Queue-first write, then ensure; reconciliation recognizes spawned/rejected/queued/reserved/unknown. Queued startup failures may delete the request. Retain accepted work; reuse reconciliation and preserve TID in rejection diagnostics. |
| `weft/commands/run.py`, `weft/commands/manager.py`, `weft/core/heartbeat.py`, `weft/core/monitor/task_monitor.py` | Consumers of ensure results: run owns wait and started-here cleanup, manager commands promise readiness, heartbeat ignores the result, and TaskMonitor reaches heartbeat ensure indirectly through `upsert_heartbeat()` during `_ensure_heartbeat_registered()` at the start of a service turn. Migrate all direct and indirect callers without conflating accepted submission and successful explicit manager start. |
| `weft/client/_prepared.py`, `weft/client/_task.py`, `weft/commands/types.py`, `weft/_exceptions.py`, `weft/cli/run.py` | Preserve Task/TID and existing receipt/output shapes; document changed acceptance semantics. Include the accepted TID in existing rejection error messages; do not add a public exception attribute or change constructors. No new CLI flag. |
| `integrations/weft_django/weft_django/client.py`, `integrations/weft_django/tests/test_weft_django.py` | Prepared submissions bind deferred TIDs after commit. Verify readiness failure no longer aborts independent callbacks. Do not change Django exception policy for validation, broker-write failure, or task rejection. |
| `tests/core/test_control_probe.py`, `tests/commands/test_manager_commands.py`, `tests/commands/test_submission.py`, `tests/commands/test_run.py`, `tests/commands/test_run_public.py`, `tests/system/test_run_diagnostics.py` | Existing probe, bootstrap, rollback, and public contracts. Replace obsolete rollback expectations only after proving old failure and preserving rejection/unknown-write distinctions. |

Read `weft/core/service_convergence.py`, `weft/core/queue_wait.py`, `weft/core/spawn_requests.py`, `weft/manager_detached_launcher.py`, and `tests/helpers/weft_harness.py` before editing their callers. No wholesale extraction is requested. Before editing, answer: Who owns an exact request after reservation? Why does an accepted TID not prove execution? Which callers require a ready manager rather than merely accepted work? Can the registry iterator hold a transaction while waiting for a PONG? Close it before waits; a session must not become a long transaction.

## Invariants, Constraints, and Hidden Couplings

- Keep `TaskSpec -> Manager -> Consumer -> TaskRunner`, queue names/envelopes, TID assignment by broker message ID, immutable spec/io, forward-only execution states, and spawn-based processes.
- Atomic reservation owns dispatch; lowest-live-TID convergence remains advisory. No distributed election, launch lock, lease, new registry format, health cache, or background supervisor.
- Reader policy must not delete registry/mapping rows. New recovery uses caller-owned monotonic state only. Runtime state queues remain excluded from export/import.
- Keep scoped PID identity and exact keyed, dispatch-eligible PONG validation. A stopping/draining/superseded manager is not eligible. Unknown evidence must not authorize voluntary leadership yield.
- Core must not import commands/client/CLI. Reconciliation remains in its existing command owner; observe exact-TID progress in commands between shared core discovery phases; do not inject a submission predicate into core.
- Read append-only histories with existing generators, close iterators before waits/session exit, and avoid waits under a broker transaction. Preserve thread ownership and borrowed broker lifetime.
- A successful broker write proves historical acceptance even if later inspection fails or retention removes evidence. It does not prove current queue location, execution, or exactly-once effects. An ambiguous failed write is not promoted to acceptance.
- No automatic post-acceptance deletion/requeue on readiness failure, unknown location, caller interruption, or wait timeout. Existing explicit cancellation and manager reserved-policy owners remain unchanged; this plan does not promise new cancellation support for unclaimed requests.
- Existing CLI wait timeout, task failure, and Ctrl-C semantics remain. No new finite default wait timeout is introduced; callers choosing an unbounded wait still can wait indefinitely during an outage.
- Direct `weft manager start` must still fail if readiness cannot be established. Only a caller with confirmed enqueue acceptance may return an accepted receipt despite readiness failure.
- Catch expected availability/bootstrap failures only at the post-acceptance boundary. Do not swallow programming errors, validation errors, `KeyboardInterrupt`, or `SystemExit`. If an unexpected exception escapes after enqueue, preserve work and include the known TID in diagnostics.

## Rollout, Compatibility, and Rollback

Ship core detection/probe changes first or together with the acceptance change; do not deploy specs alone as a claim of shipped behavior. All adapters in the distribution must be tested against the same revised acceptance contract. No storage migration or payload-version change is needed; existing managers can consume new callers' accepted requests. Old callers still retain old rollback behavior, so mixed versions do not constitute completion of the rollout.

This intentionally changes behavior: Python submit and CLI `--no-wait` can succeed while no manager is ready. Success means confirmed broker acceptance; readiness degradation is diagnostic, not task completion. Release notes and README examples must state this. Explicit manager-start commands retain readiness success semantics. Waiting callers retain existing timeout/result behavior. No opt-in deployment mode is introduced. This submission-success semantic change is a public API behavior change; review/approval of this plan must precede implementation and spec promotion under the repo ask-first boundary. Plan-authoring does not constitute that approval. No new `SubmissionManagerError.tid` attribute is proposed.

Proposed accepted recovery risk: a healthy but busy/admission-limited incumbent can leave this caller's new request unreserved through both proof rounds. Pending backlog plus missed proof can therefore launch a helper even without a stalled manager, increasing transient process/connection pressure. The revised timing reduces premature starts; it does not eliminate this incident shape. Atomic reservation and existing convergence bound duplicate dispatch and manager overlap, not resource usage or exactly-once task effects. This tradeoff requires owner acceptance with the plan before implementation; no launch fencing or admission redesign is added.

Rollback is code-only for future submissions; it must not delete requests accepted by the newer client. Before rollback, inventory affected exact TIDs and drain/reconcile them through existing queue/task tools. Do not repeat a submission just because an old client would have raised. Executed effects and accepted work cannot be undone by package downgrade. Helper managers may briefly overlap; existing convergence/reservation remains the safeguard. Deploy or rollback is an operator action outside plan-authoring scope.

## Proposed Spec Delta

Promotion strategy: A, in-file requirement text before new implementation-link claims, for every file below. Apply these deltas in the spec-promotion slice; replace contradictory baseline sentences rather than appending a competing contract. Keep existing mappings accurate and add new owner links with implementing code. No file reclassification or new spec file.

### Manager detection: 03-Manager_Architecture.md [MA-1] item 4 and [MA-3]; 07-System_Invariants.md [MANAGER.8]

Replace the automatic-start namespace-ambiguity grace clauses with:

> Automatic recovery follows this sequence:
>
> 1. Reduce latest canonical registry records, then apply the existing 300-second unknown-record age cutoff unchanged. Positive scoped-runtime proof remains authoritative; as today, an unknown record older than the cutoff no longer suppresses automatic startup. Expiry is a bounded recovery policy, not proof that a process died. Registry read failure is uncertainty, not an empty registry.
> 2. On a fresh uncertain incumbent, record caller-local monotonic time and pending public work before one keyed proof attempt. Reuse any positively proved dispatch-eligible manager.
> 3. If still uncertain and work was and remains pending, wait only the remainder of the namespace-ambiguity grace measured from that first observation. The first probe's elapsed time counts. Attempt keyed proof once more against fresh registry evidence, then decide; do not restart the sequence or add intermediate probes. Changed unproved incumbent identity returns uncertainty without launch in this attempt.
> 4. Fresh proof wins; empty backlog or failed final reads suppress helper startup. An expired/absent incumbent allows ordinary startup after a successful fresh registry check. A still-unproved same incumbent plus pending work at both observations allows a helper after the grace. Presence is not proof of stalled dispatch: a busy healthy manager may still qualify. Existing atomic reservation and manager convergence remain authoritative.
> 5. Submission callers also reconcile their exact TID before authorizing startup. Reserved/spawned/rejected work does not authorize another manager for that submission; unknown location preserves acceptance but does not authorize launch. Core discovery does not receive a submission predicate.

### Probe: 05-Message_Flow_and_State.md [MF-3]; 03-Manager_Architecture.md [MA-1] item 4

> The two automatic-recovery proof rounds use the existing control-surface timeout, not the competing-launch settlement grace. Timeout and I/O failure remain distinct outcomes; matching and dispatch eligibility are separate checks. The final timeout read evaluates matching replies before sweeping probe-owned rows, so valid proof wins. Cleanup never downgrades proof or deletes another request's reply. Manager reactor probe scheduling is unchanged.
>
> Record one structured diagnostic for an abnormal automatic-start decision, including incumbent TID, submitted TID when known, last probe request ID, and decision reason. Do not log payloads or secrets. Successful routine reuse need not log. Diagnostics must not alter acceptance or proof.

### Acceptance: 05-Message_Flow_and_State.md [MF-1], [MF-6], [MF-7]

Replace the automatic rollback/manual-recovery submission bullets and summarize identically at [MF-1]:

> A successful spawn-request write commits acceptance under its returned TID. Manager discovery and bootstrap are post-acceptance availability work. Failure to prove or start a manager must not automatically delete the accepted request, rewrite task state, resubmit it, or remove its TID from the caller's result.
>
> After readiness failure, reconcile by the accepted TID using existing durable evidence. Spawned, queued, reserved, and unknown-location observations all retain historical acceptance. Unknown location must be reported as unknown, never as proof the request remains queued. An authoritative manager rejection remains a typed submission error carrying the accepted TID and rejection reason. A reconciliation read failure preserves acceptance with an explicit diagnostic. No success is inferred when the spawn-request write itself failed or has an ambiguous outcome; that pre-acceptance error boundary remains unchanged.
>
> Submission-scoped recovery never consumes, moves, or deletes reserved requests. Known reservation ends further manager startup attempts for that submission; execution/result observation and existing reserved recovery continue under their existing owners.

### Public adapters: 14-Python_API_Surfaces.md [PY-2], [PY-3]; 10-CLI_Interface.md [CLI-1.1.1]

> A returned Task or submission receipt proves broker acceptance, not manager readiness or completed execution. Confirmed acceptance survives manager-readiness failure without changing receipt fields or assigning a second TID. Existing typed manager-rejection errors retain the TID; readiness-only failures after acceptance are reported as diagnostics rather than submission errors. Direct manager lifecycle commands continue to require their documented readiness proof.
>
> `weft run --no-wait` returns the accepted TID and exit 0 after confirmed acceptance despite readiness degradation; emit the degradation warning to stderr, leaving stdout and existing JSON receipt shape intact. Waiting execution continues through the existing result/wait path and retains existing timeout, task-failure, and interruption behavior. A wait timeout does not cancel accepted work. No new default timeout or CLI flag is introduced. An authoritative rejection returns exit 1 with its TID and reason, not an accepted-success result.

### Django adapter: 13C-Using_Weft_With_Django.md [DJ-8.4], [DJ-9.1]

> Deferred submission binds the accepted TID when the core broker write succeeds, including when subsequent manager readiness cannot be established. Readiness-only degradation must not raise from that commit callback or prevent later callbacks from running. Pre-commit validation/captured-context rules remain unchanged. Broker-write errors and authoritative rejection retain their error behavior. `on_commit` remains an in-memory transaction hook, not a durable outbox or atomic cross-database dispatch guarantee.

## Detailed State Machines and Ownership

### Probe and recovery

Use the existing `CONTROL_SURFACE_WAIT_TIMEOUT=2.0`, `MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS=2.0`, and `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS=300.0`. Keep the 0.5-second competing-startup grace for its existing owner. Update the namespace-grace docstring to say that the first two-second proof normally subsumes the grace, so an extra wait is usually zero; update the control-timeout docstring to distinguish caller discovery from competing-launch settlement. Add neither a separate four-second policy clock nor a new probe constant. Two probes plus the remaining grace are finite by construction: normally at most four seconds of deliberate waiting, excluding backend I/O and the separate existing launch deadline.

Replace the private ensure tuple with `ManagerEnsureResult`: outcome ready/not_needed/uncertain, optional proved manager record, actual started_here/process ownership, and reason. The two phase seams consumed by commands are public core names, not underscore-private helpers: `observe_manager_availability(...) -> ManagerAvailabilityObservation` and `decide_manager_recovery(...) -> ManagerRecoveryDecision`. Export these names from `weft.core.manager_runtime.__all__`; commands continue importing the core module and call only exported names. Their interface accepts a context plus the existing optional borrowed broker and the prior observation for the decision phase; submission-specific TIDs and reconciliation results do not cross this seam. Preserve all callers. Do not add a general state-machine framework.

Factor only the two phases needed for shared reuse inside `manager_runtime.py`: initial observation and final recovery decision. Both generic `ensure_manager` and command-owned `ensure_manager_after_submission` use these same phases and existing `start_manager`; neither duplicates selection or bootstrap. The public core observation value carries initial candidate identity, first_uncertain_at, whether backlog was observed, and last probe outcome/request ID. The public core decision value distinguishes reuse / launch / no_start; launch reason distinguishes policy absence from helper recovery. These are ephemeral core coordination values, not public receipt fields or cached runtime authority. Core never receives a TID-progress predicate and never imports command reconciliation.

For these two discovery rounds, call `send_keyed_ping_probe` directly with `timeout=remaining_round_budget`, initialized to `CONTROL_SURFACE_WAIT_TIMEOUT`; for a single candidate this is the full two seconds. Apply existing `pong_proves_dispatch_eligible` to its matched payload and keep its typed timeout/error result for diagnostics. Do not call `_manager_record_has_matched_pong`, whose current 0.5-second clamp and Boolean result cannot implement this contract. Leave that helper and competing-launch settlement behavior unchanged for their other existing callers. Obtain the initial/final raw registry snapshots with `_snapshot_registry(..., prune_stale=False)` and apply the explicit latest-row/runtime/age classification described here, so `_registry_view` or a filtered snapshot cannot inject hidden 0.5-second probes into either round. Reuse normalization, eligibility and ownership reduction; do not duplicate PONG parsing or dispatch-field validation. R3 must prove a valid matching reply observed after 0.5 seconds but before the two-second round ends is accepted in that same first round without starting a helper or second probe.

Initial observation folds latest records before liveness filtering, then reuses exactly `is_canonical_manager_record`, `manager_registry_record_liveness`, and `_manager_record_unknown_is_expired` for candidate shape, scoped runtime proof, and the existing age cutoff. Do not rewrite those classifiers in the new phase functions; if the age helper must become public for the shared core interface, rename it mechanically to `manager_record_unknown_is_expired` and update all callers without changing logic. Evaluate runtime proofs before spending one round's shared two-second PONG budget (lowest canonical TID first). Record first_uncertain_at and successful backlog presence BEFORE that first probe so probe time counts toward the existing grace. Close registry iterators before waits; no transaction spans the observation interval. A matching but ineligible PONG is not ready. Failed registry/backlog I/O suppresses startup in this attempt, rather than initiating an additional retry loop.

Generic callers: after an unsuccessful first probe with pending backlog, wait `max(0, first_uncertain_at + grace - monotonic())`, then run the final phase. Submission callers: use that same remaining interval for exact-TID reconciliation instead of a generic sleep, then run the final phase only if the request is still queued. Do not stack another full grace after the first probe.

Final phase: refresh latest registry/runtime evidence; if absent under existing age/terminal policy, authorize normal startup without another PONG. If ready, reuse. If the uncertain incumbent changed identity, return no_start/uncertain rather than restarting grace. If it remains the same, make exactly one second proof round with a fresh request ID and shared two-second budget, then do one fresh registry/backlog read. Positive proof wins. A newly changed unproved incumbent or failed final read suppresses launch. Empty backlog returns not_needed; same unproved candidate and successful pending observations after grace authorizes helper. There is no third probe, separate total deadline, or deadline-settlement exception. A final registry read may recognize authoritative expiry/stale/terminal evidence as policy absence. An initially absent observation also needs a fresh registry check before launch; a newly arrived unproved candidate returns uncertain rather than launching or starting a new recovery sequence.

| Observation | Action |
| --- | --- |
| Ready manager | Reuse; no launch. |
| Successful read, no policy-eligible incumbent (including expired unknown) | Fresh registry check; authorize ordinary start if still absent. |
| Fresh unknown incumbent + no pending backlog | No helper; explicit manager-start caller must not report ready. |
| Fresh unknown + pending at first observation | First probe; wait remainder of existing grace; second probe; final registry/backlog check. |
| Final proof ready | Reuse. |
| Same still-unknown incumbent + successful final pending backlog after grace | Allow helper, acknowledging busy-incumbent risk. |
| Changed unproved candidate, registry/backlog read error, or unknown submission location | Return uncertainty without launch. |
| Exact submitted TID reserved/spawned/rejected | Command stops startup for that submission; preserve acceptance or raise known rejection. |
| Authorized launch fails | Preserve existing abort/reap cleanup; acceptance owner handles only expected availability failure. |

Proof caches last one round only. Expired unknown rows are policy-filtered, not reader-deleted. Never resurrect an old active row behind a newer stopped/superseded row. Positive runtime proof and the existing lowest-live-TID reduction are unchanged.

### Command-owned exact-request observation

`reconcile_submitted_spawn` currently returns immediately on queued even when timeout is positive. Therefore the review's suggested timeout-only call would not wait. Add a private keyword `queued_is_terminal: bool = True`, analogous to existing reserved_is_terminal. Only recovery passes False: queued continues observation until the supplied timeout, while reserved/spawned/rejected return immediately. At expiry return the latest actual observation, not a cached queued state; an unknown final location remains unknown. Reuse existing watcher/resources and evidence ordering; do not duplicate polling or add a background observer. Existing callers retain their default behavior.

The submission command orchestrates initial core observation, reconciliation with timeout equal to remaining grace (zero still performs one read), and final core decision. If initial discovery is policy-absent, reconcile once before authorizing immediate startup. Before acting on a launch decision, reconcile once more with timeout zero: only still-queued permits launch for this submission. Reserved/spawned ends manager recovery, rejected raises existing typed error with TID in its message, unknown/read failure returns acceptance with warning. If recovery is skipped because a manager is already ready, do not add a full reconciliation scan to ordinary successful submission. Core generic manager/heartbeat callers have no submission knowledge and use the same core phases directly. The final exact read cannot eliminate the subsequent launch race; that is an explicit accepted risk, not a fencing guarantee.

### Expected failure classification

Reuse existing `ManagerStartFailed` for recognized detached bootstrap failure. Normalize the launcher first-event timeout, explicit `spawn_failed` event, malformed/missing spawned-PID protocol event, and OS failure creating startup directories/files or starting the launcher at their source in `manager_runtime.py`; preserve original causes and existing abort/reap cleanup. Do not convert every RuntimeError from manager code into this type. Known `BrokerError` and `OSError` from registry/probe/reconciliation I/O are caught at those narrow boundaries and produce an uncertain/read-error diagnostic. The post-acceptance owner handles only these expected classes and typed ensure outcomes; arbitrary RuntimeError/Exception must propagate, with accepted TID diagnostic and no request deletion. Existing broad defensive probe catches must be audited so they do not silently classify a programmer RuntimeError as backend unavailability. Cancellation exceptions retain their current explicit cleanup path. No new public exception hierarchy is needed.

### Acceptance and adapters

Confirmed broker write is the only `not_submitted -> accepted(tid)` transition. After it, readiness changes availability diagnostics, not acceptance. Known rejection is a separate accepted-but-rejected disposition exposed through the existing typed error (include the TID in the existing error message; retain its constructor and public attributes). Do not invent new TaskSpec execution states.

`ensure_manager_after_submission` owns the command boundary and retains a typed availability result. `_run_with_managed_execution` and `_submit_prepared_outcome` consume it: bind/announce the TID, emit one degradation diagnostic if needed, and optionally wait. Direct manager commands unwrap ready only; heartbeat continues its existing bounded endpoint-readiness observation after uncertain/not_needed, then raises its existing timeout if no live endpoint appears; it must not return an endpoint solely from the manager result. Inventory every `ensure_manager`, `_ensure_manager_after_submission`, `ensure_heartbeat_service`, and `upsert_heartbeat` caller with `rg` before migration. Include the indirect TaskMonitor path `TaskMonitor._ensure_heartbeat_registered -> upsert_heartbeat -> ensure_heartbeat_service -> ensure_manager`, which runs inside a service turn and already tolerates the existing ten-second startup block. Preserve started-here cleanup only for managers actually launched/owned by this call.

## Implementation Tasks

Execute sequentially; independent reviews may run while unrelated documentation verification runs. Every slice uses complete annotations, module-level imports, named constants in `_constants.py`, and spec-linked docstrings. No new dependencies, project configuration, or CI edits.

1. [x] **Spec-promotion slice.** Apply exact deltas above to the six named specs; synchronize contradictory summary paragraphs and implementation notes in those same sections, add plan backlinks, record promotion baseline. Read the specs at baseline first. Check README submission summaries for contradictory rollback claims and update those exact statements with the acceptance slice. Do not generalize cleanup changes beyond submission. Stop if extra execution/queue contracts must change. Verify metadata/spec hygiene and review the promoted delta before code.
2. [x] **Probe and recovery.** Files: `weft/core/control_probe.py`, `weft/core/manager_runtime.py`, `weft/_constants.py`, `weft/_exceptions.py`, `weft/commands/manager.py`, `weft/core/heartbeat.py`, `weft/core/monitor/task_monitor.py` (inventory/verification; edit only if its error boundary needs synchronization), `tests/core/test_control_probe.py`, `tests/commands/test_manager_commands.py`; migrate submission callers mechanically as necessary to keep the result contract coherent. Reuse existing PONG coercion, eligibility, registry normalization, the three named snapshot classifiers, ownership reducer, queue wait, and detached launcher. Expose the shared observation/decision phase names through `manager_runtime.__all__`; do not present cross-layer functions as private. Reuse the existing 2-second control timeout and ambiguity grace, retain 300-second expiry, and leave competing-launch settlement unchanged. Pin the two-probe sequence and docstrings; explicitly say the first probe normally subsumes the grace. Do not add a separate policy deadline. Prove old premature-start and final-sweep behaviors red first. Review state tables and absence/uncertainty classification independently. Stop if a synchronous recovery wait is introduced into a manager reactor or service turn without checking its existing owner deadline/error containment, including TaskMonitor's heartbeat-registration turn, or if reader cleanup changes custody.
3. [x] **Durable acceptance and caller migration.** Files: `weft/commands/submission.py`, `weft/commands/_spawn_submission.py` for the default-preserving queued_is_terminal option, `weft/commands/run.py`, `weft/_exceptions.py`, client/CLI adapters listed above only where consumption/diagnostics require it; `tests/commands/test_submission.py`, `tests/commands/test_run.py`, `tests/commands/test_run_public.py`, `tests/system/test_run_diagnostics.py`. Remove the post-readiness automatic-delete branch, not general queue-delete APIs. Reuse exact-TID reconciliation; return original acceptance even on location-read failure. Prove request retention then later execution through a real manager. Prove rejection and failed-write paths do not become success. Stop if accepted work must be re-enqueued, if no-wait output needs a new shape, or if a broad catch hides programmer errors. Independent review before broad adapter verification.
4. [x] **Adapter proof, migration documentation, and traceability reconciliation.** Files: Django integration tests/client docstrings if needed, `README.md`, `integrations/weft_django/README.md`, source specs, module/function mappings, this plan/index; update `docs/agent-context/runbooks/runtime-and-context-patterns.md` only its obsolete description of automatic submission rollback (product-contract synchronization, not a new workflow policy). Verify three prepared callbacks with a readiness failure on the second all bind accepted TIDs, rollback submits none, and bad input raises before commit. No robust-callback flag or outbox. Run final gates and independent completed-work review; reconcile every accepted finding, spec backlink, and mapping before closing the implementation plan.

## Contract Verification Matrix

Use existing backend-shared `broker_env` and `WeftTestHarness`, with owned real managers/children. Mock only the local PID-visibility observation for a container namespace and explicit injected failure boundaries, never Queue, TaskSpec lifecycle, reservation, reconciliation evidence, or PONG matching. Use barriers/events around real operations for ordering, not sleep-based correctness. Pure transition tests may use an injected monotonic clock. At least one real-clock process test must exercise end-to-end recovery; fixture teardown must close all producers before asserting exact absence or counts.

| ID | Test / observable oracle | Owner |
| --- | --- | --- |
| R1 | Nonexpired unknown registry older than two seconds + new queued request cannot authorize helper before caller-observed grace; first probe time counts, exactly two proof rounds maximum, no extra full grace; reproduce old early launch red. | manager commands |
| R2 | Newer stopped/superseded record never resurrects an older active row. Expired unknown row stops suppressing startup under existing 300-second policy, without deletion or an added grace/probe tax; a positively live process remains usable despite old row age; stopped/superseded and authoritative-dead rows do not. Empty registry permits bootstrap; failed registry read does not. | manager commands |
| R3 | A matching PONG after the old 0.5-second limit but before two seconds is accepted in the first round (no hidden clamped probe or second request); late valid keyed PONG during recovery suppresses helper; final-read PONG wins; mismatched TID/request ID, malformed and stopping/draining replies do not prove ready; unrelated rows survive. | control probe + manager commands |
| R4 | Probe I/O error and timeout remain distinct; injected programmer RuntimeError escapes rather than being reported as availability; cleanup failure cannot erase positive proof; diagnostics carry request ID/reason without payload. | control probe |
| R5 | Empty backlog/failed reads suppress helper; changed unproved incumbent suppresses launch without restarting sequence; same fresh silent or busy incumbent with pending backlog permits helper after grace; first/second PONG suppresses launch. | manager commands |
| R6 | queued_is_terminal defaults to immediate return; False waits through queued until remaining grace then returns latest evidence. Exact TID reserved/spawned/rejected during recovery prevents startup for that submission; rejection still raises with TID; unrelated task progress does not stand in for exact evidence. | submission |
| R7 | Two concurrent callers can converge with no duplicate execution of either exact request; real reservation and existing cleanup are exercised. | manager commands |
| A1 | Bootstrap filesystem/launcher failure after write returns original TID and retains exact public request; later real manager executes it once. Old rollback test is red before fix. | submission + run public |
| A2 | Reserved and unknown-location/read-error after confirmed write preserve acceptance; no deletes/requeues; write failure/ambiguous write does not become accepted. | submission |
| A3 | No-wait exit 0/TID plus stderr warning; JSON stdout remains parseable. Waiting success/failure and explicit wait timeout keep existing codes, no traceback, no request cancellation. Explicit manager start failure remains nonzero. | run public + diagnostics |
| A4 | Actual started-here ownership only controls cleanup; uncertain incumbent is never stopped by caller cleanup; Ctrl-C preserves its existing explicit control behavior. | run |
| D1 | Real Django commit with three callbacks and injected readiness-only failure on callback two binds all three TIDs (minimal before/failure/after proof); broker has their exact requests or execution evidence. | Django integration |
| D2 | Rollback writes no requests; invalid input fails before commit; failed broker write and authoritative rejection still raise; captured nondefault context survives. | Django integration |

Apply the adversarial acceptance runbook floors to every touched public adapter, including empty/malformed input, stable exit mapping, no traceback, and per-task containment. Failure isolation in D1 is specifically readiness-only, not a claim all commit-hook failures are isolated. Enumerate each row's subcases in firing tests, not one happy-path assertion.

## Verification and Gates

Load `. ./.envrc` before commands; use the repository environment. Install missing development dependencies only through the existing `uv sync --all-extras` workflow. Formatter ownership: implementer runs repo Ruff formatter on touched Python files only. Do not reformat unrelated code.

Plan-only verification (no runtime changes):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
git diff --check
```

Per-slice targeted verification:

```bash
./.venv/bin/python -m pytest tests/core/test_control_probe.py tests/commands/test_manager_commands.py -q
./.venv/bin/python -m pytest tests/commands/test_submission.py tests/commands/test_run.py tests/commands/test_run_public.py tests/system/test_run_diagnostics.py -q
./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q
```

Final implementation gates:

```bash
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/ -q
git diff --check
```

Use the repository's configured traceability checker if present at implementation time; determine its command from repo tooling, do not invent one. Spec hygiene and reciprocal implementation mappings must have no new errors/warnings; any mandatory zero-warning traceability gate remains mandatory. Record actual commands/results, red-before-green evidence per regression, independent review dispositions, and promotion baseline. No runtime tests are claimed by plan-authoring verification. Completion/ready-to-land claims require the repo commit gate; do not commit without user authorization. A review handoff can explicitly list uncommitted files instead.

Post-rollout acceptance in an isolated production-shaped environment: burst from a PID-isolated client to a host manager; delayed proof causes recovery diagnostics without premature launch; every accepted TID stays discoverable or executes; a stopped manager recovers within the finite probe/grace sequence and existing startup/backend timeouts; read-only startup failure leaves accepted work available. Do not run this destructive/fault-injection exercise against ops production.

## Independent Review and Fresh-Eyes Review

Independent review is required for this plan AND proposed delta before implementation, after each meaningful implementation slice, and before final closure. Prefer a different family. Review brief: evaluate implementability and system degradation against the baseline and exact delta; prioritize P1/P2 findings, suggest removing unnecessary work, and answer PASS/BLOCKED against those two questions. Existing unrelated concerns are observations unless this change worsens them. Accepted risks: a busy healthy incumbent can leave the new request unreserved across both probes and cause a helper; possible duplicate helper starts after final check, accepted tasks may wait during outage, existing unbounded wait remains, on_commit is not an outbox. No accepted risk excuses unsafe custody or absent diagnostics.

### Review history and dispositions

Earlier native same-family review passed the original draft after F1 (typed expected-failure classification) and F2 (deadline settlement) fixes. That verdict does not cover this revision. The user supplied a separate source-backed review on 2026-09-17 with scope/degradation verdict BLOCKED; its model family was not identified. Re-review must cover the dispositions below and any defects introduced by simplification, not reopen unrelated accepted tradeoffs.

| ID | Finding | Disposition / evidence |
| --- | --- | --- |
| D1 | Retaining expired unknown rows imposes repeated recovery delay with no retirement owner. | Accept. Retain existing 300-second policy cutoff; no retirement lifecycle. Runtime proof still wins, and readers do not delete. R2 verifies no added observation delay for expired unknown. |
| D2 | Healthy busy manager can trigger helper because own request remains pending. | Accept explicitly as proposed rollout tradeoff below, including resource contention. No claim of eliminating all redundant starts; R5/R7 verify safe dispatch/convergence. |
| D3 | Probe count ambiguous. | Accept. Initial and final rounds only, first probe counts toward grace; no intermediate or third round. |
| W1 | Four-second deadline exists to manage added machinery. | Accept. Remove separate clock, constant, settlement exception, and associated deadline-edge tests. Two probes plus remaining grace are finite. Earlier F2 is superseded by removal of its cause. |
| W2 | Submission predicate crosses an unnecessary seam. | Accept layering change, correct suggested mechanics. Source `_spawn_submission.py` returns queued immediately regardless of timeout. Add private default-preserving queued_is_terminal=False for recovery and share core observation/final-decision phases; no predicate, no duplicate polling. |
| W3 | Heavy diagnostic protocol and spec timing arithmetic. | Accept. One abnormal decision log with four fields; short numbered spec transition list; timing values/sequence in existing constant docstrings and implementation section. |
| M1 | New exported exception tid attribute changes public API. | Avoid unnecessary addition. TID remains in existing error message; no new attribute/constructor contract. Existing acceptance semantic change is still explicitly marked for owner approval before implementation. |
| M2 | 65 callbacks/failure 54 unexplained. | Accept. Use three callbacks, fail readiness on the middle one. This proves before/failure/after containment without encoding incident counts. |
| N1 | Shared observation/decision phases were described as private despite command callers. | Accept. Name public core phase functions/types, export them from `manager_runtime.__all__`, and keep submission-specific state outside their interface. |
| N2 | TaskMonitor reaches ensure indirectly inside a service turn. | Accept. Add the full heartbeat call chain to caller inventory and the service-turn stop gate; verify existing bounded error containment. |
| N3-N5 | Reuse the existing classifiers, document grace/probe timing, and avoid implying a cutoff behavior change. | Accept. Name the three classifiers, require mechanical reuse, update constant docstrings, and state that the 300-second cutoff is unchanged. |
| PB1 | Claude R2 review: explicit two-second discovery budget could accidentally retain the old Boolean helper’s 0.5-second clamp. | Accept clarity fix. Require direct typed send_keyed_ping_probe with remaining round budget, raw unprobed registry snapshots, shared eligibility validation, and an R3 reply-between-0.5-and-2-seconds oracle. Other existing helper/settlement callers remain unchanged. |
| F1 | Earlier review: plain RuntimeError mixes expected launch failure with programming defects. | Retained. Normalize named expected failure sites to existing ManagerStartFailed; paired operational/programmer-error tests. |
| S1/S2 | Earlier author review: wrong ownership helper path and tombstone/heartbeat ambiguity. | Retained corrections: service_convergence.py; latest-row reduction, no resurrection; heartbeat still requires endpoint proof. |

### Revision and difference register

Revision R2 (2026-09-17): simplifies the discovery half in response to the user-supplied review; acceptance contract, class, hardening, and adapter proof remain. No baseline spec or runtime code is modified by this revision.

| Difference from baseline | Proposed acceptance / consequence | Verification |
| --- | --- | --- |
| Fresh uncertain incumbent now gets up to two 2-second proof rounds, counting first probe toward 2-second grace. | Up to roughly 4 seconds deliberate waiting rather than a single 0.5-second probe; no extra 4-second policy clock. Backend I/O and existing launcher deadline remain separate. | R1, R3, R5 |
| Expired unknown manager remains age-filtered at 300 seconds. | No repeated observation tax from indefinitely retained unknown candidates. Aging can permit a helper even if old owner is actually alive; this is existing bounded-recovery policy, not proof of death. | R2 |
| New request can remain unreserved while healthy incumbent is busy or admission-limited. | A helper can still start after the finite sequence and compete for resources; atomic reservation prevents duplicate claim, convergence drains redundancy. No guarantee of exactly one manager or exactly-once side effects. Owner must accept this tradeoff with the implementation plan. | R5, R7 |
| Readiness/startup failure no longer cancels accepted request. | Non-wait submit succeeds while execution may be delayed; callers must use returned TID instead of blindly resubmitting. | A1-A4, D1-D2 |

Fresh-eyes revision check: verified the timeout-only reconciliation recommendation would return queued immediately; specified its minimal compatible extension. Checked first probe time is counted, registry expiry retained, changed candidate does not restart an unbounded sequence, and no new public exception attribute remains. Independent revision review and final plan-only command results follow.

### R2 other-model review and verification (2026-09-17)

Claude reviewed the full revised plan and supplied review against source, using only Read/Grep/Glob tools, empty MCP configuration, and no user/project/local settings. No attempted-write qualification was required for this tool-restricted review. This is an actual other-model review, distinct from the earlier native same-family pass.

Round 1 confirmed D1/D2/D3/W1/W2/W3/M1/M2 dispositions, returned system-degradation PASS under the declared risks, and blocked implementability solely on PB1. The author verified the existing clamp and accepted the clarification. Scoped round 2 returned PASS on PB1 with no findings, verifying the direct probe signature, raw unprobed snapshot, shared eligibility helpers, and unchanged competing-launch settlement. These verdicts verify plan text, not implementation or runtime behavior. Owner acceptance of the revised public behavior and explicit busy-helper risk still precedes spec promotion/implementation.

At the plan-only checkpoint, `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q` after `. ./.envrc` passed all four checks; `git diff --check` passed; all 60 linked/backticked repository paths resolved and the index count was 220. That checkpoint changed only this plan plus its index entry. The later implementation results are recorded below.

### Implementation review and verification (2026-09-17)

An independent implementation review initially blocked on two P2 verification gaps: no live Typer-adapter proof for degraded no-wait output, and no concurrent two-caller exact-request oracle. The implementation added both tests. Re-review returned **PASS** with no remaining P1/P2 findings. The reviewer also verified that unproved manager rows cannot escape as ready, initial backlog-read failure suppresses launch, disappearance decisions retain incumbent/probe diagnostics, unexpected post-acceptance exceptions retain their type and name the accepted TID, and all legacy callers use the named result contract.

Implementation found and corrected two broader-suite issues before the final green run. Ordinary first-manager bootstrap initially emitted the abnormal-helper warning on CLI stderr; warning emission is now limited to launch decisions carrying incumbent evidence. Two connection-ownership tests still expected arbitrary `RuntimeError` to be downgraded to probe availability; they now assert propagation while proving owned and borrowed broker lifetimes remain correct. These were contract synchronization fixes, not deviations from the approved plan.

Actual final gates:

- `./.venv/bin/python -m pytest -m ""`: 5,119 passed, 39 skipped.
- `./.venv/bin/python bin/pytest-pg --all`: 5,063 passed, 24 skipped.
- Full repository mypy command: success, 434 source files.
- `./.venv/bin/ruff check .`: passed.
- Repository formatter check: 459 files already formatted.
- `./.venv/bin/python -m pytest -n0 tests/specs/ -q`: passed, 162 tests.
- `git diff --check`: passed.
- Focused touched suite: passed with three expected PostgreSQL-only skips.

The Ruff suppression registry was reconciled with the implementation: the obsolete broad-catch approval was removed, the recovery decision state machine received `RUFF-SUP-375`, aggregate counts and firing expectations were updated, and the generated index check passes. No deployment or production fault injection was performed.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| none | The approved state machines, custody boundary, adapter behavior, and named public core phases. | Implemented as planned. | No product or architecture deviation was required. | none |

## Out of Scope

Governance data/code/retries; production repairs or deployment; Django transactional outbox; robust callback mode; idempotent resubmission protocol; changes to LivenessMonitor or manager reactor leadership proof budgets; new dependencies; CI/project configuration; distributed launch fencing; generic health orchestration; queue persistence/retention redesign; public manager-mode flags. These are not implicit follow-up tasks.
