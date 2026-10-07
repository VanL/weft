# AdmissionController: Progress-Aware Admission Waiting

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.18]
Superseded by: none

Class: 5. This changes PostgreSQL admission-wait diagnostics and the ownership
of admission policy, requiring an explicit spec delta and independent review.
Plan type: implementation with spec revision; strategy B (atomic).
Owner: Weft Manager maintainers. Authorization: implementation per owner
request on 2026-10-06; no commit, push, release or deployment requested.

## Goal

Put the existing admission arithmetic and a small PostgreSQL drain-wait policy
in `weft/core/admission.py`, consulted by Manager before reserving new spawn
requests. Keep waiting while draining makes progress; identify waits that
stall or exceed a bounded episode. Preserve queued work, current capacity
limits and the Manager's responsive reactor. This detects stalled resource
draining; it does not guarantee application work or PostgreSQL recovery.

## Scope Challenge

The existing Manager already denies before reservation, suppresses blocked
spawn sources, retries every second, and wakes early after launch/reap events.
Do not introduce another wait loop. The substantive addition is a small
progress budget and truthful transition logs, not a new scheduler.

The slice adds one policy module, two named timing constants, and narrow
Manager hooks. No new environment key, CLI option, dependency, thread,
connection, queue, durable state, permit, child ceiling, process identity
query, task-log subscription or backend SQL. No changed lane arithmetic,
SQLite observation, pool/LISTEN recovery, leadership or service policy.

Previously suggested startup pacing is excluded deliberately. The process-
start result precedes child broker startup. `_child_launch_runtime_evidence_seen`
also accepts Manager's own launch event. Child TID mapping is best-effort
and can precede later listener creation. Making any of these a readiness
permit would require failure and fairness rules beyond this small slice.
Raw PostgreSQL usage can therefore lag cold launches or later session growth.
This plan neither fixes nor claims to fix that burst-accounting gap. If the
owner requires a hard bound on outstanding launch demand, stop and design
that separately rather than adding guesses to `numbackends`.

## Source Documents and Baseline

- [Manager architecture](../specifications/03-Manager_Architecture.md)
  [MA-1.8]: lane limits, pre-reservation denial and reactor retry ownership.
- [SimpleBroker integration](../specifications/04-SimpleBroker_Integration.md)
  [SB-0.4]: public PG statistics helper through the retained Queue.
- [Message flow](../specifications/05-Message_Flow_and_State.md) [MF-6]: source
  queues, reservations and recovery.
- [System invariants](../specifications/07-System_Invariants.md) [MANAGER.18],
  [MANAGER.9]-[MANAGER.17]: control, lanes, leadership and wait ownership.
- [Existing admission plan](./2026-08-25-manager-admission-control-plan.md)
  is historical implementation rationale; current specs govern. Its index
  metadata is not evidence that its shipped paths need reimplementation.
- [AGENTS](../../AGENTS.md), [engineering principles](../agent-context/engineering-principles.md),
  [writing plans](../agent-context/runbooks/writing-plans.md),
  [hardening](../agent-context/runbooks/hardening-plans.md),
  [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md).

Spec baseline: `d50375e265bc87363468b37ef6fa860e9d85a468`. This plan revises
[MA-1.8] and [MANAGER.18]; existing [SB-0.4]/[MF-6] behavior is unchanged.
The owner's dependency update adopts SimpleBroker >=8.5.0 and PG >=4.5.0.
Do not edit its dependency declarations or lockfile. No new library API is
needed: get_connection_stats already exists.
Promotion baseline: record the applied spec delta and diff base during the
atomic implementation slice; do not cite plan prose as a shipped contract.

## Current Structure and Files

Read before editing:
- `weft/core/manager.py`: `_admission_capacity`, `_observe_admission_usage`,
  `_record_admission_decision`, `_clear_admission_retry`,
  `_schedule_admission_retry_for_source`, `_expire_admission_retry_if_due`,
  `_admission_allows_queue`, `_process_queue_message`, `_has_pending_messages`,
  `_cleanup_children`,
  `_admission_retry_timeouts`, `next_wait_timeout`.
- `weft/core/tasks/base.py`: best-effort TID publication is not a readiness
  handshake. Read only; this slice does not change BaseTask.
- `tests/core/test_manager.py`: existing real broker admission, lane priority,
  observation-failure, retry, control, launch restoration and reap proofs.
- `tests/helpers/weft_harness.py`, `tests/conftest.py`, `.envrc`: canonical
  fixture/tool configuration. Preserve normal test parallelism.

Modify only:
- new `weft/core/admission.py`; `weft/core/manager.py`; `weft/_constants.py`;
- new `tests/core/test_admission.py`; owning admission cases in
  `tests/core/test_manager.py`; `tests/conftest.py` owning shared-module entry;
- the two revised specs, their implementation mappings and plan backlinks;
- `README.md` admission paragraph (no config table change), `CHANGELOG.md`;
- this plan and its index row. Lessons only if a reusable correction arises.

Comprehension checks: why does launch-worker completion not prove capacity was
released? Why must expiring the one-second retry timer not reset drain history?
Why is child reap evidence different from a task terminal envelope? Why can a
successful statistics read alone never refresh the progress clock?

## Minimal Policy and Ownership

Manager keeps I/O, source-to-lane mapping, blocked-lane suppression, retry
deadlines, process ownership, logging and its SQLite liveness memo. The module
has no Manager import and no broker/process operation.

Move `_admission_capacity` without changing its arithmetic into the module,
and move `_admission_blocked_lanes_for_capacity` with it. Keep `_admission_state`
in Manager for logging. Move the three existing arithmetic tests to the new
owning test module and update their imports; no duplicate tests or Manager
re-export alias. AdmissionController uses that helper to compute capacity/blocked lanes from the supplied usage,
configured maximum/reserve and liveness-monitor setting. Unknown usage still
blocks both lanes. Disabled admission bypasses observation entirely. SQLite
receives the same decisions and one-second scheduling as before, with no new
progress diagnostics.

Use one small immutable decision value for the existing capacity fields,
blocked lanes, and optional PG wait phase/elapsed/idle evidence. The existing
Manager `_admission_enabled` guard owns disabled admission and bypasses the
observer/controller; do not add a second disabled branch. Manager
consumes it in `_admission_allows_queue` and `_record_admission_decision`.
The decision type is private and need not preserve the old dictionary layout
in new tests. Preserve owning arithmetic assertions while updating their import
where appropriate. No provider protocol, strategy registry, generic event bus
or duplicate cache.
The controller evaluates supplied observations at an explicit monotonic `now`,
notes actual child reaps, and clears an episode on proven empty spawn sources.
Reading time and PostgreSQL remains in Manager. Constructor settings come from existing
admission config plus named constants, not a new resolver. Initialize only
after Manager has loaded the liveness-monitor setting; the policy owns no
resource and needs no close/unwind lifecycle.

Use this private API shape, rather than inventing additional methods:

```text
AdmissionController(max_connections, reserve_fraction, liveness_monitor_enabled)
evaluate(*, used: int | None, backend: str, now: float) -> AdmissionDecision
note_child_reap(*, now: float) -> None
reset_wait() -> None
```

AdmissionDecision has only `capacity` (the existing computed capacity mapping
or None), `blocked_lanes` (frozenset of public/internal), `wait_phase` (None,
waiting, stalled), and optional elapsed/idle seconds. Manager derives allow
from the requested lane not being blocked. Phase/times are absent for
SQLite and open decisions. Keep literal lane/phase types and complete function
annotations. Configuration is already validated by the owning resolver; do not
add a second config validator. Timeouts come from named constants. Constructor
does no I/O. A reap notification while no PG episode is active is a no-op. No new public export or close method.

### One PostgreSQL wait episode

An episode starts when a supplied PG observation first denies at least one
lane, including an unavailable observation. Launch-failure restoration alone
never starts one. It ends when a fresh successful observation allows both
lanes, or an existing pending-work scan proves both spawn sources empty.
An allowed internal launch while public remains blocked does not end it. A one-second retry expiry, early wake, failed launch,
launch-worker result or lane-clear helper does not end/reset the episode.

In Manager `_has_pending_messages`, collect empty spawn lanes locally within
the existing scan, using only the successful queue probes that would already
run. Immediately after the second source probes empty, check `_stop_event`;
only if unset, call `reset_wait()`, regardless of later scan early returns. Keep existing early returns, lane skips and probe
order; add no read, retry-expiry probe or persistent emptiness cache. A skipped,
failed or stopped probe is not empty-source evidence. If a scan cannot prove
both empty, retain history conservatively. Resetting clears only the three
episode values, not lane suppression or retry timing. A later observed denial
then starts a new episode. This avoids attaching an old wait to an observed
idle gap without trying to infer unobserved gaps. This is best-effort: PG
scans are driven by existing activity hints, not the admission retry deadline.
If no scan proves an idle gap, a later burst can inherit the old episode age.
README must state that elapsed is episode age, not a request wait time, and
that idle reset is silent: `open` requires actual observed capacity. Do not
add a timer-driven probe or synthetic open transition to hide this limit.

Retain only start time, last-progress time and lowest known usage. Manager
extends its existing last-decision comparison with wait phase; the controller
does not retain a second logging history. At entry, last-progress equals start
time; an initial valid count sets the low-water mark. If entry usage is unknown, the first valid
count establishes that mark without extending last-progress. Later unknown
observations preserve the mark and both clocks.

Drain progress is either a valid usage count strictly lower than that episode's
lowest known count, or an actual child retired by `_cleanup_children` after
its existing exit proof. Update the last-progress clock once per reap pass;
the registry already removes children, so the same reap cannot recur. Count
oscillation that never reaches a new low is not progress. A terminal row,
heartbeat, request arrival, process-start success, log write or successful
probe is not progress. Capacity release is not proof of application success;
failed children can also release resources. Reaping short internal tasks or
crash-looping children can refresh the idle clock even while aggregate usage
stays high. Accept this resource-progress limit, document it in README, and
rely on the absolute 180-second cap; do not add client/task filtering.

Introduce only named defaults in `_constants.py`:
`MANAGER_ADMISSION_PROGRESS_TIMEOUT_SECONDS = 30.0` and
`MANAGER_ADMISSION_WAIT_MAX_SECONDS = 180.0`. These are initial operational
policy choices, not durations inferred from the ops incident or a guarantee
that its roughly 131-second burst will recover. No new public tuning keys.

On each existing admission reconsideration, phase is `waiting` while idle
elapsed <30s and episode elapsed <180s, otherwise `stalled`. New drain progress
can restore waiting after an idle stall if the episode has not reached 180s.
After 180s phase stays stalled until the episode ends; progress never
refreshes the absolute cap. Equality expires the relevant budget. A fresh
open decision wins even at the expiry instant. Explicit monotonic timestamps
avoid wall-clock jumps; the existing reactor retry scheduling stays unchanged.

### Expiry and observability

Stalled means the bounded *assessment* has expired. It never drops, rejects,
reserves or kills queued tasks, bypasses capacity, stops the Manager, or
abandons automatic recovery. Both waiting and stalled continue the existing
one-second checks and earlier meaningful wakes. There is no additional wait
thread, timer or adaptive-backoff policy. The cap prevents endless optimistic
"still draining" diagnostics; durable backlog itself has no new timeout.

Preserve existing `admission_state` names (`open`, `public_paused`,
`all_paused`, `unavailable`). Add PG `wait_phase` and elapsed/idle seconds to
transition evidence. Emit when either existing lane state or wait phase
changes, through `_emit_serve_log_rate_limited`. Do not put changing elapsed
values in the transition identity, so one blocked interval cannot log every
probe. Specifically, pass only backend/state/wait_phase/lane as `state=`;
usage, elapsed and idle belong in `log_fields=`. Extend the existing early
transition comparison by phase and retain the existing unavailable-warning
rate interval. Prove repeated unavailable samples for the same lane cannot
emit every second solely because evidence times change. Existing identity
changes, including different lanes, retain their rate-limit behavior.
An unavailable sample must not be labelled proven server saturation. These
are operational log additions only, not TaskSpec/state-event/PING/STATUS fields.
Existing serve logging defaults off; preserve that policy. README/rollout must
say to use `weft manager serve --level info` (or set
WEFT_MANAGER_SERVE_LOG_LEVEL=info in serve mode) to see these transitions.
Detached Managers do not emit the existing foreground serve-log stream.
Disabled logging does not disable the controller or affect decisions.

## Invariants, Couplings and Rollback

Denied spawn rows remain in source, not reserved or rewritten. Existing
reservation recovery stays independently actionable. Internal priority and
reserve, leadership fences, shutdown, reaping, cleanup and control remain
unchanged. Preserve TID, immutable spec/io, forward states, queue names,
payloads and runtime-only persistence rules. Do not sum local child counts
with PG usage or infer client ownership from aggregate counts.

The retry lane cache is transient scheduling state; the PG episode survives
its clearing. Spawn failure restoration may clear/re-arm lane timers but is
not drain progress. The existing observer's catch list stays unchanged;
BaseException cancellation propagates. Optional logging cannot change a
successful admission result. Existing synchronous broker observation may take
time; this policy does not interrupt it or add a hard latency guarantee.

Controller state is Manager-local and discarded on Manager exit/restart.
Evaluate/reap/reset run only under the existing serialized watcher drive or
manual-wait ownership. Sentinel/launch workers never mutate it directly; no
new thread or lock is needed. Preserve that ownership exclusion.
No migration/replay or cleanup protocol is needed. Old/new Managers use the
same queues and capacity thresholds; existing leadership convergence applies.
Admission remains opt-in: retain the existing configured positive
WEFT_ADMISSION_MAX_CONNECTIONS and reserve, then restart the Manager after
adoption; use foreground `weft manager serve --level info` for evidence. Revert module/wiring/docs to
restore prior diagnostics; no queued work or stored format needs repair.
Existing WEFT_ADMISSION_MAX_CONNECTIONS=0 still disables the whole guard but
also removes overload protection, so it is not the preferred rollback.
No one-way door or deployment is part of implementation authorization.

## Proposed Spec Delta

Strategy B: atomic new text, implementation mappings, reciprocal module
Spec references, code, tests and README/changelog. Promote during task 2,
not at the end. No planned-spec file or classification change.

[MA-1.8]: retain existing paragraphs and replace its retry-timing sentence
with the following (the existing observer exception/config paragraphs follow):

> Denial or observation failure leaves the row unreserved until the existing
> one-second retry deadline or an earlier child/launch-worker wake. Enabled
> PostgreSQL admission also tracks one Manager-local drain-wait episode while
> either lane is blocked. Its progress clock advances only for a strict new
> episode-low usage observation or an actual child reap; probe success, launch
> completion and retry expiry are not drain progress. An unavailable initial
> observation retains its start clock when a first valid baseline arrives.
> Thirty seconds without drain progress or 180 seconds since episode start
> marks the wait stalled. Fresh capacity that opens both lanes ends the
> episode, as does an existing hint-driven scan proving both spawn sources
> empty without extra reads; skipped, failed or stopped probes cannot establish
> emptiness. Unobserved idle gaps conservatively retain episode age. Internal
> admission alone does not end it. Progress may clear an idle stall
> before 180 seconds, but never renews the absolute assessment cap. Stalling
> changes bounded operational diagnostics only: queued work and the same
> periodic/early recovery checks remain. It never grants capacity, fails work,
> or blocks control and cleanup. These deadlines are monotonic assessment
> budgets, not broker-call or task execution timeouts. SQLite policy is unchanged.

[MANAGER.18]: append after its existing retry-deadline requirement:

> PostgreSQL drain-wait history survives lane retry-cache clearing. Only a
> strict new episode-low usage value or confirmed child reap refreshes drain
> progress. Retry expiry, failed or successful process launch and observation
> success alone cannot do so. An episode starts at first observed lane denial,
> also on an unavailable observation, never on launch-failure restoration alone.
> It ends when a fresh observation opens both lanes or an existing pending-work
> scan successfully proves both spawn sources empty and the post-probe stop
> flag is unset. This best-effort reset retains history across unobserved idle
> gaps. No extra probes or cached
> emptiness state are introduced. Unknown samples preserve its clocks and prior
> known low. Idle
> 30-second or total 180-second assessment expiry marks the wait stalled but
> neither consumes nor fails queued requests nor stops rechecking. Real
> progress can resume an idle-stalled assessment below the absolute cap;
> fresh open capacity always ends it. Wall-clock changes cannot renew budgets.
> This is resource-drain evidence, not task-success proof or a launch permit.

Update [MA-1.8] mapping to `weft/core/admission.py::AdmissionController` for
pure decision/drain budget and Manager for observations, lifecycle and timers.
Add reciprocal spec references and plan backlinks in both changed specs.
README describes PG progress/stall behavior and explicit soft-limit boundary;
Unreleased changelog records the module/wait diagnostics. Do not duplicate
normative thresholds across additional specs.

## Tasks and Verification

1. [x] Prove the required behaviors before changing the runtime. Write owning
   controller tests with explicit timestamps and arithmetic, and real-broker
   Manager regressions for episode survival, queued denial, wake/recovery and
   control. Observe failures/missing production module on the baseline. Keep
   Manager/Queue/reservation/retry paths real; substitute only external stats,
   clock and process-exit evidence when necessary. Use current fixtures.
   Stop if proving denial requires mocking the dispatch/reservation path.
2. [x] Atomically promote the exact delta and implement module/Manager/constants
   with the tests. Reuse capacity arithmetic, one-second timer and rate-limited
   logger. Add reap notification only on the existing actual-reap branch; no
   notification from `_clear_admission_retry`. Run targeted tests/static checks,
   then independent implementation review of this coherent slice. Stop if a
   new launch readiness, permit, background sampler or extra broker read appears.
3. [ ] Align mappings, README/changelog, run full core/PG gates and review final
   differences. Record promotion/verification evidence and dispositions here.
   Close metadata/index together only at authorized committed implementation
   closeout; do not commit merely to satisfy process guidance.

Required firing cases in `tests/core/test_admission.py`: unchanged lane arithmetic
(service floor/fraction/threshold equality), PG-only episodes,
30s/180s equality, multiple real count improvements extending idle but not total,
first-known baseline after unknown, later observation errors preserving history,
low-water oscillation, reap progress only during an episode, idle-stall recovery,
absolute-stall persistence, open-at-expiry, internal-allowed/public-blocked,
reset on both-open or explicit empty-source reset and a subsequent new episode.
No test of private field layout.

Required Manager proofs in `tests/core/test_manager.py`: repeated periodic and
launch-result retry clears cannot postpone stall; actual reaping advances drain
and wakes a fresh check; denial leaves source populated and reserved empty;
blocked source suppresses ordinary wake activity while control/cleanup works;
no-progress/absolute expiry leaves row unchanged and rechecks continue; later
capacity recovery launches through the existing path; PG phase transitions log
once with truthful fields, including bounded repeated unavailable warnings;
an existing scan proving both spawn sources empty resets history, while
skipped/error/stopped/partial scans do not; no added queue probe or reordered
scan; prove reset at the second empty source even if a later queue has work;
prove the stop flag checked after probes prevents false empty evidence. Drive
this proof with a real activity hint or direct existing scan, never assume a
retry timer triggers it. New denial after a proven idle gap starts waiting;
without that proof history remains. SQLite/disabled paths
(including no observation at max=0) and observer exception priority
retain behavior. Retain the existing real-PG observer/tight-row proof and run
new queue/Manager transition tests through bin/pytest-pg. For exact count/clock
sequences, substitute only the external usage sample: server-wide numbackends
can change due to other workers, so do not assert global count deltas in the
parallel suite. Do not create a second container/DSN harness or serial CI job.
Include one real child exit proof rather than replacing `_cleanup_children`
with a success stub. Any impossible red proof uses the
loud substitute rule from engineering principles 4.1, with concrete reason.

Load `.envrc` and use repository-managed tools:

```sh
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_admission.py tests/core/test_manager.py -k 'admission' -q
bin/pytest-pg -- tests/core/test_admission.py tests/core/test_manager.py -k 'admission' -q
./.venv/bin/ruff check weft/core/admission.py weft/core/manager.py weft/_constants.py tests/core/test_admission.py tests/core/test_manager.py tests/conftest.py
./.venv/bin/ruff format --check weft/core/admission.py weft/core/manager.py weft/_constants.py tests/core/test_admission.py tests/core/test_manager.py tests/conftest.py
./.venv/bin/python -m pytest
bin/pytest-pg
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
```

Planning-only gates: the last doc-test selection, git diff --check, new-plan
whitespace check and named-path inspection. Do not claim runtime tests for a
plan. Freeze source/test edits globally while tests run; cleanup all harness
resources. During implementation use existing tests also outside the -k
selection if their names omit admission; full gates remain required.

Post-deploy success signals: source backlog retained under pressure, capacity
recovery followed by ordinary dispatch, waiting/stalled/recovery transitions
matching real observations, responsive PING/STOP while blocked, no per-second
warning flood caused by changing evidence times for the same identity. Do
not claim application-success or exact PG slot guarantees.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Review and Evidence

Fresh-eyes self-review and independent plan review are required before this
plan is reported implementation-ready. Record findings/dispositions here;
review asks whether this exact bounded delta is implementable and whether it
would impair robustness, with explicit accepted limits above. Scope expansion
is an observation for the owner, not an automatic implementation task.

2026-10-06 fresh-eyes author pass: traced the existing lane cache, observer,
actual-reap branch, startup publication and logger. Corrected two ambiguities:
unknown initial usage establishes a baseline without renewing progress; public
pause survives permitted internal work and every retry clear. Removed redundant
controller logging-history state: only three episode values are needed, with
Manager extending its existing transition identity. Confirmed the rate-limiter
emits changed states immediately; elapsed values stay out of identity. Kept
startup pacing excluded because best-effort pre-LISTEN mapping is insufficient
readiness evidence. Scope is progress assessment and stall visibility, not a
hard capacity permit or application-liveness watchdog. Independent review is
completed below; no runtime/source/spec change made in this planning pass.


2026-10-06 independent different-family review: Claude, read-only invocation
through `skills/call-agent/SKILL.md`, embedded plan at the stated baseline.
Verdict PASS: implementable with confidence; no design robustness blocker.
Finding excerpts and dispositions (no scope expansion):

| ID | Reviewer finding | Disposition and evidence |
| --- | --- | --- |
| F1 (P2) | “The rate limiter decides ‘same state’ by comparing the state= argument.” Adding elapsed/idle to the shared fields dictionary makes unavailable observations emit every second. | Accepted. Separate stable `state=` identity from `log_fields=` evidence; require repeated-unavailable warning proof. |
| F2 (P2) | “A later denial, maybe hours later, then reports stalled with a very large elapsed value for what is really a new burst.” Denied rows may disappear without an open observation. | Accepted. Reset on both-source emptiness proven by an existing pending-work scan. No extra queue read, expiry-time probe, persistent cache or changed scan ordering. Unobserved gaps remain conservative. |
| F3 (P2) | “numbackends counts connections across the whole server, so under parallel bin/pytest-pg other workers’ connections change it unpredictably.” | Accepted deterministic transition tests using external stats only. Retain real observer/denied-row wiring proof. Reviewer claim that only a stubbed PG test exists is corrected by `test_postgres_admission_uses_real_connection_stats_and_retains_tight_row`; do not add another harness or serial job. |
| F4 (P3) | “_schedule_admission_retry_for_source … also blocks lanes, but without any observation.” Episode entry is ambiguous. | Accepted. Only an observed denial starts an episode; launch restoration alone does not. |
| F5 (P3) | “The plan’s decision value contains blocked lanes but doesn’t say whether these helpers move.” | Accepted. Blocked-lane mapping moves with capacity arithmetic; logging-state helper stays in Manager. No duplicate policy. |
| F6 (P3) | “The disabled path exists in two places, and the controller’s copy never runs in production.” | Accepted. Existing Manager guard is the sole disabled owner; disabled proof stays at Manager level. |
| F7 (P3) | “The backend isn’t admission config, and the connected Queue is resolved lazily.” | Accepted. Backend is an explicit evaluate argument, resolved by existing Manager I/O. |
| F8 (P3) | “Adding the same cases in test_admission.py and keeping a re-export alias in manager.py gives two copies of both the tests and the name.” | Accepted. Move the existing three owning arithmetic tests; drop the alias and duplication. |
| F9 (P3) | “Internal children … and then reaped keep refreshing the idle clock. Meanwhile numbackends sits at the limit.” | Accepted limitation with README explanation. The 180-second absolute cap bounds optimistic assessment; no filtering added. |
| N1 | “‘Epoch’ isn’t defined anywhere in the plan.” | Accepted. Use “new episode.” |
| N2 | Missing spaces before numeric timeout values. | Accepted. Corrected before spec promotion. |

Review observations retained as limits: existing wall-clock scheduling and
synchronous observation can delay phase evaluation despite monotonic budgets;
phase is computed on fresh checks rather than a new timer; unrelated clients
releasing connections count as resource progress. No additional work scheduled.
The call-agent skill worked as documented; no maintenance correction needed.


2026-10-06 scoped round-2 Claude review: PASS; all round-1 findings incorporated.
The reviewer confirmed the no-new-I/O reset is sound and conservative, not a
complete idle-gap detector. Accepted follow-up wording and verification details:

| ID | Reviewer finding | Disposition |
| --- | --- | --- |
| R2-1 (P3) | “Manager is not stopping” has to be checked after the empty probes, using `_stop_event` (once set it stays set). Stop can be set between probes, and the stopped probe then returns False. | Accepted. Explicit post-second-probe stop check and firing test. |
| R2-2 (P3) | On PostgreSQL, `_has_pending_messages` only runs inside `_activity_hint_action`, after a local or native activity hint or in fallback polling. It never runs when a plain timer deadline is reached, including the 1-second retry wake. The reset only happens if some hint arrives after the retry clears. | Accepted limitation, explicitly best-effort and hint-driven. README explains inherited episode age across unobserved idle gaps; tests use a real hint or direct existing scan, not a timer assumption. No extra probe. |
| R2-3 (P3) | The plan doesn't say whether to reset as soon as both spawn sources probe empty, or only when the whole scan returns False. A reserved queue probed later can still return True. Both are safe, but leaving it open lets the code and tests diverge. | Accepted. Reset immediately after the second empty source and stop check, independently of later scan results. |
| R2-4 (P4) | An episode ended by the empty-source reset logs nothing. Operators may see stalled, then later waiting, with no open between. | Accepted silent reset. README clarifies that open requires capacity evidence; no synthetic open log or additional transition history. |
| N2 follow-up | Two joined words are left: “engineering principles4.1” and “Promote during task2”. | Accepted. Corrected. |

Fresh-eyes follow-up confirmed manual waiting excludes concurrent drive under
existing watcher ownership. No new synchronization mechanism is justified.
Existing lane-identity changes can still trigger the existing logger; tests
must isolate the new duration-only log-flood risk, not silently alter that policy.

Planning verification, 2026-10-06: all six plan-metadata/spec-hygiene tests
passed using `.envrc` and the managed virtualenv. Plan links and the 232-file
index count resolve; tracked/new-file whitespace checks pass. Changes are
only this draft and its index entry; the owner's dependency updates are
preserved. No runtime implementation/tests, spec promotion or commit is part
of this planning deliverable. Implementation checklist remains unchecked.


2026-10-06 implementation started under the approved plan (class 5, strategy B).
Baseline remains d50375e265bc87363468b37ef6fa860e9d85a468. Owner dependency
changes in pyproject.toml/uv.lock are excluded from edits. Red tests precede
atomic spec/controller/Manager promotion. Shared source/test edits are frozen
during every verification run. Implementation and review evidence follows.

Baseline red, 2026-10-06: nine new Manager cases reached actual source denial
and failed on missing wait_phase in operational evidence (exit 1). The new
pure-policy module collection failed because the planned module does not yet
exist (exit 1); this is new API absence, not a claimed budget-behavior proof.
The frozen clock also exposed test cleanup deadlines; restore real monotonic
time before Manager finalization rather than changing product cleanup.
Promotion strategy B: exact reviewed MA-1.8/MANAGER.18 text applied to specs;
promotion baseline is HEAD d50375e265bc87363468b37ef6fa860e9d85a468 plus the
current uncommitted two-spec delta. Runtime implementation follows against
these promoted files, not plan-only text.

Verification-command correction: pytest-pg uses argparse positional targets;
pytest options require its `--` separator. The first targeted invocation was
rejected before provisioning (exit 2); corrected command above uses the same
wrapper, parallelism and fixtures. No runner/config change.


Implementation review 1 (Claude, read-only): no blocker. Findings/dispositions:

| ID | Finding | Disposition |
| --- | --- | --- |
| A1 (P3) | An internal-lane evaluation alone can start or extend an episode while no public request is waiting. | Accepted existing spec boundary: history tracks either blocked lane, not per-request wait. README calls this episode age; no request-aware policy added. |
| A2 (P3) | The pg_admission_setup fixture freezes time.monotonic for the whole process, not just Manager's clock. Broker/driver deadline loops could hang while frozen. | Accepted and repaired in tests only: replace Manager's time module binding with a narrow namespace, keeping broker/driver clocks real, and restore the binding before cleanup. No production seam. |
| A3 (P4) | Decision-lane cases overlap arithmetic tests, and SQLite rows overlap the separate SQLite-assessment test. | Removed duplicate SQLite rows. Retained arithmetic owner assertions and controller decision cases because the latter prove blocked-lane/phase integration, a distinct wiring risk. |
| A4 (P4) | Promoted spec text runs straight into the existing exception sentence on a long line. | Accepted and rewrapped without semantic change. |

Full SQLite gate 1: 5276 passed, 30 skipped, one failure in the owning explicit
core-test classification gate. Fixed the test inventory owner by adding only
new tests/core/test_admission.py to tests/conftest.py _SHARED_MODULES; removed
its redundant file-level mark. This is required backend routing, not an
allowlist/suppression, parallelism change or relaxed test. Plan file list now
names the central entry. No runtime change from this finding.
Targeted SQLite/PG and mypy (440 sources), repository-wide ruff, changed-file
formatting, and DOM-15 fixture checks had passed before those test-only fixes.


Sensitivity checks, 2026-10-06, canonical managed pytest/default parallelism:

| Control | Intended observed failure |
| --- | --- |
| Baseline HEAD Manager with final test clock/fixtures | All 10 new Manager cases fail missing wait_phase after real denial; zero setup errors. |
| Force blocked PG assessments always waiting | Six policy cases fail on required stalled phase (idle equality, total cap, unknown baseline, oscillation and mixed lanes). |
| Omit actual Manager reap notification | Real process reap test loses required waiting transition after confirmed exit. |
| Omit existing-scan episode reset | Empty and later-reserved cases retain stalled/elapsed40 instead of fresh waiting/elapsed0. |
| Omit post-probe stop guard | Stopped case resets to waiting rather than retaining stalled history. |
| Put duration fields in logger identity | Repeated unavailable case emits three warnings instead of one. |

All controls produced exit 1 with zero setup errors, then original source was
restored byte-for-byte. No control committed. Restored candidate targeted
SQLite/classification selection passed; full mypy (440 sources), repository
ruff and six changed Python files' format checks passed again. These results
prove the production hooks, not a test-local model. Final full gates follow.


Final scoped Claude review: PASS on A2/A3/A4 and the central classification
entry; no new blocker. Minor wrapping observations accepted for closeout.
The future mixed-clock warning is retained: pg_admission_setup is for admission
and PING, not STOP/drain paths that share deadlines with BaseTask. Such tests
must use compatible owner clocks. No new production seam or synchronization.
The test-audit and call-agent skills worked as documented; no skill edits needed.

Full SQLite rerun: managed default pytest, 5275 passed, 30 backend-specific
skips, zero failures/errors, 263.78s. JUnit contains 5305 cases. No test-time
source edits. Full PG and final doc/traceability gates remain pending below.
This is an uncommitted checkpoint; plan/index stay draft until authorized
committed closeout. No dependency, version, CI, release or deployment changes.


Final full PG gate, 2026-10-06: `bin/pytest-pg -- tests --junitxml=<temporary report>`
under .envrc, normal wrapper/shared markers/parallelism. 5128 passed, 25
backend/provider-specific skips, zero failures/errors, 309.65s; JUnit contains
5153 cases. Wrapper exited 0 and cleaned its temporary PostgreSQL container.
No source/test edits in flight. SQLite gate remains 5275 passed/30 skipped.

Closeout fresh-eyes pass: verified unchanged arithmetic, no alias/duplicate
policy, exact observer catch lists, actual-reap-only notification, pure retry
clears, post-probe stop guard, no extra reads and stable logger identity.
Corrected one operator instruction after inspecting serve_log_level: level
alone does not activate a detached Manager's stream. README/rollout now name
foreground `weft manager serve --level info`; logging activation is unchanged.
Also made blocked-capacity episode age explicit even without a queued request
in that lane (accepted A1), rather than claiming request waiting time.

Tasks 1/2 and task 3's implementation, docs, review and verification portions
are satisfied. Only committed closeout remains outside authorization. Keep
Status and index draft together; no unauthorized commit/push/release. Current
runtime tests and review are complete; final documentation gates follow.


Final documentation/traceability checks passed from current state: six
metadata/spec-hygiene cases, DOM-15 fixture checker, tracked/new-file whitespace,
all plan links, 232-plan index count, and reciprocal module/spec/plan references.
The old Manager arithmetic alias is absent. Repository-wide ruff and all six
changed Python files' format checks pass. Docker inspection confirms the final
PG test container was removed. HEAD remains d50375e2 (Release 0.9.107).
The owner's pyproject.toml/uv.lock diff sizes remain 4/4 and 659/606 lines,
matching preflight; neither dependency file was edited for this slice.

Uncommitted implementation files:
- Runtime: weft/core/admission.py, weft/core/manager.py, weft/_constants.py.
- Tests: tests/core/test_admission.py, tests/core/test_manager.py,
  tests/conftest.py (one shared-module registration).
- Docs: docs/specifications/03-Manager_Architecture.md,
  docs/specifications/07-System_Invariants.md, README.md, CHANGELOG.md,
  docs/lessons.md (clock custody correction), this plan and docs/plans/README.md.

Residual limits are unchanged from the accepted scope: startup connection
accounting is still soft; resource-drain evidence is not application progress;
unobserved gaps can retain episode age; stalled diagnostics preserve automatic
capacity recovery and queued intent. No ops deployment or live-incident recovery
claim. Implementation is verified, with committed closeout pending authorization.


Owner amendment, 2026-10-06: increase the absolute assessment cap from 120
to 180 seconds. Keep the 30-second idle budget, progress/reset rules and
continued retries unchanged. Update the constant, existing boundary tests,
README, changelog and governing MA-1.8/MANAGER.18 text; verify the changed
policy tests and existing Manager admission integration. Earlier full-suite
and independent review evidence predates this numeric amendment. Changes
remain uncommitted.

Amendment verification: restored 180-second candidate passed the normal
SQLite admission selection plus plan/spec hygiene (55 passed, 3 PG-only
skips; /tmp/weft-admission-180-sqlite.xml). The canonical PG admission
selection passed (41 passed, 11 SQLite-only skips;
/tmp/weft-admission-180-pg.xml). Two revised progress boundary tests both
failed their intended waiting-versus-stalled assertions with the old
120-second constant, then passed after byte-exact restoration to 180.
Focused independent read-only review passed with no defects. Ruff lint and
format checks, DOM-15 fixtures and whitespace checks passed. Full suites
and mypy were not repeated for this constant-only runtime amendment; prior
evidence remains recorded above. The 30-second idle rule is unchanged.
