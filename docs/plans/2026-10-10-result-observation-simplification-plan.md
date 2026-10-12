# Result Observation: Canonical History and Unified Settlement

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/10-CLI_Interface.md [CLI-1.2.2]
Superseded by: none

Class: 5. Canonical history narrows the supported discovery encoding; settlement
and watcher changes cross execution and ownership boundaries. Hardening applies.

## Goal and Outcomes

Address the owner's October 10 critique of the result implementation. Preserve
accepted configuration, one consuming collector and activity-based waits. Remove
the unsupported escaped-TID search, repeated history reduction and separately
compiled settlement policies. Promoted specs describe behavior and ownership;
implementation and review evidence belongs in plans.

- [x] Reconcile the specs, inherited facade ownership and superseded plans.
- [x] Use one canonical history search with complete public pagination.
- [x] Keep incremental completion facts, rather than the whole observed log.
- [x] Let one settlement owner select row eligibility, completion and waits.
- [x] Preserve watcher liveness without converting fatal exceptions to BrokerError.
- [x] Run real-broker regression checks and independent simplicity reviews.

The attachment is the requested revision to the ongoing authorized implementation.
No commit, push, release/config change, dependency change or sibling-repository
write is included. Preserve all earlier edits outside the explicit delta.

## Source Documents and Current Owners

Read AGENTS.md and the full ordered agent-context chain, including writing-plans,
hardening-plans, review-loops-and-agent-bootstrap, runtime-and-context-patterns,
testing-patterns, adversarial-acceptance-probes and both lessons files. Apply the
test-audit skill when changing tests. Governing contracts are [MF-5] in
[Message flow](../specifications/05-Message_Flow_and_State.md), [SB-0.4] in
[Broker integration](../specifications/04-SimpleBroker_Integration.md), [PY-1/2]
in [Python surfaces](../specifications/14-Python_API_Surfaces.md), and
[CLI-1.2.2](../specifications/10-CLI_Interface.md). Read architecture, TaskSpec,
system invariants and pipeline completion before changing collection rules.

| Files to read/change | Responsibility and shared path |
| --- | --- |
| `weft/core/task_evidence.py`, `weft/_constants.py`, `tests/core/test_task_history_reader.py` | Public filtered history, exact identity, cursor and shared log evidence. |
| `weft/commands/_result_wait.py`, `result.py`, `events.py` | Registered queue ownership, acquired evidence, consuming and read-only result observations. Reuse this owner, not another collector. |
| `weft/core/queue_wait.py`, `tests/core/test_queue_wait.py` | Thread custody, native/fallback activity, latched failure and interruption. |
| `weft/commands/_task_snapshot_reducer.py`, `system.py`, `tasks.py` | Existing snapshot fold. Reuse if a retained snapshot is needed; do not invent a second snapshot reducer. |
| `weft/client/_task.py`, `_client.py`, `commands/submission.py`, custody/facade tests | Accepted configuration and session-owned spawn facade. Inspect; retain behavior. |
| `tests/commands/test_result_settlement_policy.py`, `test_consuming_result_collection.py`, `test_realtime_events.py`, `test_retained_result_history.py`, `test_result_activity_waits.py` | Existing literal settlement rules and real-producer custody, routing, grace and wake proof. |
| Specs 04/05/14; October 9 result plans; `docs/plans/README.md` | Behavioral wording, concise current owner mappings and historical execution records. |

Comprehension checks: Which output row may be claimed before task completion,
and why? What distinguishes persistent batch completion from task completion?
What wakes an indefinite wait after a watcher thread exits? Which producer
serialization can a bare-TID lookup actually discover?

## Spec Baseline

Plan type: implementation with spec revision. Diff base is
`8085962af48c2ae38b8487fc449a4b11e0471170`; the governing promoted texts at plan
start are pinned by SHA-256 in `/tmp/weft-simplification-start-hashes.json`.
The initial tracked diff is `/tmp/weft-simplification-start.patch`. Capture the
promotion diff/hash after the reviewed atomic slice, and use that as the
implementation baseline. These are review artifacts, not another contract.

| Governing file | Start SHA-256 |
| --- | --- |
| `docs/specifications/04-SimpleBroker_Integration.md` | `cf8565622c116da38ce7d3e87b805e75a8daccdcb89d19494a0f5bec5cb92724` |
| `docs/specifications/05-Message_Flow_and_State.md` | `6e441412f6aeb70c805648d746a4c73a2313f70057be179169d0c136a9ebd247` |
| `docs/specifications/14-Python_API_Surfaces.md` | `ffae7440064ab9341477c2f935c2445e55d5d65aa3a52cbbe1fabf7a7a9d7008` |

## Proposed Spec Delta

Strategy B: change the small canonical-encoding requirement and reader together
after review. Strategy D: remove status diary and qualify the existing public
search boundary without changing its queue API.

Replace the discovery paragraph in [MF-5], starting “A bare-TID operation” and
ending “replay fallback”, with:

> A bare-TID operation resolves canonical TID-default routes directly and
> discovers only missing configuration required for correct collection, such
> as explicit routes or persistent/pipeline mode. It uses SimpleBroker's public
> body filtering and advancing message-ID bounds. The supported task-log
> serialization is the production writer's JSON, whose top-level `tid` is a
> string of literal ASCII digits. Discovery searches for those literal digits;
> substring hits qualify only when decoded records match the exact top-level
> TID. Lookup paginates through its captured range without truncating history.
> Weft does not provide an alternate escaped-TID decoder or a whole-log replay
> fallback. Imported bodies with escaped TID digits are outside this discovery
> contract; this rule does not authorize rewriting imported bodies. Filtering
> limits candidate bodies fetched and decoded by Weft. Backend search work, body transfer inside
> the broker, and any indexing belong to SimpleBroker.

Remove the three October 9 `_Implementation status` paragraphs in [SB-0.4],
[MF-5] and [PY-2], and the “no compliance claim” backlink suffixes. Retain the
behavioral rules and current owner mappings; shorten/update mappings where an
owner is removed by this plan. Add this plan's backlink. The retained spawn
facade already described by [SB-0.4]/[PY-1] remains deliberately part of the
candidate; no new facade contract is proposed.

## Invariants, Couplings and Rollback

Preserve queue truth and the TaskSpec → Manager → Consumer → TaskRunner spine;
TID/spec/io immutability; task equality and public constructors; accepted TID
custody; explicit route precedence; forward-only task state; reserved policy;
spawn process isolation; runtime-only state exclusion from dumps. No new queue,
state store, observer framework, subclass or relay.

Preserve all externally visible completion rules: one-shot final fallback,
persistent batch boundaries and quiet fallback, pipeline lifecycle proof,
wrapper-lost replacement during original grace, terminal-source precedence,
claimed-only diagnostics, null-result presence, failure/timeout exit meaning,
stream framing and final nonblocking drain. Caller deadline, first terminal
observation, matching batch observation and changing nonstream output are
different facts. Do not merge them into one resettable clock.

Claim only rows eligible for delivery. Live stream delivery does not authorize
claiming ordinary pipeline or buffered stream values before qualifying proof.
Do not preclaim a whole window before yielding; competing readers may win an
exact claim. Release broker operations before yield/wait. On resumption after
a yield, inspect new durable evidence before settling. Register new routes
before reading them, and establish replacement observation before closing the
old lease. Observer failure remains fatal to the observation; cleanup does not
replace a primary failure. Manager handed-off success is not revoked.

Retained facade behavior is inherited and included in verification. Session
operations end before recovery; fork/failed close clear its cache. Do not
redesign SimpleBroker sessions, schema inspection or backend search here.

Rollback is an explicit reversal of this plan's delta, preserving prior edits.
There is no storage migration or one-way door. Canonical live log writers are
unchanged. Imported escaped-digit identities lose unsupported discovery; do
not claim compatibility with those fixtures. Existing paired Django cancellation
release prerequisites remain separate, as do admission overshoot and broker
index/setup issues.

## Ordered Slices and Verification

1. **Plan review and document reconciliation.** Record fresh-eyes review and
   a fresh Claude plan/delta review using the owner's criteria: expert Python,
   conceptual unity, Weft principles and overengineering. Supersede the earlier
   result approach and placeholder explicitly, preserving historical reviews.
   Clarify inherited facade ownership and qualify backend-transfer claims.
   Keep draft status until landing is authorized. Validate plan metadata and
   spec hygiene. Stop if reconciliation needs a new behavior beyond the exact
   delta above.
   Set the burst, spec and implementation plans of October 9 to
   `Superseded by: [Result observation simplification](./2026-10-10-result-observation-simplification-plan.md)`.
   Set the October 9 discovery placeholder to the same successor. Match all
   four index cells. Preserve their draft statuses and historical review logs.
   Replace the implementation plan's “without adopting its unrelated
   submission-facade work” with explicit inheritance of the [SB-0.4]/[PY-1]
   facade and verification. Add a historical-deferral banner before the
   placeholder's “No ... promotion is authorized” text. Rename the spec plan's
   “Current Implementation Assessment” to its dated baseline assessment.
2. **Atomic canonical discovery and watcher correction.** One literal-TID
   search, no copied broker maximum. Use the public default page size and keep
   paging until empty; retain horizon and exact decoded identity checks. Remove
   capability getattr probes. Remove escaped/mixed compatibility fixtures;
   replace their pagination proof with producer-canonical rows plus boundary
   tests for nested/nonmatching/malformed candidate bodies. Show a failing
   control for needless escaped-text candidate transfer before repair. Keep
   real queues. Ordinary thread failures catch Exception, latch and wake.
   In both _run_multi_waiter and _run_watcher, a finally block records unexpected
   observer death unless closing,
   so uncaught KeyboardInterrupt/SystemExit still reach the thread boundary and
   wake waiters without being converted into a broker cause. Do not transfer a
   thread-local SystemExit into the caller process. Constructor cleanup still
   catches BaseException and re-raises. Removing capability probes lets missing
   methods raise their native AttributeError through the command translation
   boundary. Run history/watcher tests and review the slice.
3. **Incremental evidence and one settlement owner.** Remove the four copied
   terminal fields and terminal-payload reconstruction. Extend the existing log
   fold as one frozen `LogFold(terminal, latest_live_at)` value whose `fold`
   method returns the next fold after fresh rows. Replace known_tid_evidence's
   task_events parameter with log_evidence; update every caller in tasks,
   system, result and tests to fold its acquired rows once. Do not add a third
   optional lending shape. Remove the redundant prior_live bool, deriving it
   from latest_live_at. log_evidence=None still requests history acquisition;
   an explicit LogFold is an authoritative loan, including absence.
   load_latest_taskspec_payload borrows fresh rows until it finds configuration,
   then retains that payload; it never needs old rows again.
   Raw fresh rows serve event delivery,
   not retained lifetime history. Retain one batch-boundary sequence pairing
   IDs with first-observed times. Realtime accumulates acquired rows only until
   its startup snapshot is emitted, since absent configuration may defer that
   snapshot across turns. Lend that startup loan to the existing snapshot owner,
   then clear it. Later state delivery uses fresh rows directly, removing its
   replay tuple/cursor. No new retained snapshot reducer is needed.
   Make existing _CollectedResult the named collection state machine. Its
   select_claims(rows, evidence, now) method consumes the one bounded pending
   window and selects exact rows eligible to deliver; settle(evidence, now)
   chooses result/timeout/wait. It owns mode/scope, original caller deadline,
   aggregate values/framing, first actual claimed ID, selected batch boundary
   with original observed time, initial history horizon, final-output count and
   first-final observation time, and the pending horizon's last-change time.
   Configuration and selected evidence remain in ResultMaterialization; do not
   copy them into a second facts struct. Output eligibility and the quiet/batch
   grace share this owner. Remove _ResultObservationState and its separate
   boolean compiler. Settlement carries status/error/reconciliation/wait_until
   so missing, unavailable-store and claimed diagnostics retain their shapes.
   Remove
   _collection_policy, _result_drain_boundary and _result_preflight; remove the
   second claimed-row classification and realtime's separate grace compiler.
   The survivor is _classify_result_observation's known_tid_evidence selection,
   including fresh runtime mapping. A live runtime cannot be replaced by a
   claimed-only diagnostic from a second mapping-free call. Pin this with a
   real live task, retired/old log liveness and actual claimed result residue;
   waiting must follow runtime evidence until its caller timeout or fresh proof.
   Read one pending output window after route registration/classification;
   the selector detects framing in that window, eliminating the separate
   _result_has_stream_output scan.
   Peek classification uses decode_result_payload without touching stream_buffer.
   Stateful decoding runs only after a successful exact claim (or on realtime's
   read-only copy); a lost claim cannot corrupt assembly. The collection owner
   gates output-window acquisition when no row could be selected or observed:
   unknown mode without terminal proof (consuming path only; realtime still
   observes) and nonrendering pipeline without terminal proof skip the window.
   Keep that gate with claim policy, not as another driver mode switch.
   Claim selected IDs one at a time; interpret the actual claim and stop at the
   first persistent streaming final when no
   explicit boundary exists. Never preclaim the next batch. Read-only realtime
   uses the same owner for task-scope settlement, observes complete values
   without claiming and keeps its first eligible-final observation clock while
   the same row retains qualifying output proof. Withdrawal, replacement or
   loss of that provisional proof revokes its pending clock.
   One-shot ordinary output without terminal proof is also buffered delivery:
   observe its eligible complete value first, then claim only when original
   quiet grace or caller deadline allows returning it. An observer failure or
   abandoned follow during that window must leave the ordinary value pending.
   Preserve the output-only success fallback at grace/deadline, including
   timeout zero and JSON null. Extend the existing real custody table to pin
   this repair rather than retaining the earlier S2 early-claim limitation.
   The driver does broker I/O, invokes selection, delivers, then invokes settle.
   Let the driver own whether it yielded, removing resumed_output. Preserve
   one suffix refresh after a yielded batch, as currently done. Adapt the literal policy
   table and real custody/grace/batch/realtime tests. Add one firing incremental
   fold control. Stop and re-plan if a second collector, parallel policy-input
   struct, compatibility cache or eager duplicate output scan
   appears. Fresh Claude review must check actual simplification, not just tests.
4. **Current-state gates and traceability.** Reconcile owner mappings and
   backlinks. Run focused then full SQLite/default suites, relevant PostgreSQL
   targets through bin/pytest-pg, canonical mypy/Ruff, suppression-index check,
   git diff --check, strict backstitch and final independent Claude review.
   Source/test edits freeze while runners are active; backend-heavy jobs run
   sequentially. Do not relax tests, timeouts, skips or gates to clear a failure.

Commands start with `. ./.envrc` and use `.venv/bin/python -m pytest`,
`.venv/bin/ruff check .`, and the AGENTS.md full mypy command. Preserve default
parallelism. Reuse existing real Consumer/WeftTestHarness producers and literal
settlement cases; mock only the nondeterministic clock or external failing
waiter. Pure refactors retain their firing tests; new behavior needs a red/green
control. No new test-only production seam. Dense history default paging needs
more queries than the former requested 1,000; this is a deliberate public-API
completeness tradeoff, not an unqualified performance improvement.

## Broker Follow-ups for Separate Action

Public find_message_ids uses SQLite instr and PostgreSQL strpos; it is not an
indexed semantic TID lookup. Redis's sibling source HMGETs bodies before Python
matching. No portable zero-network-transfer promise follows. Report efficient
task identity selection, fresh facade plugin/schema setup, and SQLite watcher
payload transfer to the owner for SimpleBroker action; do not compensate with
an alternate Weft encoding, SQL query or index.
Also flag public default-page export: relying on the default until an empty
page is complete but costs an extra search for nonempty candidate ranges.

## Review Record

Initial author fresh-eyes: remove state already held in terminal evidence, but retain
distinct semantic clocks and generator resumption custody. Do not add a cached
dict to Task: result/realtime already convert once at observation start, and
snapshot APIs each convert once per separate operation. The three read-only
audits confirmed the history/spec issues and these counterarguments. Independent
Claude review was required before implementation; its disposition rounds follow.

Claude round 1 (fresh configured claude-opus-5-5): BLOCKED, unable to implement
slice 3 confidently. Artifacts: `/tmp/weft-simplification-plan-review.json` and
the full-required-reading prompt. Findings were independently checked against
the source, not treated as instructions to implement blindly.

| Finding | Disposition and revision |
| --- | --- |
| F1 (P1), ambiguous fact gathering/state ownership/verdict shapes | Accepted. Existing _CollectedResult becomes the named state machine; select_claims consumes the already-bounded rows, settle shares its evidence/clock ownership, and settlement carries diagnostics/reconciliation. No new facts struct or pure-function goal. Also repair one-shot buffered custody with the existing quiet fallback. |
| F2 (P2), third log lending shape and redundant prior_live | Accepted. Replace the classifier's row loan with one frozen LogFold; migrate every caller. No additional optional row/fact protocol. |
| F3 (P2), classification survivor unspecified | Accepted. Keep the mapping-aware acquired evidence. Live runtime outranks a second mapping-free claimed diagnostic; add a real firing control. |
| F4 (P3), unnecessary retained snapshot and overstated per-row loop change | Accepted. Startup snapshot uses first acquisition; later state uses fresh rows directly. Preserve one resumption turn after a yielded batch. |
| F5 (P3), fatal thread exception moved into caller | Accepted. Catch Exception, finally latch unexpected thread death unless closing. Preserve fatal exception at its thread boundary. Constructor interruption cleanup remains. |
| F6 (P3), default paging increases scans | Tradeoff retained and quantified in the plan, without claiming a speedup. Add public default-page export to upstream follow-ups. Zero-match queued waits still make one search. |
| F7 (P3), superseded plan filenames ambiguous | Accepted. Name all four October 9 files and matching successor/index changes. |
| F8 (P3), ephemeral baseline | Accepted. Inline three normative file hashes; temporary artifacts remain supporting evidence. |
| Spec notes: imported encoding unenforced; missing capability exception changes | Accepted. Describe unsupported escaped imports, no rewriting requirement; document native AttributeError translated at command boundary. |

Author second fresh-eyes: state count alone is not the target. Remove duplicate
interpretation and scans while preserving necessary independent facts. The
one-shot custody repair is directly in the owner's claim-versus-delivery
critique; it closes the prior S2 limitation without a new delivery protocol.
Round 2 independent verification is required before implementation.

Claude round 2: FAIL on NEW-1 (P2), retryable startup snapshot. Accepted: hold
only the startup snapshot's row loan until it is actually emitted, then clear
it. The fold and later event delivery remain incremental. Accepted P3 notes:
explicit None-versus-fold loans, fresh-only configuration lookup, no stateful
peek decoding, and a collection-owned gate on pointless output-window reads.
Round 3 is scoped to these fixes and any newly introduced defect.

Claude round 3: PASS. NEW-1 and P3-a through P3-d verified against the actual
source. Artifact: `/tmp/weft-simplification-plan-round3.json`. P3-e wording
accepted: unknown mode with terminal proof can collect; realtime can observe
unknown-mode output for its metadata refresh. The reviewed plan is approved
for implementation within the owner's existing authorization.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| CLI-1.2.2, MF-5 | Shared lifecycle/grace rules across rendering modes | Ordinary persistent rendering uses the same quiet fallback as buffered collection; inherited emit_stream exclusion removed | Presentation must not determine result completion. Real rendering control times out under the old exclusion and passes under shared policy | No new behavior contract: alignment with the promoted shared rules |

## Execution Evidence

Record current-state commands, findings and dispositions here as slices run.
No earlier pass is evidence for a subsequently changed source tree.

- Pre-repair regression run: eight intended failures in
  `/tmp/weft-simplification-red.txt`. History acquisition/classification folded
  seven producer rows 28 times; both ordinary one-shot custody cases lost the
  exact pending result during observer failure; four fatal native/fallback
  thread exits were swallowed; the escaped search transferred unrelated text.
- The canonical transfer control was repeated with actual Consumer log writers:
  `/tmp/weft-simplification-canonical-red.txt` shows the intended unrelated-body
  assertion failure. It also exposed a fixture operation held across Consumer
  cleanup. The fixture now closes bounded broker operations before producer
  cleanup. The corrected real-writer fixture was repeated against the original
  two-needle search transplanted into an isolated current-interface snapshot:
  intended RED, followed by GREEN after exact source restoration. The second
  control restored aggregate-as-one read-only fallback and also failed for the
  intended ambiguity assertion. Artifacts: `/tmp/weft-simplification-corrected-controls-red.txt`
  (two failures) and `/tmp/weft-simplification-corrected-controls-green.txt`
  (two passes). The main checkout was untouched by either control.

- Main focused verification: 212 passed, one backend skip, in
  `/tmp/weft-simplification-focused.txt`. Full intermediate suite: 5,465 passed,
  44 skips, in `/tmp/weft-simplification-all.txt`. Canonical mypy passed 450
  files; full Ruff and suppression-index checks passed. These precede the
  remaining semantic-clock repair and are not final-state gate evidence.
- Self-review found that partial persistent stream activity could age later
  wrapper-lost grace, and that framed or ambiguous output could schedule an
  ineligible quiet wake. Two literal cases and a real two-Consumer custody
  interleaving fail before repair in `/tmp/weft-simplification-clocks-red.txt`.
  The real scheduling test was then corrected to assert outside Consumer
  publication retry and repeated RED in `/tmp/weft-simplification-ambiguity-clock-red.txt`:
  waits were `[0.5, 0.25, 1.5]` rather than `[0.5, 1.75]`; rows remained pending.
  The existing owner will retain the ordinary count of its current output
  window as an eligibility fact; only eligible quiet completion contributes
  a timer. No separate policy object or wake protocol is added.

### Independent core/document slice review

Fresh configured Claude review: no blocker, two P3 issues and three nits.
Artifact: `/tmp/weft-simplification-core-review.json`. Dispositions:

| Finding | Disposition |
| --- | --- |
| F1, stale fixture/evidence text | Accepted. Corrected actual-writer RED/GREEN control is recorded above. Implementation status belongs here, not the spec. |
| F2, acquired rows instead of fold in owner mapping | Accepted. MF-5 maps LogFold.fold, explicit empty-fold loans and the actual collection owner. |
| F3, ordering guards/helper name | Retained. Explicit newest-event checks enforce the fold contract even for borrowed input; the existing bounded read helper remains clear without extra rename churn. |
| F4, global thread hook captures unrelated errors | Accepted. The test captures only its own fatal instance and forwards all unrelated errors to the original hook. |
| F5, canonical paragraph formatting | Accepted. Reflowed without changing the reviewed behavior. |

Outside-slice note: task-status watches still retain startup/snapshot rows for
snapshot projection. This inherited path is not the result collector's removed
lifetime replay, and is not expanded by this revision.

### Promoted specification baseline

The reviewed canonical rule and current owner mappings are promoted. Specs
describe behavior; execution status remains in this plan. The promotion hashes
are the baseline for the final implementation review.

| Governing file | Promoted SHA-256 |
| --- | --- |
| `docs/specifications/04-SimpleBroker_Integration.md` | `dc6403a5ffadc6224a544aab2ff86bcadd009bfd9c91ac0b701c03eec16e29ac` |
| `docs/specifications/05-Message_Flow_and_State.md` | `01db52917c3b1db82592b3496b0b5fc340ef75879b981f70e3978cc5354116d8` |
| `docs/specifications/14-Python_API_Surfaces.md` | `4cb4d7b7f80ec21e5183a87158b1be43edcd81995f6c175d90c8e0625af85be8` |

- Final focused current-state suite: 321 passed, four backend skips,
  `/tmp/weft-simplification-final-focused.txt`. Canonical mypy passes 450 files;
  full Ruff and suppression-index checks pass. The final full and PG suites
  and independent completed-work review are running/queued, not yet evidence.
- Strict backstitch still fails with 31 errors and 1,170 warnings. Three new
  advisory mapping warnings were repaired by qualifying owner paths; the rerun
  has no introduced normalized issues versus the previous candidate.
  Reports: `/tmp/weft-simplification-final-backstitch.json` and
  `/tmp/weft-simplification-final-backstitch-comparison.json`. This mandatory
  gate is not waived by the baseline comparison, so no land-ready claim follows.

### Completed-work review and bounded follow-ups

Fresh configured Claude verdict PASS, no P1/P2, four P3 findings and one nit.
Artifact: `/tmp/weft-simplification-final-review.json`. That review and the
5,468-pass/44-skip full suite (`/tmp/weft-simplification-final-all.txt`) precede
the bounded follow-ups below; final-state verification and re-review remain.

| Finding | Disposition |
| --- | --- |
| F1, realtime owner named follow_task_events | Accepted. MF-5 names iter_task_realtime_events for read-only windows and startup loans. |
| F2, ordinary persistent quiet fallback under emit_stream | Intentional alignment with CLI-1.2.2: presentation does not change lifecycle/grace policy. Add real Consumer firing coverage and a baseline control; do not restore the old presentation-only exclusion. |
| F3, snapshot helpers re-fold acquired rows | Accepted. Pass the existing LogFold through status/ping/Monitor classification. Compute it once only if absent; keep raw rows for snapshot projection, not a second classifier protocol. |
| F4, registration oracle checks classification after first read | Accepted. Observe _acquire_result_observation, the actual first durable read. |
| N1, unused select_claims live_delivery override | Accepted. Constructor owns the existing flag; remove the unused method parameter. |

Author follow-up: a now-invalid unclaimed window must not retain a clock that
shortens later wrapper-lost grace. Gate pending output clocks on current quiet
eligibility; retain actual final delivery clocks and fixed terminal/boundary
clocks. This removes an inherited persistent edge and a buffered-one-shot
regression without adding another clock or policy owner. The real producer
ambiguity regression and additional literal negative/positive clock controls
provide proof before repair.

- Follow-up RED `/tmp/weft-simplification-followup-red.txt`: three withdrawn
  window clock cases and real PING/Monitor duplicate-fold case fail as intended;
  actual delivered-final clock positive control passes. The clock and loan
  repairs are now applied, with current GREEN gates pending.
- F4 registration oracle sensitivity: isolated pre-registration acquisition
  fails with the explicit registration-order assertion; restored candidate
  passes. `/tmp/weft-simplification-registration-red.txt` and `-green.txt`.
- F2 real Consumer rendering control: restoring only the legacy ordinary
  persistent rendering exclusion causes CommandTimeoutError, as intended.
  Removing that isolated control passes rendering and buffered cases (two
  passes). `/tmp/weft-simplification-rendering-red.txt` and `-green.txt`.

- Current follow-up GREEN: 479 passed, four backend skips in
  `/tmp/weft-simplification-closeout-focused.txt`. Mypy passes all 450 files;
  full Ruff and suppression-index checks pass (`closeout-mypy.txt`,
  `closeout-ruff.txt`, `closeout-index.txt` under `/tmp/weft-simplification-`).
- Current strict backstitch: 31 errors, 1,170 warnings, zero introduced
  normalized findings. `/tmp/weft-simplification-closeout-backstitch.json` and
  its `-comparison.json` retain the mandatory non-pass. Source/tests are frozen
  while PostgreSQL and final independent review run; the final full SQLite
  rerun follows PostgreSQL sequentially.

### Second completed-work review

Fresh configured Claude re-review PASS, no P1/P2; F1-F4 and N1 verified fixed.
Artifact: `/tmp/weft-simplification-closeout-review.json`. New C1 (P3) accepted:
a replacement unclaimed row must not inherit a withdrawn row's first clock.
Reuse the existing latest_pending ID and first_final_seen fields to detect that
transition. Preserve actually delivered-final, terminal and boundary clocks.
Add sequential owner-boundary cases before repair, then rerun current gates and
obtain a scoped independent re-review. No extra state or policy owner is needed.

The first PG attempt collected no tests: the temporary probe was outside the
repository and tests/conftest.py requires an in-repo path. The second attempt
uses the prior private tests/commands/_result_acceptance_operational_probe.py
placement; remove that task-created temporary file after probes. This is a
harness invocation correction, not a test, skip, timeout or gate relaxation.

- Corrected PostgreSQL invocation: 1,007 passed, two backend skips in
  `/tmp/weft-simplification-closeout-pg-v2.txt`; physical connection cases and
  shipped CLI operational probes passed. This is pre-C1 proof; a current-state
  rerun will follow the replacement-clock repair.

- C1 regression RED: six consuming/read-only replacement cases fail on the
  current owner, while the actual delivered-final positive passes.
  `/tmp/weft-simplification-replacement-clock-red.txt`. Cases cover withdrawal,
  ambiguity and direct A-to-B replacement without an intermediate empty turn.
  Repair binds only undelivered pending clocks to existing row identity.

- Class 1 editorial follow-up to the owner's spec/status distinction: removed
  the inherited non-normative “fully implemented” status sentence from the
  Manager specification introduction. The existing behavior and owner mappings
  remain intact; no normative rule changed. Author fresh-eyes checked that only
  the status assertion was removed. Current spec-hygiene/full gates verify it.

### C1 repair and final verification

- C1 GREEN: 130 passed, including all six replacement cases, the delivered-final
  positive control, consuming/realtime regressions and the shipped CLI SQLite
  operational probes. `/tmp/weft-simplification-c1-green.txt`.
- Current PostgreSQL: 1,014 passed, two documented SQLite-only skips;
  `/tmp/weft-simplification-c1-pg.txt`. Shipped CLI and physical connection
  checks pass. The task-created private probe was removed afterward.
- Fresh scoped Claude C1 review: PASS, no blocker. Artifact
  `/tmp/weft-simplification-c1-review.json`. R1 accepted as a bounded conservative
  difference: read-only classification can revoke provisional output proof
  when stronger terminal proof wins, retaining wrapper_lost until its own grace.
  The consuming window can retain the row's clock because it still observes
  that row directly. This can add at most one completion-grace period in realtime;
  it cannot shorten failure grace or lose/claim a value. MF-5 already requires
  evidence precedence; no alternate identity channel is added for this delay.
  The plan's first-clock wording is qualified above to avoid overstating it.
  A direct owner-boundary probe reproduced R1 before its disposition: A at
  100.0, stronger wrapper_lost at 100.25 yields consuming wait-until 100.5 and
  readonly wait-until 100.75. Artifact
  `/tmp/weft-simplification-readonly-conservative-clock-probe.txt`.
- V1 disposed by direct repeated-row verification on both existing owners:
  observations at 100.0 and 100.4 retain 100.5 as the original quiet deadline;
  readonly settles and consuming selects the exact row at 100.5. Artifact
  `/tmp/weft-simplification-same-row-clock-probe.txt`. No new source or duplicate
  policy test was needed. Broader real quiet-fallback tests also pass.
- Current canonical mypy passes 450 files; Ruff, suppression-index and diff
  checks pass. `/tmp/weft-simplification-verified-{mypy,ruff,index,diff}.txt`.
- Strict backstitch remains 31 errors and 1,170 warnings, with zero introduced
  normalized issues. `/tmp/weft-simplification-verified-backstitch.json` and
  its `-comparison.json`. This mandatory gate is not waived.
- Full current-state suite: 5,481 passed, 44 documented backend/live-provider
  skips in 301.71 seconds. `/tmp/weft-simplification-verified-all.txt`.
  All 477 tracked/non-ignored Python source files match the verification freeze
  in `/tmp/weft-simplification-verified-source-hashes.json`. The promoted 04/05/14
  specification hashes still match the baseline above.
- The requested revision is implemented and independently reviewed; source,
  tests and plans remain uncommitted at HEAD
  `8085962af48c2ae38b8487fc449a4b11e0471170`. Status remains draft. No completed
  or land-ready claim is made: strict traceability still fails and no commit
  is authorized. Changed-file inventory is recorded in
  `/tmp/weft-simplification-verified-git-status.txt`. Broker setup/index work and
  admission overshoot remain separate action, as scoped above.
