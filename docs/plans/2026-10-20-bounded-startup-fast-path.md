# Bounded Startup No-Wait Fast Path

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [QUEUE.7], [OBS.6], [MANAGER.11], [MANAGER.18]
Superseded by: none

Class: 5. Implementation with spec revision. Hardening applies: process admission,
startup feedback, reactor observation and recovery custody cross the durable spine.
This document plans implementation; it does not authorize a release or deployment.

## Goal

Overlap a small number of ordinary PostgreSQL startups while measured connection
usage is below half of ordinary usable capacity. Keep a fresh resource check per
launch and return to serialized startup at higher pressure. This is a no-wait
path through the existing scheduler, not a no-check path or another spawn lane.

## Source Documents and Spec Baseline

- Spec and implementation baseline: `c3ec8095a0e538870dcd762a8d9ad93f0ba6dba8`
  (release 0.9.115). The queue-reactivation correction and phase tracing are
  already present there; this plan extends that behavior, not the older failure.
- Governing files and sections: the metadata references above. Admission currently
  refreshes usage after each ordinary startup becomes observable; startup is scalar.
- [Program theory](../program-theory.md), especially [ALT-WT-001]: do not restore
  guessed connection charges, child lanes, lifetime debt or a family cap.
- [Planning](../agent-context/runbooks/writing-plans.md),
  [hardening](../agent-context/runbooks/hardening-plans.md),
  [review](../agent-context/runbooks/review-loops-and-agent-bootstrap.md),
  [testing](../agent-context/runbooks/testing-patterns.md), and
  `skills/test-audit/SKILL.md` govern implementation and verification.
- The queue-reactivation correction remains a prerequisite: a released
  startup slot reactivates known public work immediately through normal guards.
  Trace instrumentation and that correction are not implemented by this plan.
- Promotion baseline: c3ec8095 plus the promoted spec diff, SHA256 by file:
  `03-Manager_Architecture.md` = `593f211eb866eced227f07e732660a9ce28654190cad2e7e925efa4e9a797524`;
  `05-Message_Flow_and_State.md` = `40bd958f645f74b95227e6a0d457410506f307471e738e1efac24496a52c55e4`;
  `07-System_Invariants.md` = `0736ab3cbe230e894914256a1c45a67a92f484dc8dfca8f24edf4dbc7b21a758`.

## Evidence and Sizing

Historical October 20 ops observation: approximately 65 connections used against
an ordinary ceiling of approximately 270; parent start spacing 2.28 seconds;
parent execution around 21 seconds. Each enrichment requires parent and LLM
child launches. The decomposition was about 0.679 seconds of startup feedback
and 0.454 seconds of other inter-launch time per launch. These are measurements
from the preceding investigation, not fresh health claims or precise forecasts.

Local PostgreSQL trace evidence from the queue-reactivation investigation:
`weft-launch-phases-gb6psphk.jsonl` (20 parents),
`weft-launch-phases-xctmgbb7.jsonl` (12-parent control),
`weft-launch-phases-cag2ajf4.jsonl` (12-parent counterfactual), and
`weft-launch-phases-_hir8nlp.jsonl` (12 parents with actual reactivation fix).
These temporary files were collected under the local macOS temporary directory,
not production. The last run completed 24 launches and recorded 22 startup
handoffs, each with the public queue active afterward; its mean parent start
gap was 0.511 seconds. They establish that missed readiness was real and that
local startup cost differs from ops; do not extrapolate their throughput to ops.
Preserve a compact numeric account here rather than make temporary files a
future implementation dependency. Reproduce with the existing family workload.

Let C be the applicable PostgreSQL ceiling already resolved by admission, R its
existing reserve, L = `AdmissionDecision.capacity["public_limit"]`, and U fresh
coherent observed usage. L is already max(0, C - R); do not calculate a second
reserve. C retains the configured `WEFT_ADMISSION_MAX_CONNECTIONS` override or
automatic backend capacity, as resolved by existing admission.
Choose H = floor(L / 2). For C=300 and R=30, L=270 and H=135.
"Half of availability" means half the fixed usable ceiling, not half of the
remaining headroom recomputed as usage grows.

Choose W=4 unfinished ordinary startups per Manager, including a selected
launch still in the worker lane. Four permits overlap across the measured
~679 ms feedback interval without attempting to fill the entire 135-connection
headroom at once. It bounds the incremental exposure to three additional
startups relative to current serialization. It does NOT mean four connections.
At the threshold there is about 135 connections of headroom, or about 34 per
unfinished startup if evenly divided; this is a sizing illustration, not an
observed or guaranteed per-task maximum. There is no measured optimum for W.
Use four as a conservative initial policy, not an adaptive tuning project.
If serialized launch throughput above H is lower than the task completion rate,
usage can settle around H rather than rise toward L. That is the deliberate
headroom/throughput tradeoff of the owner's half-capacity starting point, not a
claim that this policy maximizes use of all 270 connections.

Keep H's fraction and W as named internal constants in `weft/_constants.py`,
not new environment settings. If H<W, retain serialization rather than enabling
the fast path on a tiny ceiling. SQLite retains its current startup behavior.
For PostgreSQL, with N unfinished ordinary startups and the existing resource
decision already allowed, the startup decision is exactly:
`N == 0 or (H >= W and U < H and N < W)`.
Other guards (single-flight worker, one launch per turn, leadership and durable
custody) remain necessary; this expression cannot override them.

## Current Structure and Ownership

`weft/core/manager.py` owns exact selection, process-launch worker receipts,
startup feedback, state checkpointing and notification interest. The methods
`_checkpoint_admission_launch`, `_checkpoint_spawned_admission`,
`_advance_launch_custody`, `_activity_wait_queues`, `_has_pending_observation`,
`_recover_scheduler_paths`, `_admission_allows_queue` and `_process_queue_message`
are the relevant path. `_build_tid_state_payload` serializes scheduler evidence.
`weft/core/admission.py::AdmissionController.evaluate` owns measured resource
decisions and drain-wait diagnostics. Do not conflate a full startup window with
a resource-exhaustion wait or reset those clocks to obtain more launches.

The launch worker remains single-flight and broker-free. Its short OS-launch
operation can finish before earlier child startup feedback. Overlap those
startup intervals, not worker threads or launches in one reactor turn.
`weft/core/tasks/multiqueue_watcher.py` already accepts notification-only queues
through the Manager override. Reuse its waiter replacement protocol.

Read [MA-1.8], [MF-5], [MF-6], [MANAGER.11] and these functions before editing.
Implementer comprehension answers belong in the execution handoff:
1. What does startup feedback prove? Startup observation or the existing bounded
   fallback, not lifetime maximum connection use and not permission to ACK custody.
2. Can a spare startup slot override missing durable process evidence? No.
   Failed checkpoints and ambiguous launches still gate ordinary dispatch.

## Invariants, Risks and Rollback

- Preserve fresh C/R/U admission for every ordinary launch, unknown-observation
  denial, internal reserve policy, family ordering and no charge for queued work.
- No per-task connection forecasts, public child lane, running-family cap,
  new broker helper thread, broker schema or new persistence owner.
- Preserve exact TID, immutable resolved TaskSpec/spec/io, forward lifecycle,
  spawn process isolation and exact reservation handoff. No missing-evidence replay.
- Pre-launch identity and post-launch scoped process receipt remain durable before
  another ordinary dispatch. One unresolved checkpoint failure blocks both modes.
- Window membership starts before process creation and counts an active launch
  once. Feedback/fallback/failure/exit removes only its own TID. Later feedback
  must not overwrite or erase an earlier startup. Durable custody is separate.
- At U>=H with any unfinished startup, wait for those startups to settle before
  the next serial launch. At U<H, a fresh sample can reopen the bounded window.
  No policy-mode hysteresis, new timer, or batch admission based on one sample.
  Remember a negative startup-readiness decision for the current membership until
  a membership/worker-evidence change or the existing admission/reconciliation
  recheck deadline. Both pending-work and dispatch paths use that readiness hint
  to avoid hot loops. It is not cached positive admission authority. A due recheck
  may sample again and reopen the window even if membership has not changed.
- Keep controls, worker receipt handling, cleanup and all startup observations
  responsive while either resource admission or the startup window is blocked.
- This is a Manager-local bounded speculation window, not global atomic admission.
  Multiple Managers can each have W startups, and external clients or running tasks
  can grow connection use. The change does not promise zero connection rejection
  for arbitrary tasks. A threshold is headroom, not a reservation or hard bound.
  The bound applies to starts authorized by one Manager incarnation. Bootstrap
  keeps the existing resident-based recovery and fresh resource checks; it does
  not restore a predecessor's local startup window or trust its diagnostic list.
  A restart can therefore overlap old surviving startups with the new window.
  No cross-incarnation window bound is claimed. Missing process receipts still
  block ordinary dispatch under the existing ambiguity rule.
- The existing one-second missing-publication fallback remains per TID. A fallback
  releases startup feedback after retaining live scoped evidence and a successful
  checkpoint plus fresh usage. Such release is not proof of observed connections.
- Rollback: stop admission through normal Manager shutdown, then restore serial
  runtime policy. Keep resident evidence readable and do not purge reservations.
  There is no broker data migration or destructive step. An older executable is
  not the first rollback tool until its resident-recovery handling is verified.

## Proposed Spec Delta

Promotion strategy A: requirement text before new implementation claims. The
exact approved text below is promoted first. Keep existing section mappings for
unchanged behavior; add new helper mappings with reciprocal code in its slice.
Do not reclassify an active spec or treat this plan as runtime authority.

### [MA-1.8] and [MANAGER.18]: replace the startup sequencing sentence

Replace only the sentence beginning "A Manager refreshes actual usage between
ordinary launches" and ending "existing failure/reap handling." in both
`docs/specifications/03-Manager_Architecture.md` and
`docs/specifications/07-System_Invariants.md` with this text:

> Every ordinary PostgreSQL launch requires a fresh coherent usage observation
> and the existing measured admission decision. Let L=max(0,C-R), H=floor(L/2),
> and W=4. If H>=W and observed usage U<H, a Manager may have up to W unfinished
> ordinary startups. Otherwise it waits until no ordinary startup is unfinished
> before authorizing one launch. Unknown usage denies dispatch. Window occupancy
> counts selected launch-worker requests and processes awaiting startup feedback
> once per TID, starting before process creation; it does not count queued work
> or running tasks whose startup feedback has settled. SQLite retains serialized
> startup. The window bounds unobserved launch exposure, not connection consumption;
> it adds no per-task resource charge, family cap, or global admission atomicity.
> The window is local to one Manager incarnation. Surviving tasks adopted after
> restart remain subject to existing resident/custody recovery and fresh measured
> admission, not reconstruction into the new Manager's local startup window.
> Startup settles independently for each TID when its wrapper becomes observable
> in `weft.state.tasks.<tid>` or attributed broker usage, or through existing
> failure/reap handling or the bounded missing-publication fallback below.
> A negative startup-readiness decision suppresses ordinary source readiness until
> startup membership or launch-worker evidence changes, or the existing admission
> recheck deadline arrives. A due recheck can reopen the window with a fresh usage
> observation. Positive admission is never cached across launches. Startup blocking
> does not start or reset the resource-exhaustion drain-wait episode.

Preserve the remainder of each paragraph verbatim: missing-publication fallback,
scoped evidence, checkpoint guard, reservation/ACK separation, ambiguous launch
handling where present, future-use caveat, reactor responsiveness and classification
visibility. Singular references to "the inter-launch gate" in those paragraphs
become "that TID's startup feedback"; clearing one TID must not clear the others.
The denominator for resource admission and existing reserve are unchanged.
Replace the separate reactivation paragraph in [MA-1.8], starting "A startup
handoff or ready fallback" and ending "when it is empty.", with the following
single paragraph (do not duplicate it in the notification paragraph):

> Startup settlement and a successful launch-worker receipt with room remaining
> in the startup window reactivate the ordinary public source. The next reactor
> turn reevaluates known work through normal measured admission and custody gates
> without waiting for inactive-source rediscovery. The normal pending check makes
> the source idle again when empty. A negative startup-readiness decision suppresses
> unchanged blocked backlog as ordinary wait activity.

### [MA-1.8]: replace notification-interest paragraph

Replace the paragraph beginning "Positive startup publication wakes the existing
reactor" and ending "existing data-version/polling mechanism." with:

> Each unfinished ordinary startup has one transient notification-only interest
> in `weft.state.tasks.<tid>` once its launch-worker receipt is available. There
> are at most W outstanding interests (one on SQLite).
> These queues participate in the existing activity waiter, not delivery: no
> handler, consumption, reservation or task-state mutation is added. Install
> interest before reading and retain a strict publication cursor per TID. A new
> publication returns control even if no delivery queue is actionable. Validate
> exact snapshot identity before handoff or acknowledgement. Rejected evidence
> advances only its own cursor and retains its interest without unchanged-row
> spinning. Reconcile ready TIDs independently, including out-of-order publication;
> do not wait for an older TID or the periodic reconciliation tick. The existing
> one-second absence fallback applies independently to each startup. Feedback,
> positive exit and failure release only their own interest; cleanup closes all
> owned interests. Native waiter
> replacement registers the candidate before displacing and closing the old waiter,
> under existing owner/stop serialization. The process-launch worker stays
> broker-free; no helper reader thread or historical handles are retained.
> PostgreSQL reuses SimpleBroker's shared process-local listener; SQLite keeps
> its existing data-version/polling mechanism.

This preserves subscription order, cursor validation, wake-without-delivery,
rejection handling, lifecycle ownership and backend semantics from the old paragraph.

### [QUEUE.7]: observation cardinality

In `docs/specifications/07-System_Invariants.md` [QUEUE.7], replace only:

> The Manager observes only the one task-state queue awaiting startup feedback
> under [MA-1.8].

with:

> The Manager observes each task-state queue awaiting its startup feedback,
> with active notification interests bounded by the [MA-1.8] startup window.

Preserve registration-before-read, strict cursors, owner/stop serialization,
candidate-before-close, reactor-owned reads and no historical handles in the
remaining paragraph. Existing singular "startup gate" terminology in
`docs/specifications/02-TaskSpec.md` and the persistent-task paragraph of [MA-1.8]
can describe the individual TID's gate; no unrelated TaskSpec edit is needed.

### [MF-5] and [OBS.6]: checkpoint field

In `docs/specifications/05-Message_Flow_and_State.md` [MF-5] and the matching
paragraph in `docs/specifications/07-System_Invariants.md` [OBS.6], replace
"nullable `startup_feedback_tid`" with "`startup_feedback_tids` (a list of
unfinished ordinary startup TIDs)" and "the current inter-launch startup gate"
with "the current bounded startup-feedback membership". Replace the sentence
"Gate release publishes the updated checkpoint before subsequent ordinary dispatch."
with:

> Startup-feedback membership is diagnostic and is forward-written in the existing
> Manager snapshots. It does not authorize admission, acknowledgement or replay.
> Resident identity, process receipts and exact request custody retain their existing
> durable publication and recovery requirements. Bootstrap uses those resident
> records and fresh resource observation, not the diagnostic membership list.

The runtime field is forward-written only and is not read by recovery. Existing
adoption reads resident evidence rather than trusting the old scalar; keep that
recovery path, not a scalar-to-list shim or a new startup reconstruction barrier.
The local window starts empty on a new Manager incarnation; unresolved pre-launch
custody still gates dispatch. Verify resident recovery with several surviving
launches without requiring this diagnostic field. Preserve the rest of [MF-5],
including finite ancestry and LivenessMonitor ownership. Both duplicated checkpoint
descriptions must change together.

## Tasks

1. [x] **Spec-promotion slice.** Read the references and comprehension questions;
   apply the reviewed delta to the three spec files. Add plan backlinks and record
   the promotion identifier. Audit the replaced clauses against the baseline and
   reconcile intervening spec edits before applying. Stop if authority or reserve
   semantics change beyond the delta. Verify doc gates below.
2. [x] **Red regression and policy boundary.** Read `skills/test-audit/SKILL.md`.
   Extend `tests/core/test_manager_family_maintenance.py` using its real startup
   publication process and broker fixture; hold first startup publication and
   prove a second eligible request reaches process launch under low PG usage.
   Record RED under `bin/pytest-pg`, not the default SQLite run. Add or retain
   the corresponding SQLite case proving unchanged serialization. The fixture's
   SQLite `WEFT_ADMISSION_MAX_CONNECTIONS=0` must not enable this PG-only feature.
   Existing serialization must fail the PG overlap assertion before implementation. Reuse
   `tests/core/test_admission.py` for pure independent boundary literals if policy
   extraction is needed, not a new policy framework. No fabricated queue/process
   implementation or sleep-based throughput deadline. Stop if core behavior has
   to be mocked to obtain the signal.
3. [x] **Core startup window and observation.** In `weft/_constants.py` add only
   the internal half-capacity fraction and W constants. In `weft/core/manager.py`
   replace scalar feedback state with an insertion-ordered bounded per-TID record
   holding current notification handle/cursor/pending data. Reuse residents for
   launch time, identity and custody instead of duplicating those facts. Count
   worker-selected requests before process creation; retain single worker flight
   and one launch per reactor turn. Apply the window decision using the same fresh
   admission sample, after normal admission permits the lane. Do not put startup
   blocking into resource drain-wait accounting. Suppress a startup-blocked source
   in both `_queue_has_pending` and dispatch with a shared negative-readiness hint
   keyed to window membership/worker evidence and the existing recheck deadline.
   Clear it on those changes or deadline expiry, never cache a positive permit.
   Reuse `_admission_retry_after_ns` and the `_clear_admission_retry` lifecycle,
   clearing the startup-only negative hint on settlement too. No keyed cache or
   new deadline owner is needed. Keep a startup-only readiness reason separate
   from resource-denial diagnostics: a full window must not set `admission_blocked`
   or start the AdmissionController's resource-drain clock merely by being full.
   Wake from startup publication, worker/exit evidence or that existing deadline,
   not an idle polling loop. A successful worker receipt with N<W reactivates the
   source even though the TID merely moved from worker to feedback membership.
   That receipt turn has already consumed the one-launch-per-turn allowance:
   schedule the next turn through existing `_strategy.notify_activity()` after
   reactivation when the bounded window can continue. Marking the queue active
   alone is not a wake. Do not rely on the worker hint already consumed by the
   preceding wait, a new broker notification, or a zero-time polling loop.
   Update each scalar use, including
   tracing, wait deadlines, reaping, shutdown, snapshot publication and recovery.
   Preserve notification-before-read and exact durable handoff. Each existing
   evidence-refresh pass visits every local window member in addition to its
   resident round-robin batch, so the fallback is not delayed behind old residents.
   Reuse one coherent by-owner usage sample per reconciliation pass when needed,
   not one query per member. Admission for the next launch still samples freshly.
   When a slot opens, reactivate known work. Keep existing resident bootstrap and
   global custody-failure blocking; do not reconstruct local startup membership
   from snapshots. Stop if this needs
   new worker concurrency, broker APIs, persistence or guessed connection charges.
4. [x] **Contract proof and closeout.** Extend the existing files listed below,
   preserving working admission/custody behavior assertions. Mechanical conversion
   of assertions on the old private scalar to the new representation is expected;
   do not weaken the original behavior under test. Include the checkpoint assertion
   in `tests/system/test_family_recovery.py` and the snapshot fixture in
   `tests/tasks/test_liveness_monitor.py` in the reader/writer audit. Retain any
   deliberately older resident fixture where it proves unchanged recovery; do
   not add a migration shim. Update [MA-1.8]/[MANAGER.18] implementation mappings
   and reciprocal code references together; update `CHANGELOG.md` with measured
   policy, not performance promises. Run the final gates, independent completed-work
   review and a disposable PG workload. Record commands/results and deviations;
   only then mark this plan and index completed. Release/deploy remain separately
   authorized actions, not a task here.

## Regression Tests

Use `broker_env`, real queues/processes, the existing waiting-publication fixture,
`tests/helpers/reactor_driver.py` and `tests/helpers/weft_harness.py`. Use explicit
process events to hold publication and establish producer closure for negative
assertions. Do not mock admission reads for the PG integration proof. Pure
threshold tests may supply numeric observations to the actual policy decision.
PG cases whose verdict depends on global usage must use the existing
`tests/helpers/pytest_pg_runtime.py::isolated_postgres_server` fixture. Establish
that fixture before constructing their broker target, harness or Manager; use
explicit fixture dependencies rather than sibling-argument order. Per-schema
isolation does not isolate server-wide connections from the other pytest workers.
Keep normal worker concurrency and reuse this physical-isolation lifecycle rather
than adding a test-server implementation or serializing the suite.
For the window-bound regression, hold each real child before its first attributed
broker connection AND task-state publication. Holding only its task body or
snapshot is insufficient: attributed usage can legitimately settle startup first.
Reuse `_publish_waiting_task`'s pre-construction event fence. Hold periodic fallback
with the existing `_admission_refresh_after` test hold. Immediate worker-receipt
reconciliation covers only that just-launched TID, whose launched_ns is recorded
after the worker's process-start operation. Exercise fallback separately by
backdating the resident's existing launched_ns and enabling refresh. Expire the
existing `_admission_retry_after_ns` to test due retries. Do not add a production
clock seam, patch a shared stdlib clock, or require four processes to start within
one second.

- In `tests/core/test_manager_family_maintenance.py`: below H, a second startup
  launches before the first publishes; W starts can be pending, W+1 remains in its
  source, and a later-started task publishing first releases exactly one slot.
  Drive enough complete turns/worker receipts to make the negative assertion
  meaningful. Reverting overlap must fail the second-launch assertion; removing
  the bound must fail the W+1 assertion. Recover all results after releasing events.
  Drive real waits between turns so receipt-to-next-launch progress cannot pass
  merely because a tight manual `process_once()` loop skips the wait boundary.
  Also hold a startup at U>=H, prove the denial path was reached, then drive a
  fixed number of complete turns with no new event/deadline. Usage-query count
  must not grow once per turn. Release evidence or expire the existing retry deadline
  and prove progress. Use existing trace timing events or an observer-only query
  counter; do not replace the actual resource decision or database queries.
- In `tests/core/test_admission.py` or the owning Manager test: independent values
  C=300,R=30 give H=135; U=134 permits overlap, U=135 does not. N=3 versus N=4
  straddles W. Ordinary U>=270 still denies; unknown U/C still denies; small L
  uses serial behavior. Include transition from low to high usage with pending
  startups and reopening on fresh low usage. Do not calculate expected thresholds
  through the helper under test.
- In `tests/system/test_measured_admission_pg.py`: use actual PG connections to
  cross H and the ordinary ceiling, with an applicable configured ceiling suitable
  for the fixture. Verify retained sources while startup/resource blocked, fresh
  observations before further launches, control responsiveness, and progress after
  connection release. Reuse that file's `_pressure` helper and isolated server
  fixture; `tests/core/test_connection_admission.py` supplies pure boundary-test
  examples, not live pressure helpers. Do not build a connection pool or fake
  stats service.
- Extend existing checkpoint/notification cases in
  `tests/core/test_manager_family_maintenance.py`, `tests/core/test_manager.py`
  and `tests/core/test_admission_recovery.py`:
  out-of-order and rejected publications, fallback for one TID while others wait,
  checkpoint failure denying both modes, launch failure and exit releasing only
  their own slots, and unchanged successor adoption with multiple surviving
  residents independent of diagnostic window membership.
  Assert exact reservation custody/no duplicate launch, not merely list contents.
  Keep normal SQLite serialization and internal reserve tests firing. A successful
  fallback alone must not ACK a reserved request.
- Preserve the known-queue reactivation regression. Cover slot release under
  backlog with periodic rediscovery held off; empty queues must still become idle.

Do not add version-pinning tests, tests for plan prose/release procedure, brittle
absolute millisecond assertions, exhaustive permutations, or a new benchmark suite.
When completion evidence is asserted, use existing result grace/wait helpers.

## Verification and Runtime Acceptance

Before commands, source the repo environment. When entered from mm-governance,
unset its inherited `DJANGO_SETTINGS_MODULE` for Weft test commands. Preserve
normal pytest concurrency (12 workers); do not edit during a live test run.

Per-slice and plan-only checks:
```bash
env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
bin/check-doc-paths
bin/check-dom15-fixtures
git diff --check
```

Implementation final gates (run after the last code/spec edit):
```bash
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/manager.py weft/core/admission.py weft/_constants.py tests/core/test_manager_family_maintenance.py tests/core/test_manager.py tests/core/test_admission.py tests/core/test_admission_recovery.py tests/system/test_measured_admission_pg.py tests/system/test_family_recovery.py tests/tasks/test_liveness_monitor.py
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest -m ''
env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python bin/pytest-pg --all
bin/check-doc-paths
bin/check-dom15-fixtures
git diff --check
```
The full suites are required because startup state crosses launch, notification,
shutdown and recovery. Record environmental blockers honestly, not as passing.
Run the repository's installed traceability gate if declared by the execution
baseline, at its existing error/warning floor; no new tool or waived debt.

Disposable PG exercise: use `tests/helpers/family_workload.py` with
`WeftTestHarness`, record startup-window occupancy, launch-to-feedback spans,
parent/child starts and completions, actual connection peaks and admission denials
from existing trace/status/log paths. Compare serial and fast policy under the
same workload and normal contention, without a flaky CI timing threshold. Prove
overlap and the window bound; do not infer them from process liveness alone.

After a separately authorized deployment, watch one natural burst read-only:
pending work, actual concurrency/usage, launch gaps, resource errors, complete
artifacts/receipts and queue reconciliation. Faster starts without safe completion
are not success. High usage with safe serialized fallback is expected. If the
burst never occurs, or tasks remain active at observation end, report that limit.
Do not promise to fill all spare connections or guarantee a particular speedup.

## Independent Review and Fresh Eyes

Author family: OpenAI/Codex. Reviewer family: Anthropic/Claude, Tier 1 via
`skills/call-agent/SKILL.md`. CLI version: Claude Code 2.1.293. Liveness probe
returned `PROBE-OK`; the write-attempt probe refused the repository write, and
`probe-write-test.txt` was absent afterward. Invocation:
`claude -p "$PROMPT" --permission-mode plan --allowedTools "Read,Grep,Glob"`,
stdin `/dev/null`, timeout 540 seconds. The prompt embedded the full plan and
named the governing specs, program theory, Manager/admission and test paths.
Round 1 reviewed plan SHA256
`688e2e6d00e5ce7933f6ec7b06f2920771dd417a331e69a00c0c5606ef97ae95`
and live code; verdict BLOCKED. The findings below were verified against the
named paths and amended in the operative text. Round 2 reviewed plan SHA256
`a0555479d61bf2106db383fd9e6bf568c6ee46436134f366f9299e20686bc3af`
with the same read-only invocation; verdict "PASS on F1–F12." Its new P2
finding and P3 notes are dispositioned below. The reviewer explicitly stated that
deleting the clock-seam instruction did not need another review round.
Required verdict: implementable as written and no meaningful robustness
degradation within the accepted measured-admission limits. Check correctness,
notification/recovery ownership, falsifiable tests and over-engineering. Prefer
removing work. Existing non-atomic admission and arbitrary future task resource
growth are accepted limits, not invitations to add a distributed reservation system.

| ID | Finding | Disposition |
| --- | --- | --- |
| F1 | P1: "Hot loop at high usage." | Accept. Shared negative readiness suppresses both pending-work and dispatch until membership/worker evidence changes or the existing recheck deadline. No positive permit cache or new timer. Add a query-count regression. |
| F2 | P1: "The delta misses standing clauses that the change contradicts." | Accept. Exact [QUEUE.7] replacement, duplicated [OBS.6] field update, and one canonical reactivation paragraph. TaskSpec's per-TID gate wording remains valid. |
| F3 | P2: "The second launch would wait for periodic rediscovery." | Accept. Worker receipts reactivate the source when N<W, in addition to settlement. Post an existing local activity wake for the next turn because the receipt turn consumed its launch allowance; test through real waits. |
| F4 | P2: "Stale baseline." | Accept. Baseline is c3ec8095; prerequisite is already satisfied. Task 3 converts trace fields with other scalar uses. |
| F5 | P2: "Bootstrap rule is undefined, and the field it relies on is never read." | Accept deletion. Membership stays diagnostic, forward-written only. Keep resident-based bootstrap and custody guards; explicitly disclose the per-incarnation bound and restart overlap risk. No new startup reconstruction policy. |
| F6 | P2: "The red test can't fail on the default suite." | Accept. RED evidence comes from actual PG; SQLite must prove the existing serial behavior. |
| F7 | P3: "Fallback can arrive late." | Accept within scope. Visit each local window member on existing refresh passes instead of placing it behind the resident batch. |
| F8 | P3: "Repeated usage queries." | Accept. One coherent usage sample per window reconciliation pass, separate fresh admission per new launch. |
| F9 | P3: "Unclear terms." | Accept. Use the existing public_limit and configured override/automatic-capacity vocabulary, not a new role-ceiling rule. |
| F10 | P3: "Wording." | Accept. Interests are at most W; worker requests count in occupancy but do not yet have interests. |
| F11 | P3: "Unavoidable test edits." | Accept. Allow private-field assertion updates while preserving the tested behavior. |
| F12 | P3: "Text overstates enforcement." | Accept for the diagnostic membership field only. It supplies no durability authority; existing receipt/custody publication requirements stay intact. |
| R2-1 | P2: "the Manager-module-owned clock binding is a new test-only production seam, and nothing needs it." | Accept the simpler proof. Use existing refresh/retry holds and backdate resident launch time; no production clock seam. Updated the operative regression instructions. |
| R2-2 | P3: use existing blocked-lane/retry state and clear it on settlement. | Modify. Reuse the existing retry deadline and clear lifecycle. Keep a startup-only readiness reason so resource-exhaustion traces and drain clocks stay honest; no new keyed cache or policy state machine. |
| R2-3 | P3: private scalar consumers also occur in family recovery and LivenessMonitor tests. | Accept. Added both to the reader/writer audit and formatting gate without requiring needless fixture churn. |

Fresh-eyes author pass (2026-10-20): re-read the whole draft independently of
the writing pass. A1: a publication-only fence would not prevent attributed
connections settling startup; fixed the regression instructions to fence both
and defer the fallback without a fragile wall-clock assertion. A2: startup
blocking must suppress ordinary wait activity rather than spin on durable backlog;
fixed task 3 to retain that existing wait behavior. Residual risk: W is conservative
policy, not an empirically optimal window or hard connection guarantee. External
review and scoped PASS are recorded above. Independent implementation review remains required
after code and tests, even if plan review passes.
An additional identifier sweep found the duplicated checkpoint field under
[OBS.6], not [MANAGER.18]; the exact delta now names and updates both copies.
The post-review fresh-eyes trace of `BaseTask.run` and watcher waiting also
confirmed that a consumed worker wake cannot be reused: receipt reactivation
must post an existing local wake for the next turn. Task 3 and the regression
now state that boundary explicitly, within F3's accepted scope.
The same pass verified the existing physical-PG isolation fixture and pressure
helper ownership. Usage-dependent integration cases must use that fixture before
constructing their targets, so normal concurrent workers cannot invalidate H.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
| --- | --- | --- | --- | --- |
| 2026-10-20 | Round 1, 688e2e6d | Delta, tasks, tests | Disposition F1–F12 and author fresh-eyes corrections. | Yes; scoped Round 2 PASS, a0555479. |
| 2026-10-20 | Round 2, a0555479 | Tests and execution detail | Remove the clock seam per R2-1; reuse retry lifecycle with honest startup diagnostics; name the two additional scalar-consumer tests. Make F3's next-turn local wake and real-wait proof explicit, and name the existing physical-PG isolation fixture. No policy or custody authority changed. | No; accepted review fixes and implementation details within reviewed scope. Reviewer explicitly waived another round for R2-1. |

## Plan Authoring Verification

At plan authoring, the metadata/spec-hygiene command passed 6 tests;
`bin/check-doc-paths`, `bin/check-dom15-fixtures` and `git diff --check` passed.
Implementation verification is recorded separately below.

## Execution Evidence

2026-10-20, before runtime edits: the user authorized implementation. Comprehension
answers: startup proves observation/fallback, never lifetime capacity or ACK
authority; a free slot cannot override failed durable custody or unknown usage.
The spec-promotion slice applies the reviewed wording and preserves the fallback,
failure/reap, reservation and publication-cursor clauses around the edited spans.

The real-PG overlap regression first established one recorded process receipt,
then failed on the pre-fix runtime with:
`second startup did not progress before first feedback: received=['1792512688030818304']`.
Command: `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python bin/pytest-pg tests/core/test_manager_family_maintenance.py::test_startup_window_overlaps_but_bounds_unobserved_processes`,
normal 12 workers. Transcript: `/tmp/weft-startup-window-red-final-20261020.log`.
After implementation, the same PG case passed in 8.14s, including all five
result payloads (`/tmp/weft-startup-window-green-20261020.log`). Its default
SQLite window-one case and the policy/maintenance suites also passed. The
backend-specific window sizes are the only scenario difference; the real
pre-construction fence and exact source/reservation/result assertions are shared.

The pressure and maintenance PG slice passed 19 tests. Fault sensitivity exposed
a weakness in the window test: five immediate turns could finish before an extra
worker became visible. Added full-window actionable-readiness and selected-worker
assertions. Raising W to 5 then failed `full window remained actionable`.
Removing the startup-negative hint from the shared dispatch guard failed the
real pressure observer count (`12 == 2`). Both temporary runtime mutations were
restored; the two PG regressions then passed in 8.64s. Control transcripts:
`/tmp/weft-startup-window-bound-control-20261020.log`,
`/tmp/weft-startup-pressure-spin-control-20261020.log`, and
`/tmp/weft-startup-final-regressions-20261020.log`.

Initial full SQLite gate: 5920 passed, 49 skipped in 228.05s, normal 12 workers,
`env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest -m ''`.
This predates the completed-work review amendments below and is not the final gate.

### Completed-work review, round 1

Anthropic/Claude, Tier 1, same verified read-only CLI invocation and 540-second
bound as above. Full operative plan embedded verbatim; baseline c3ec8095 and
current touched files named. Exit 0, verdict **no blocker**, with F1/F2 required
before closeout. No tests were run by the reviewer. Findings are quoted excerpts;
the full response is retained in this session's CLI tool output.

| ID | Reviewer finding | Disposition |
| --- | --- | --- |
| I1 (F1), P2 | "freeing one slot looks the same as freeing all of them" | Accept. Assert exact startup membership and retained sibling interests after out-of-order release, in addition to exact reservation custody. |
| I2 (F2), P2 | "Several required regressions are missing." | Accept per-TID fallback, failure/exit and multi-survivor recovery proof, plus low/high/reopen with multiple unfinished startups. Preserve existing checkpoint-failure coverage: those global undurable guards run before the startup decision, so duplicating a high/low permutation adds no new correctness signal. Run the workload before closeout. The LivenessMonitor fixture at `tests/tasks/test_liveness_monitor.py:1414` deliberately retains the old diagnostic scalar; resident custody is independent of that field, and no compatibility reader is added. |
| I3 (F3), P3 | "a wait that spins can't be seen" | Accept. Add blocked-source pending and whole-manager pending assertions to the actual pressure test. |
| I4 (F4), P3 | "That call is now redundant." | Accept deletion of the leftover reactivation/comment in `_advance_launch_custody`; `_resume_startup_dispatch` is the single owner. |
| I5 (F5), P3 | "this early settle-then-re-add can't happen before this change" | Accept after tracing worker receipt order. Keep selected worker requests in membership until receipt/stale-worker reconciliation, while leaving valid durable handoff handling unchanged. |
| I6 (F6), P3 | "The predicate assertion uses unfinished=0, which returns True before capacity is read" | Accept deletion of the four redundant resource-controller cases. Existing measured pressure and Manager tests own actual resource-before-startup ordering. Keep the independent startup boundary literals. |
| I7 (F7), P3 | "has no reason comment" | Accept optional-PG-dependency comment. Decline optional helper relocation: reusing two existing test process helpers is simpler than moving fixtures for this slice. |

Reviewer observations on per-incarnation bounds, bounded retry sampling and
extra fresh checks after settlement are the plan's accepted limits, not requests
for hysteresis, cached positive permits or a second resource policy.

Expanded PG verification first failed only the new test's assumption that the
fifth member already had an installed notification handle. Interests install at
the next wait, not at receipt. The test now enters that existing setup boundary
before checking per-TID closure and retains exact sibling handle identities.
The invalid-publication phase also holds that fixture resident's absence-fallback
timestamp and restores it, so a legitimate fallback cannot race the rejection
assertion under load. No runtime clock seam was added. Re-running the complete
four-file PG slice (`test_manager_family_maintenance.py`,
`test_admission_recovery.py`, `test_measured_admission_pg.py`, and
`test_family_recovery.py`) passed 34 tests in 44.54s at normal 12 workers.
All owned tasks/results and two-survivor recovery assertions ran.

### Disposable workload comparison

Local-only command: `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python
/tmp/weft-launch-phase-probe.py --backend postgres --count 12`. The serial
control used the identical command with `--package-root
/tmp/weft-startup-serial-control.zDrl3W`, an archive of c3ec8095. Each run owned
its disposable PostgreSQL server and used the existing `family_enrichment` and
`family_leaf` workload with usage observations enabled. No production connection
or payload was involved. Compact records:

| Observation | Serial baseline | Bounded fast path |
| --- | --- | --- |
| Parent starts / parent completions | 12 / 12 | 12 / 12 |
| Child accepted / child starts / retrieved successful results | 12 / 12 / 12 | 12 / 12 / 12 |
| Launch receipts | 24 | 24 |
| Mean parent start spacing | 0.501888s | 0.243452s |
| Maximum observed startup membership | 1 | 3 |
| Sampled actual connection peak | 22 | 49 |
| Trace launch-to-observed-settlement upper-bound median / maximum | 0.2427s / 0.2789s | 0.3384s / 0.5595s |

The settlement intervals use the first post-receipt phase snapshot without that
TID, measured from OS-launch start. They are coarse upper bounds from existing
trace boundaries, not exact child-init timings. More startup overlap increased
that per-startup interval while reducing parent spacing. This finite workload
exercised overlap but only reached three unfinished startups; the fenced regression
separately proves all four and rejects a fifth. Usage is sampled, not a continuous
peak guarantee. This is one local comparison, not a statistical performance bar
or prediction for ops. Neither run emitted a resource-denial event; the dedicated
real-pressure regression, not this low-load workload, proves the pause/reopen path.
Logs: `/tmp/weft-startup-serial-workload-20261020.log` and
`/tmp/weft-startup-fast-workload-20261020.log`. Trace files end in
`weft-launch-phases-o9yrm3hz.jsonl` and `weft-launch-phases-wbywstik.jsonl` under
the local macOS temporary directory.

Fresh-eyes author implementation pass: re-read the release, receipt, retry,
observation and recovery paths against the promoted clauses. The test-bound
control weakness and notification-install assumption were corrected above.
Selected worker requests now stay counted until receipt reconciliation; clearing
one member never grants resource authority or ACKs its reservation. No additional
runtime defect found in this pass. Residual limits remain per-incarnation bounds,
non-atomic global observation, future task growth and W's unproven optimum.
Independent scoped re-review and final suites remain pending at this entry.

Completed-work round 2 used the same read-only invocation, scoped to accepted
F1–F7 and their fixes, with the amended plan embedded. Exit 0; verdict
"PASS on F1–F7." The reviewer verified the actual receipt/stale-worker ordering,
per-TID membership/cursors, pending suppression, two-parent recovery and all
seven dispositions. New N1 (P3): the sibling extension removed the former final
`not manager_launch_custody_pending(...)` assertion after the last retirement.
Accept: retire that sibling in the same test and restore the final false-branch
assertion. This preserves the old correctness proof rather than adding another
fixture or protocol. No new runtime defect or reconsideration was reported.
The reviewer's two harmless duplicate wake/clear observations and optional
refresh-batch test suggestion are non-actionable for this bounded slice.
N1 is applied in the same retirement test: after proving the live sibling keeps
custody pending, expire only its scoped process incarnation, retire it, re-read
the checkpoint and assert custody is no longer pending. The earlier post-review
SQLite run passed 5917 tests with 49 skipped in 243.36s, but predates this last
assertion. Both full backend gates are rerun from the final source/test state;
their acceptance results belong below, not inferred from that earlier run.

### Final acceptance gates

From the final source/test state after N1, normal 12 workers:

- `./.venv/bin/ruff check .`: passed.
- The ten-file `ruff format --check` command above: passed.
- The full `mypy` command above: success, 478 source files.
- `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest -m ''`:
  5917 passed, 49 skipped in 233.66s. Log:
  `/tmp/weft-startup-full-sqlite-acceptance-20261020.log`.
- `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python bin/pytest-pg --all`:
  the fresh post-diagnosis rerun passed 5868 tests, 27 skipped in 353.95s.
  Log: `/tmp/weft-startup-full-pg-acceptance-rerun-20261020.log`.
- After closeout metadata, the named plan-metadata/spec-hygiene command passed
  six tests; `bin/check-doc-paths`, `bin/check-dom15-fixtures` and
  `git diff --check` passed. Those doc gates were rerun once more after this
  evidence entry so the final document state, not only its preceding draft,
  is checked.

The first full PG acceptance run was red: 5865 passed, 27 skipped, one
`tests/cli/test_cli_queue.py::test_queue_watch_sigint_exits_cleanly` failure in
368.97s. After the real `Watching queue` readiness line and SIGINT, the CLI did
not exit within the unchanged ten-second deadline. The admission/startup
regressions passed. Preserve this counterexample:
`/tmp/weft-startup-full-pg-acceptance-20261020.log`.
The unchanged queue-watch file's complete PG slice then passed 31 tests in 5.90s;
that isolated pass did not clear the full gate.

Baseline diagnosis, not a test waiver: a disposable real-PG probe verified imports
from the unmodified c3ec8095 archive and repeated that exact CLI readiness/SIGINT
sequence. Sixty immediate and 120 delayed sequential probes were clean. At
12 concurrent diagnostic CLI processes, one of 240 probes reproduced the exact
ten-second hang (iteration 104, immediate SIGINT), with 239 clean exits.
`/tmp/weft-queue-sigint-baseline-concurrent-20261020.log` records the import and
outcomes; `/tmp/weft-sigint-concurrent-hang-58241.sample.txt` captures the blocked
main-thread lock acquisition while the PG listener remained alive. This proves
the timeout exists without the startup change, not its exact lock owner or fix.
The queue-watch command, resource scope, monitor and test paths are unchanged
against c3ec8095 (`git diff --quiet` exit 0). No deadline, test or CLI code was
changed to obtain a green result. A fresh unchanged full PG gate is required
after diagnosis, and the race remains an explicit separate issue even if it passes.

Traceability audit: both promoted checkpoint descriptions and [QUEUE.7] match;
the startup predicate, retry/readiness owner and per-pass observation functions
are mapped from [MA-1.8]/[MANAGER.18], with reciprocal module/inline spec refs.
The old scalar remains only in the deliberately older LivenessMonitor fixture.
No backstitch executable/config is present in this execution environment;
the existing spec-hygiene tests and doc-path/fixture checks are the
available repository gates, not a newly added checker or a waived warning floor.

Closeout: all four implementation slices are complete, with the promoted contract,
current regression proofs, independent scoped PASS and full backend gates above.
The unchanged-baseline SIGINT hang is an explicit separate issue, not repaired by
this feature or hidden by a skipped test. No source/test edits followed the final
acceptance runs. No release, deployment, commit or production mutation was made.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Out of Scope

mm-governance task/session changes, provider timeout tuning, production edits,
releases/deploys, a global resource reservation service, hard per-task connection
limits, adaptive control, new user config, lifetime task limits and broker changes.
