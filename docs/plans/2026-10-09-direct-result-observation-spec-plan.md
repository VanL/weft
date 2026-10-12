# Direct Result Observation: Spec Changes Only

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/10-CLI_Interface.md [CLI-1.2.2]
Superseded by: [Result observation simplification](./2026-10-10-result-observation-simplification-plan.md)

Class: 5. Changes the intended result-observation boundary across Python and CLI. Hardening applies to the proposed contract because observation, history, and completion cross execution boundaries.

## Goal and Authorized Outcome

Specify the smallest coherent result operation: the caller observes and retrieves
this task's result or failure directly, using information it already owns. A
submitted specification must not be reconstructed from shared history. A bare-TID
caller may discover missing configuration through task-matched history without
fetching and decoding everybody else's log rows.

The initial request authorized plan authoring and Claude review. On October 9,
the user then authorized promotion of the reviewed spec changes and evaluation
of the current implementation against them. This continuation applies Tasks 2
and 3, with a read-only implementation assessment. Runtime edits and a runtime
implementation plan remain separate work. After promotion, the canonical specs
govern; the delta below is historical review material.

Outcome checklist:

- [x] Exact, reviewable text for the owning specs, with replacement boundaries.
- [x] Separate fresh-eyes self-review and Claude review using the user's criteria.
- [x] Findings disposition and document checks; handoff is the accompanying response.
- [x] Reviewed text promoted to canonical specs, with status notes and backlinks.
- [x] Current implementation evaluated against the promoted contract, including duplicate shared policies.

## Source Documents and Required Reading

Read `AGENTS.md` and the required `docs/agent-context/README.md` chain, including
`decision-hierarchy.md`, `principles.md`, `engineering-principles.md`, the
writing-plans, hardening-plans, review-loops-and-agent-bootstrap, and
runtime-and-context-patterns runbooks, then both lessons files.

Governing sections:

| Document | Section | Why it matters |
| --- | --- | --- |
| [Overview](../specifications/00-Overview_and_Architecture.md) | architecture and design philosophy | Queues own durable truth; avoid hidden setup and a second control plane. |
| [Core components](../specifications/01-Core_Components.md) | [CC-2.1], [CC-2.2.1] | One watcher driver/wake policy; source adapters and real timers must cover every wake source. |
| [TaskSpec](../specifications/02-TaskSpec.md) | [TS-1], [TS-1.4], [TS-1.4A] | Resolved `tid`, `spec`, and `io` are immutable; submission hooks finish before acceptance. |
| [Broker integration](../specifications/04-SimpleBroker_Integration.md) | [SB-0.4], CLI waiting guidance | Existing ownership and queue-native waits; metadata freshness wording needs narrowing. |
| [Message flow](../specifications/05-Message_Flow_and_State.md) | [MF-3], [MF-5] | Task-local terminal envelopes, result boundaries, evidence precedence, and current discovery-first wait. |
| [Invariants](../specifications/07-System_Invariants.md) | [OBS.6], [IMPL.8], [IMPL.9], [IMPL.10] | Runtime snapshots are best-effort; preserve execution-worker/reactor ownership. |
| [CLI](../specifications/10-CLI_Interface.md) | [CLI-1.2.1], [CLI-1.2.2] | Known-TID observations, claimed-result diagnostics, output and exit contracts. |
| [CLI crosswalk](../specifications/11-CLI_Architecture_Crosswalk.md) | [CLI-X3] | One shared result capability beneath the adapters. |
| [Pipelines](../specifications/12-Pipeline_Composition_and_UX.md) | [PL-1], [PL-3.2], [PL-5.2], [PL-5.3] | Pipeline outbox and public status differ from private child-owner events. |
| [Python surfaces](../specifications/14-Python_API_Surfaces.md) | [PY-1], [PY-2] | Acceptance is distinct from execution; handles preserve context and shared command meaning. |

Read the owning implementation to check what the proposal changes, not to make
existing inefficiencies normative: `weft/client/_task.py`,
`weft/commands/submission.py`, `weft/commands/result.py`,
`weft/commands/_task_history.py`, `weft/commands/_result_wait.py`,
`weft/commands/_streaming.py`, `weft/commands/events.py`,
`weft/core/queue_wait.py`, `weft/core/tasks/multiqueue_watcher.py`,
`weft/core/tasks/base.py`, and `weft/core/task_state.py`.

Comprehension checks: What information does accepted submission already own?
Which existing surface proves a persistent work boundary, and which proves task
termination? Why does direct caller observation not bypass the task reactor's
ownership of executing work and publishing its result?

## Spec Baseline

Plan type: spec-revision proposal, plan/review deliverable only.
Committed baseline: `8085962af48c2ae38b8487fc449a4b11e0471170`.
The proposal revises that contract; it does not claim current code conforms.

Additional inputs captured at authoring, relative to that baseline:

| File | SHA-256 of captured file | Additional text under review |
| --- | --- | --- |
| `docs/specifications/04-SimpleBroker_Integration.md` | `c611ce0b7cd588537793cefbdaf3b3d68575408f38581a3f244061b0ff9eb9c0` | Earlier burst work adds retained spawn-facade ownership; excluded from this delta. |
| `docs/specifications/05-Message_Flow_and_State.md` | `3dd5f59f40625ba9647e2c313e80ae055a6d15a21333f72229005e47e7d8c065` | Earlier burst work adds startup whole-history replay and advancing cursors; replace that proposed rule in any later promotion. |
| `docs/specifications/14-Python_API_Surfaces.md` | `b35616f7b255c7954071a2d90638aa871d0b8c07f5356977f0936fe6f9a36aaa` | Earlier burst work adds retained spawn-facade ownership; excluded from this delta. |

[Earlier burst plan](./2026-10-09-burst-result-observation-plan.md) records a
partially developed implementation direction. It is not authority for the
new result contract. [Discovery placeholder](./2026-10-09-task-specific-result-discovery-placeholder.md)
records the previously deferred unrelated-row decoding issue. This proposal
addresses its spec boundary without treating the placeholder as implementation.

Promotion baseline (2026-10-09): diff base `8085962af48c2ae38b8487fc449a4b11e0471170`,
with the reviewed delta applied to the four canonical files. The following
SHA-256 values identify the promoted content; implementation assessment uses
this baseline, not the proposal text. No runtime code is changed by promotion.

| Promoted spec | SHA-256 |
| --- | --- |
| `docs/specifications/04-SimpleBroker_Integration.md` | `ba6bf41a26e0abd456f0188654a2dbc7670382ad5d87f2cc7b0812cc3161321c` |
| `docs/specifications/05-Message_Flow_and_State.md` | `6a56f4ed6b3e81cc78e15c9888ae123fc42d9311a0a1d4ac7678d3ced0dd8976` |
| `docs/specifications/10-CLI_Interface.md` | `0697817f28aa4580cae9ba1499e47701d0f5945ccf7eed1231d1765476bbbb98` |
| `docs/specifications/14-Python_API_Surfaces.md` | `8fc5e53d08c4fdba0d1d1f167cb2be5234dc978b7a305fb30cd59338c012671b` |

## Design Judgment, Constraints, and Risks

Reuse the accepted canonical spec and effective resolved context. This is
immutable execution configuration, not cached lifecycle truth. Continue reading
fresh durable result and completion evidence. TID-default queue names remain
deterministic, but explicit `io` routes and pipeline mode must remain honored.
Do not infer one-shot behavior merely from an available outbox value.

The caller owns observation and retrieval. A CLI process and user Python code
can watch queues directly through the same capability. A parent-task relay
would add a second transport and owner without solving a required problem.
This does not remove legitimate worker-to-reactor outcome delivery inside a
task, private agent-runtime protocol, or pipeline stage forwarding.

Reuse the existing queue/watch foundation. No new subclass, result router,
route descriptor hierarchy, metadata queue, persistence store, public API name,
dependency, or configuration key is specified. Implementation can carry existing
validated values through private shared paths; this plan does not choose a
public signature or new object model.

Task-local evidence is preferred for normal waits, but existing protocols do
not all put sufficient completion evidence there. Persistent batch boundaries
and pipeline completion currently use lifecycle events. Pipeline status is
best-effort observability, not a substitute for reliable terminal proof.
Standard control cleanup can remove terminal envelopes after raw lifecycle
rows have already been retired. The existing terminal Monitor-store record
then supplies retained configuration and terminal evidence for known full TIDs.
Keep task-matched history and that existing read-only fallback where needed.
Requiring a new
per-task completion protocol for every path would expand this fix; it is not
part of this proposal. Existing evidence precedence still governs conflicting
evidence actually acquired. Do not consult global history solely to see whether
something might outrank already sufficient local proof.

Broker-side body search reduces transferred rows and Python decoding. It does
not establish an indexed TID lookup or remove all database search cost. A TID
substring can occur in another task's prompt or nested metadata, so exact
top-level identity validation is mandatory. Searches must paginate and track
the inspected range even when it contains no matches. Imported or moved old
IDs can invalidate a live cursor's completeness; a fresh observation is
required after such administrative backfill. Do not silently exclude supported
serialized history merely because a particular substring query cannot match
its encoding.

Preserve TID format, forward-only lifecycle transitions, `spec`/`io`
immutability, reservation and claimed-result semantics, spawn isolation,
context/config custody, consumption versus non-consuming inspection, result
payloads, CLI options, and exit codes. `weft.state.tasks.<tid>` remains a
best-effort runtime snapshot, excluded from dumps; it is not promoted to a
TaskSpec or result authority.

No one-way door or stored-format change is proposed. A later spec promotion
can be rolled back by reversing only this delta and its backlinks. Do not
claim runtime rollout or behavioral rollback has occurred. The later runtime
plan must keep old retained histories usable and verify producer/consumer
coverage before removing any wait cap. Normal signal sources, observer failure
fallback, and interruption must stay live; a timer cap cannot be removed merely
because it looks like polling.

## Proposed Spec Delta

Promotion strategy: **A, in-file requirement text before new implementation
claims**. A later docs-only promotion applies these paragraphs in the existing
files and adds plan backlinks. Existing implementation notes must explicitly
separate baseline behavior from the new unimplemented requirements. Do not add
mapping claims that say current functions implement this delta, reclassify an
entire active file, or write runtime code in the promotion slice.

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| `docs/specifications/05-Message_Flow_and_State.md` | A | [MF-5] realtime metadata and result observation |
| `docs/specifications/04-SimpleBroker_Integration.md` | A | [SB-0.4] task-control observation's per-turn metadata instruction |
| `docs/specifications/14-Python_API_Surfaces.md` | A | [PY-2] accepted-handle configuration custody |
| `docs/specifications/10-CLI_Interface.md` | A | [CLI-1.2.2] stream cross-reference wording only |

### 1. [MF-5]: Narrow the realtime metadata rule

Replace the sentences beginning "Realtime observation refreshes task metadata"
through "Persistent and interactive work-item output is not task completion"
with:

> Realtime observation uses known immutable task configuration when available.
> When configuration is missing, it discovers and validates the task's metadata
> before treating ordinary outbox values as one-shot completion evidence. Late
> discovery may establish explicit routes and collection mode without consuming
> those queues; once known,
> immutable configuration is retained for that observation. Explicit terminal
> evidence remains sufficient without TaskSpec discovery. Persistent and
> interactive work-item output is not task completion. Fresh lifecycle
> observations remain separate from immutable configuration.

### 2. [MF-5]: Replace the result-wait/discovery rules

Replace the bullet beginning "`weft result` and `weft run` share the same wait
helper path" through the discovery-first bullet ending "shared completion
wait". Also replace the captured additional paragraphs beginning "Generic
result materialization reads shared lifecycle history once at startup" through
"Generic discovery still decodes newly appended unrelated log rows", including
their implementation claim. Exact replacement:

> `weft run`, `weft result`, and Python result retrieval use one shared result
> capability. Shared result behavior has one implementation owner and is
> reused by callers and collection modes; separate implementations of the same
> behavior are defects. Mode-specific completion rules remain explicit within
> that shared capability. The caller owns observation and retrieves output
> directly from the task's configured result surface. A parent task does not relay results
> solely to satisfy another caller's wait.
>
> An operation with the accepted canonical task specification, assigned TID,
> and effective resolved context derives its routes and collection mode from
> those values. It must not rediscover the same specification through task
> history or wait for child initialization, queue existence, or a task-owned
> TaskSpec event before observing those routes. Explicit `io` routes take
> precedence over canonical TID defaults. Submission-time shaping is complete
> before this configuration is retained.
>
> A bare-TID operation resolves canonical TID-default routes directly and
> discovers only missing configuration required for correct collection, such
> as explicit routes or persistent/pipeline mode. Discovery is specific to that
> TID and uses public SimpleBroker filtering before fetching and decoding shared
> history bodies. Substring matching is candidate selection, not identity:
> parsed records must match the exact top-level TID. Paginated lookup must not
> truncate relevant history. Supported serialized histories must remain
> discoverable; an unsupported filtering capability or encoding is an explicit
> boundary to resolve, not permission for silent absence or a normal whole-log
> replay fallback.
> After raw task-log retirement, a known full TID uses the existing read-only
> terminal Monitor-store fallback to recover retained configuration. Only
> terminal records qualify; absence and unavailable/degraded-store results
> retain their existing distinct diagnostic meanings.
>
> Configuration discovery is separate from result waiting. Once configuration
> is established, the observation retains it. A negative lookup retains the
> inspected range so later relevant activity does not replay the same history.
> Generated live writes at the supported SimpleBroker dependency floors may be
> followed by an advancing message-ID range;
> administrative exact-ID imports or ID-preserving moves that backfill that
> range require a fresh observation. This does not restrict ordinary pipeline
> result moves.
>
> The observation owner registers interest in its exact result and completion
> queues before checking current durable contents, including queues that have
> not received their first write. It then uses the existing watcher/reactor
> waiting foundation for backend activity, interruption, and actual timeout or
> completion-grace deadlines. There is no separate Weft sleep/probe cadence or
> arbitrary maximum wait slice for result discovery. Backend-native waiting
> and SimpleBroker's own polling remain broker concerns. If Weft's observation
> adapter loses its native waiter, it must transition to another live wake
> source or fail explicitly. It must not silently wait until a caller deadline
> or indefinitely when no deadline exists.
> Notifications are hints; durable messages determine the result.
>
> The watched completion surfaces follow the existing task protocol: task-local
> typed control evidence where sufficient, and TID-matched lifecycle evidence
> for persistent work boundaries and pipeline completion. A live wait may omit
> lifecycle-log interest only when every terminal path for that task kind has
> reliable task-local terminal publication and crash coverage under the existing
> terminal-retry and manager `wrapper_lost` contracts. Other task kinds retain
> TID-matched lifecycle observation. Best-effort pipeline status snapshots do
> not replace lifecycle completion proof. After raw history retirement, known
> full TIDs retain the existing read-only terminal Monitor-store fallback and
> its unavailable/degraded diagnostics, at its existing evidence priority.
> Unrelated shared-log activity must not cause whole-log payload
> decoding or restart completed configuration discovery. Shared history is not
> a prerequisite for ordinary collection when local evidence is sufficient.
>
> Existing evidence classification and precedence apply to acquired evidence.
> Persistent result waits retain `work_item_completed` and `work_completed`
> boundaries; a work-item result is not task termination. Streaming follows
> unread output using the existing completion semantics. Output already emitted
> as a completed one-shot result remains evidence against a later false timeout.
> Result waits tolerate the existing completion/output visibility gap, honor
> the completion grace, and perform the last non-blocking output drain before
> returning completed. Claimed-result recovery remains scoped to actual claimed
> rows rather than repeated on empty turns.

The other [MF-5] evidence-priority and diagnostic rules stay in force. No new
completion envelope, batch marker, or failure status is introduced.

### 3. [SB-0.4]: Replace the per-turn reconstruction instruction

Replace "Each turn rereads metadata, routing, and completion evidence;
connection reuse does not cache those observations" with:

> Observation owners retain known immutable task configuration and routing for
> the operation, and read fresh durable completion and runtime evidence when
> evaluating its outcome. Connection reuse does not cache lifecycle truth and
> does not require reconstructing immutable configuration on each turn. Result
> discovery and waiting follow [MF-5].

Retain the surrounding connection/transaction cleanup requirements. The earlier
submission-facade additions are outside this delta.

### 4. [PY-2]: Insert after the acceptance/receipt paragraph

> A returned submission handle retains access to the accepted canonical task
> configuration, assigned TID, and effective resolved context needed by the
> shared result capability [MF-5]. These immutable values are usable before
> child initialization and after the submitting client closes. They are not a
> lifecycle snapshot. Python and CLI callers observe and retrieve task results
> directly through that capability; result meaning and errors remain shared.
> Bare-TID callers use the task-specific discovery boundary in [MF-5].

No new public field or argument is mandated. Existing handles and CLI result
forms remain supported. The CLI/crosswalk sections already delegate to this
shared capability and need no duplicate normative result model.

### 5. [CLI-1.2.2]: Keep the stream cross-reference aligned

In the `--stream` bullet, replace "the same task-log completion and grace rules"
with "the same lifecycle completion and grace rules in [MF-5]". This is a
cross-reference clarification, not a new CLI contract or a change to streaming
result meaning.

## Tasks and Stop Gates

1. **Author and review this proposal (authorized now).** Touch only this plan
   and `docs/plans/README.md`. Verify exact replacement anchors against the
   baseline and captured additions. Self-review, then ask Claude to review
   the whole plan and exact delta. Stop if a finding requires a new protocol,
   persistence surface, or public API rather than tightening the spec boundary.

2. **Spec-promotion slice (authorized by the continuation).** Apply the reviewed
   delta only to `04-SimpleBroker_Integration.md`,
   `05-Message_Flow_and_State.md`, `14-Python_API_Surfaces.md`, and the one
   cross-reference in `10-CLI_Interface.md`. The [SB-0.4] edit is within the
   task-control observation paragraph; its immutable-route rule is shared,
   while the surrounding control ownership requirements remain intact. Add this
   plan's backlink beside each touched section. Label nearby baseline
   implementation mappings accurately without claiming the new requirements
   are implemented. Record the promotion baseline. Stop if the reviewed text
   has drifted or a compatibility issue changes its meaning.

3. **Verify, review, and assess (authorized by the continuation).** Rerun
   document gates and check every requirement against preserved neighboring
   contracts. Claude reviews the promoted diff and status/mapping accuracy.
   Record dispositions and reconciliation. Evaluate current code against the
   promoted contract, separating confirmed gaps from untested risks. Keep
   runtime work outside this plan; a separate implementation plan supplies failing behavior tests,
   broker/process proofs, and rollout details before coding.

All edits are sequential. Read-only checks may run together. No other agent
edits or runs tests while the document owner is editing.

## Verification

For the authorized plan artifact, use the existing metadata gate: after adding
the plan but before indexing it, demonstrate the missing-index failure; add the
index row and rerun the gate. This is a documentation check, not evidence that
the proposed runtime behavior works. No production test is added or modified.

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
git diff --check
```

For the initial plan-only slice, check scope with `git status --short` and
the explicit plan/index diff, preserving all prior work and the captured spec
hashes. For the authorized promotion, verify the exact reviewed wording and
record the new promoted hashes; verify every preexisting source/test file remains
unchanged by this slice. No backstitch executable is available in the discovered local
tooling; do not report an unrun traceability scanner as passing. A future
promotion must use the repo's then-available required traceability gate and
cannot waive mandatory findings.

Future runtime acceptance obligations, for its separate implementation plan:
known-spec queued child with no TaskSpec-history discovery; custom routes; bare-TID candidate
false positives and pagination beyond one page; negative-range progress;
result-before-subscribe race; timeout, interruption and native observer failure;
one-shot failure without output; persistent multi-output batch boundaries;
stream completion and final drain; pipeline public status and final output;
claimed-result diagnostics; client-close/context preservation. Keep queues and
processes real, using `broker_env`/`WeftTestHarness` on SQLite and PostgreSQL.
Counters at the read/decode boundary can prove absence of unrelated payload
replay; latency alone cannot. These are obligations, not test results or an
implementation checklist authorized by this plan.

## Independent Review and Findings

Reviewer: authenticated local Claude CLI, a different agent family. Read-only
tools; no code, spec promotion, tests, delegation, or commits by the reviewer.

Required criteria: "So what would an ideal spec include? What would make sense
to an expert Python developer who wanted to build an elegant system on weft's
principles?" Also check conceptual unity, shared functionality versus new
subclasses, and overengineering. Prefer deleting needless work. Verify the
exact delta against the source sections and relevant implementation, including
failure, persistence, pipeline, cleanup, and history boundaries.

Return PASS/BLOCKED with reasons: could the spec edits be made confidently and
correctly as written; would the resulting contract impair robustness? Do not
require a runtime design to approve a spec-only plan. Every accepted finding
gets a disposition and amendment; materially changed text gets re-review.
Review also covers the completed plan/index artifact and document evidence.

| Review | Finding | Disposition / evidence |
| --- | --- | --- |
| Self-review | A universal task-local completion requirement would force persistent batch protocol changes and ignore control cleanup. | Narrowed to existing sufficient local surfaces plus task-matched lifecycle evidence when required. |
| Self-review | Substring search is not exact identity or guaranteed indexed lookup; a last-match-only cursor cannot advance an empty range. | Explicit candidate verification, pagination, negative-range progress, encoding completeness, and bounded performance claims. |
| Claude round 1, B1 (blocking) | Raw task history may already be retired when controls are cleaned up; the proposal omitted Monitor configuration/terminal fallback. | Verified in Monitor collation and [MF-5]/[CLI-1.2.3]. Added the existing terminal-only read-only fallback and its distinct degraded diagnostics to both discovery and completion clauses; corrected rationale. |
| Claude round 1, B2 (blocking) | Weft's native observer thread can exit on failure without attaching fallback watchers. Removing a wait cap without a normative replacement can hang. | Verified in `weft/core/queue_wait.py::_run_multi_waiter`. Added transition-to-live-source-or-explicit-failure requirement; no polling cap or new abstraction prescribed. |
| Claude round 1, N1/N2 | Pipeline snapshots are best-effort; a waiter cannot infer missing future terminal proof. | Removed pipeline status as completion proof. Made eligibility to omit log interest depend on producer terminal-retry/crash guarantees; pipeline and persistent boundaries retain task-matched lifecycle observation. |
| Claude round 1, N3/N4 | Late route switching lost explicit non-consumption; broker paragraph is about task-control observation. | Restored non-consumption and named the task-control paragraph context in promotion instructions. |
| Claude round 1, N5/N6 | CLI stream wording can drift; per-file promotion table was absent. | Added the single [CLI-1.2.2] cross-reference edit and per-file strategy table. |
| Claude round 1, observations | Backfill qualifier, relay prohibition, JSON encoding, upstream costs. | Restored supported-dependency qualifier. Kept the relay boundary because it records an explicit user correction. Did not adopt the assertion that every JSON encoding contains bare TID digits: escaped string digits are a counterexample. Kept encoding completeness as an implementation boundary to resolve, and kept upstream search/facade costs separate. |
| Claude round 2 | PASS; all eight accepted findings verified, no new defects. | Read-only completed-plan review confirms exact anchors, captured spec hashes, 237-row index, clean document diff, and expert-defensible simplicity. |
| Claude round 2, optional duplication/relay trims | Monitor fallback appears in both configuration and evidence clauses; caller-relay prohibition could be omitted. | Retained: configuration custody and lifecycle evidence are separate boundaries, and the relay sentence records an explicit user correction. No mechanism or extra protocol results from either sentence. |
| Claude round 2, inherited live-task retention gap | A bare-TID caller may lack explicit-route configuration for a still-live task whose initialization history has been retired; terminal-only Monitor fallback cannot cover it. | Flagged, not fixed or claimed fixed. “Supported serialized histories” refers to retained evidence, not an extension of metadata retention. The proposal adds no nonterminal Monitor authority or guaranteed configuration lifetime. A future promotion must not claim to solve this case; extending retention or reading nonterminal summaries for immutable configuration would require separate review. |

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No runtime implementation deviations are claimed by this spec-promotion and
evaluation deliverable.
The intended contract changes are explicitly in Proposed Spec Delta.

## Out of Scope and Separate SimpleBroker Action

Do not finish, revert, or promote the earlier burst implementation, repair its
tests, change manager admission, sweep other polling sites, or alter
SimpleBroker. Schema inspection and fresh session-facade setup are separate
concerns. Flag to the user that retaining a session/core does not currently
eliminate per-facade plugin discovery/schema validation, and body search may
still scan queue bodies inside the database. Whether upstream should change
facade setup or offer structural/indexed TID filtering belongs to separate
SimpleBroker work. This plan does not require those upstream changes.

## Execution Log

| Step | Evidence |
| --- | --- |
| Baseline and source audit | Baseline SHA, three captured spec hashes, exact replacement anchors, and existing protocol boundaries recorded above. |
| Document checks | Metadata check before indexing: two expected missing-index failures. After indexing: metadata plus spec hygiene, six cases passed with exit 0. `git diff --check` exit 0. Twelve plan links and sixteen literal repo paths resolved; index count matches 237 files. Three captured spec SHA-256 values unchanged. |
| Claude review | Round 1 BLOCKED with B1/B2; findings verified and dispositions above. Round 2 PASS, all eight accepted findings resolved. Claude explicitly endorsed the expert-defensible shared design with no new subclass, router, store, protocol, public API, or configuration key. Both reviews used the authenticated local Claude CLI with read-only tools and no session persistence. |
| Authorized continuation | Promoted the five reviewed deltas into four canonical specs; added backlinks and truthful strategy-A implementation-status notes. Exact-text check RED before, GREEN after. Promotion hashes recorded in Spec Baseline. No runtime source/test changes; 17 other captured prior files retain their hashes. |
| Implementation assessment | Root plus two read-only agents inspected current code and closest tests. Confirmed accepted-config, filtered-read, wake/cadence, failure recovery and Monitor result gaps. Focused reruns: three raw-cursor cases pass; queued realtime regression fails reads=2 expected0; pipeline quiet-output and failed-native-waiter behavior tests pass. |
| Promotion review | Claude PASS for promotion and assessment accuracy. F1 accepted: restore paragraph separation. F2 accepted: describe late metadata discovery, not repeated refresh. F3 accepted: add each backlink to its existing Related Plans section. User clarified that duplicate shared implementations are defects; follow-up Claude PASS for the exact shared-owner clarification and common-policy assessment. A1 accepted: name existing run route/pipeline duplication while acknowledging the persistent-wait guard. A2 accepted: record static wrapper-lost/emitted-output differences without deciding correct mode behavior. A3 accepted: assessment column now states missing ownership. S1 accepted: reflow the paragraph. Independent agent audited further realtime/interactive/output-assembly duplication; root verified and integrated it. |

## Baseline Implementation Assessment (2026-10-09)

Assessed against the promoted four-file baseline above, including the earlier
worktree implementation candidate. This is an evaluation, not a runtime change
or an implementation plan. Source locations identify the inspected owners;
static findings are distinguished from the bounded tests rerun below.
The successor plan owns subsequent contract revisions and implementation;
these findings describe the assessed baseline, not the current code.

| Promoted requirement | Assessment | Evidence and consequence |
| --- | --- | --- |
| Caller retrieves directly through a shared capability. | Direct retrieval present; shared implementation incomplete. | `weft/client/_task.py` `Task.result()` and `weft/commands/run.py` `_LiveRunSession.wait()` call `weft/commands/result.py::await_task_result`; the collectors read the task outbox directly. No parent-result relay is required or present. |
| Shared result behavior has one code owner. | Missing for common collection policies. | `_result_wait.py::await_one_shot_result` and the persistent branch of `result.py::_await_single_result` separately implement queue/session/monitor setup, terminal settlement, completion grace/final drain, and deadline composition. Existing shared decode/evidence helpers do not make these independently implemented policies shared. Persistent work-boundary rules differ from one-shot completion rules; that does not justify duplicating their common machinery. |
| Accepted configuration, TID, and effective context remain available to result observation. | Context/TID present; configuration missing. | `Task` has only client/TID/context; `_submit_prepared()` discards the prepared configuration when constructing it. `_LiveRunSession` likewise retains execution/context without canonical configuration. Both public waits enter `_await_result_materialization()` unconditionally. The older `execute_run` callback can resolve known routes, but the canonical CLI `cmd_run` creates its live session with `wait=False`, then rediscovers them. |
| Task-specific broker filtering before body transfer/decoding; complete lookup and negative progress. | Filtering missing; raw cursor is a partial improvement. | `_streaming.poll_log_events()` iterates global queue bodies and JSON-decodes before checking top-level TID. Materialization starts with cursor `None`. Its raw watermark now advances past unrelated/malformed/non-object rows, avoiding repeat materialization replay. `_task_history.load_latest_taskspec_payload()` still starts fresh and can fall back to a full scan. Generator reads and exact TID checks exist; broker candidate filtering does not. |
| Retain known immutable configuration separately from fresh lifecycle evidence. | Partial. | The result collector retains discovered routes/mode, but known accepted configuration never reaches it. Realtime `events.py` replaces its TaskSpec on later matching log records and can recompute/rebind routes after configuration is already known. Its independent cursor advances only after a matching TID, so unrelated suffixes can be reread. |
| Subscribe to exact result/completion surfaces before waiting; use actual deadlines. | Collector subscriptions present; discovery and cadence incomplete. | Materialization watches only `weft.log.tasks` while probing task/default/pipeline result surfaces. Collectors establish outbox/control/log observation before their loop, but all three result waits use `effective_result_surface_wait_interval()`, capped by the 0.1-second `RESULT_SURFACE_WAIT_INTERVAL`. Realtime also has a 0.1-second cap. Those are application rescan slices, not caller timeout or completion-grace deadlines. |
| Observer failure transitions to a live source or fails explicitly. | Missing after native startup success. | `QueueChangeMonitor._run_multi_waiter()` wakes once, closes/drops the failed waiter, and exits. Constructor-time native unavailability does create per-queue watchers; runtime waiter failure does not. Existing poll caps still supply scan liveness, so this assessment does not claim a current indefinite hang. Removing the caps first would create that risk. |
| Recover terminal configuration/evidence through the existing Monitor fallback after raw retirement. | Missing from generic result collection. | Result materialization and one-shot/persistent collection have no terminal Monitor-store lookup. Status snapshots and interactive run have separate fallback owners in `tasks.py::_monitor_store_task_snapshot` and `run.py::_poll_monitor_terminal`; those do not feed retained config into generic result materialization. A retired custom-route task or failure without output therefore lacks the required result fallback/degraded distinction. |
| Preserve typed terminal proof, batch boundaries, streams, grace/final drain, claimed recovery, and context ownership. | Existing mechanisms to preserve; not a blanket compliance claim. | `_result_wait.py` keeps terminal envelopes, emitted-output evidence, grace and final drains. `result.py` keeps persistent work boundaries and claimed-row preflight. Pipeline completion still uses lifecycle evidence, not best-effort status proof. Queue sessions/leases are bounded by existing finally/close scopes. Unknown metadata defaults to nonpersistent collection, so lost configuration can still select the wrong mode. No broad runtime suite was rerun for this assessment. |

A focused independent source audit found further duplicate shared policies,
with concrete divergence. These are implementation defects under the shared
owner requirement, not optional refactoring. Source-only counterexamples are
not claims that production exercised the paths:

| Duplicated behavior | Owners and observed source difference |
| --- | --- |
| Route and collection-mode selection | `run.py::_wait_for_task_completion` (line 599) derives routes and always invokes one-shot collection; `result.py::_await_single_result` (line 415) derives routes and selects persistent mode. Stored-spec submission rejects persistent `--wait` at line 1324, so this is not evidence of a public persistent-wait failure. Route selection and pipeline control suppression are nevertheless duplicated; merge the existing accepted-config path into the shared owner rather than add a third copy. |
| Terminal and emitted-output settlement | `_result_wait.py` defers manager `wrapper_lost` at lines 188–194 and lets visible output complete; the persistent branch in `result.py` treats it as failure at lines 491–503. One-shot materialized completion accepts `emitted_result_seen` at line 234; the persistent condition at line 594 checks only `result_values`. These are static differences, not firing-test results, and correctness of each mode has not been decided. |
| Lifecycle-log cursor advancement | `events.py` (line 557) advances only after a target-TID row; `_streaming.poll_log_events` (line 123) advances across every raw row. Realtime therefore retains the unrelated-suffix replay defect avoided by the materialization cursor change. |
| Terminal interpretation | `run.py::_on_state` (line 849) maintains its own event mapping instead of `task_evidence.terminal_status_from_event` (line 285). An event-only `work_timeout` with nested completion proof maps to failed in the callback and timeout in the shared classifier; the callback also lacks the shared `task_activity` exclusion. This is a static counterexample, not a runtime failure injection. |
| Completion grace and empty-result settlement | `events.py` first waits terminal grace at line 670, then starts another full grace for an empty completed result at line 716. `_result_wait.py` settles after one completion grace and a final drain at line 266. Read-only versus consuming access does not justify different completion-grace policy. |
| Public output assembly | `events.py::_peek_result_value` (line 187) and `result.py::_read_outbox_task_result` (line 787) separately scan, select final values, append, and aggregate. Decode/aggregation helpers are shared; no output-shape divergence was demonstrated. The common assembly behavior still has separate implementations. |

Persistent work-boundary rules, non-consuming observation versus result
consumption, and interactive input/presentation are distinct operations. They
should use the same common policies and code rather than reimplement them.
Existing shared decoders, aggregation, terminal-envelope selection, and queue
monitor code are useful owners to reuse; no new subclass is implied.

Closest tests also expose old assumptions that must not be mistaken for new
contract coverage. `test_result_reads_pipeline_outbox_by_pipeline_tid` passes
with pipeline output, a retained `status="running"` snapshot, and no seeded
terminal lifecycle event. The generic one-shot quiet-output fallback can settle
this path without the pipeline completion proof the promoted contract requires.
`test_queue_change_monitor_wakes_once_when_waiter_raises` passes while expressly
requiring the observer thread to exit without a second wake. These two cases
were rerun together and passed; they confirm existing behavior that future
implementation must revisit, rather than new-spec compliance.

Public backend-error translation also needs parity verification: CLI
`cmd_result` wraps ordinary collection exceptions as `CommandExecutionError`,
whereas Python `Task.result` directly forwards the helper call. No failure
injection or claim of complete public error parity was made in this evaluation.

A further visible consequence of missing accepted-config custody: for a queued
child with no discovered result surface, `Task.result(timeout=None)` calls
`await_task_result` with `wait_for_materialization=False`. The materializer can
return `None`, producing `status="missing"` instead of establishing a wait on
known routes. This is confirmed by the call/default/branch, not a new production
measurement.

The retained live-task metadata gap identified in Claude's plan review remains:
this contract does not extend retention or make nonterminal Monitor rows live
proof. SimpleBroker per-facade discovery/schema setup and possible database
body-search cost remain separate upstream concerns, not findings fixed by these
spec edits.

Assessment verification:

- Source review by the document owner and two read-only agents; no runtime edits.
- Existing `test_materialization_decodes_each_history_row_once`: three parameter
  cases passed. This proves the earlier raw-cursor improvement, while explicitly
  accepting one read of each unrelated body; it does not prove filtered lookup.
- Existing `test_queued_realtime_observer_does_not_expand_to_pre_tid_history`:
  failed with `reads=2`, expected `0`, across two driven waits. This reproduces
  queued realtime replay in the current candidate, beyond the materializer's
  advancing raw cursor. The test and production source were not changed.
- Exact promotion-text checker: failed before promotion (and again before the
  shared-owner clarification), passed after all five
  deltas, and confirmed the superseded rules absent. Metadata/spec hygiene:
  six cases passed; `git diff --check` passed.

The smallest next runtime objective is custody of accepted configuration through
existing handles into the existing shared result collector. Common result
behavior must be implemented once and reused, including by one-shot and
persistent collection. A common public entry point is not sufficient when its
branches duplicate that behavior. Task-filtered
historical lookup and observer failure/cadence are distinct remaining gaps.
They need a separately reviewed implementation plan; this assessment does not
prescribe a subclass, new protocol, or upstream dependency change.
