# Complexity Review Corrections Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.2], [CC-2.4]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-6]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

## Scope and baseline

Class 5, hardened: corrections cross runtime identity, foreground takeover,
interactive shutdown and context custody; normative clarifications require
review before promotion. Baseline: `b605582c`. This is a forward correction
plan for the seven findings supplied on 2026-09-08, not a rewrite of the seven
original commits. Preserve the unrelated CLI-session draft and the owner's
existing parent-ledger/index edits. No dependency, configuration or CI changes.

## Outcome checklist and dispositions

- [x] F1: verify and retain failure when a one-shot worker supplies a private
  outcome but cannot be reaped. No public outbox success has been emitted at
  this boundary. Consumer normal-return processing releases worker custody;
  suppressing the failure would falsely publish idle identity. Explicit cost:
  completed computation can fail the item when process closure is unproved.
  Do not invent a new outcome/cleanup channel in this correction.
- [x] F2: preserve explicit exact creation-time evidence when the managed-PID
  observation is unknown. A recorded exact identity still wins over conflicting
  handle evidence; two unknown observations stay unknown without fresh lookup.
  Apply this precedence to publication and control, not only one merge.
- [x] F3: let typed dump and client dump share one concrete resolved-context
  owner. Preserve the supplied broker target and configured artifact directory,
  default/explicit output paths, owner-only files and typed errors.
- [x] F4: retain missing/invalid/empty handle as unknown and retain positive
  stop-proof requirements. Explicitly review the MA-3 paragraph added after the
  original proposed delta; do not claim original Codex-review coverage.
  Prove that aged valid service rows with missing handles are pruned after
  both age thresholds. Reader omission/pruning is not process-exit proof.
- [x] F5: preserve post-unwind interactive STOP/KILL acknowledgement and fix
  the client's one-second STOP wait. Use the existing
  `INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget, observing matching ack or
  existing terminal evidence promptly before escalating. The mismatch was
  introduced by Plan 3 (`f3ad6741`), not Plan 6. Do not claim the reported
  additional child-exit delay is reproduced without timing/signal evidence.
- [x] F6: restore foreground startup's scoped supersession append for an
  unconfirmed active canonical public-request manager in the same scoped
  service, with external-supervisor authority and `foreground_serve=True`.
  Reduce to newest per owner; do not supersede draining or terminal rows. Keep shared positive-proof
  selection and no-reader-deletion unchanged. Positive runtime or matching
  PONG blocks startup. Supersession failure must not permit takeover. Existing
  rows remain in history; do not globally treat unknown as live or stale.
- [x] F7: log failed exact Manager CLEAR at WARNING with message/queue identity;
  preserve residue and no-retry semantics, leaving successful and KEEP paths
  unchanged.

## Invariants, structure and rollback

The existing TaskSpec -> Manager -> Consumer -> TaskRunner -> queues spine
remains the only execution path. Full TIDs, queue names, lifecycle payloads,
reserved disposition and plugin authority remain unchanged. A task must not
signal itself or a reused PID; no fresh PID observation may replace recorded
unknown identity. Completed output must not erase surviving worker custody.
Manager stop may not infer death from a filtered or pruned registry row.
Foreground supersession is a peer-history append, not a deletion or death claim.
Interactive acknowledgements stay after unwind; earlier terminal evidence
keeps its existing interpretation and does not acquire new signal authority.

Current owners: BaseTask merges published observations and controls managed
identities; HostRunner owns its process reap; Consumer releases normal-return
identity. `cmd_system_dump` owns dump materialization and the client currently
rebuilds context. `_foreground_serve_blocking_manager` is the scoped takeover
owner; shared selection omits unknown rows before it sees them.
`_InteractiveRunLifecycle.request_exit` owns client escalation while task shutdown
owns the larger grace/termination budget. Manager's exact CLEAR catch owns
the failure diagnostic and retained row.

Rollback is a revert of this correction commit. No schema or data migration.
Already appended superseded rows are historical observations and are not
rewritten on rollback. Existing exports are not removed or relocated. Do not
roll back the original identity/custody safeguards merely to restore timing.

## Proposed Spec Delta

Promotion strategy A: after independent review of this exact delta, apply it
to the specs before implementation. The MA-3 paragraph at baseline lines
566-571 is explicitly included in this review despite being retained verbatim.
Other additions are clarifications of the selected boundaries; specs remain
the single contract after promotion.

1. Add to 01 [CC-3.2] following the identity-control paragraph:

   > When combining observations of the same host PID, a recorded exact
   > creation time takes precedence. If the recorded creation time is unknown,
   > an exact creation time explicitly supplied by the runtime handle is
   > retained. Unknown observations never erase exact evidence, and a fresh PID
   > lookup must not fill a previously recorded unknown identity. A private
   > worker outcome alone is not public result delivery or reap proof; failure
   > to confirm one-shot worker exit fails the runner call and retains custody.

2. Add to 03 [MA-1] near the foreground fallback rule:

   > Foreground startup may supersede an unconfirmed active canonical
   > public-request manager in the same scoped manager service only when its
   > handle has external-supervisor authority and `foreground_serve=True`
   > metadata. It reduces to the newest row per owner and appends a superseded
   > owner record; draining and terminal rows are not takeover candidates.
   > Positive runtime or matching PONG evidence blocks this takeover.
   > Candidates are considered in ascending TID order: a lower unconfirmed
   > foreground incumbent is superseded before a higher proved-live manager
   > blocks startup. A lower proved-live manager blocks without superseding
   > higher rows.
   > This does not delete peer history or classify unknown identity as dead;
   > failure to append supersession blocks takeover.

3. Retain and independently review 03 [MA-3]'s stop-confirmation paragraph;
   add after it:

   > Missing, invalid or empty runtime identity remains unknown. Expiration
   > of the reader or pruning age windows does not prove manager exit. Valid
   > service rows with unknown identity remain subject to the unknown-row
   > pruning rule in [MA-1], including when the runtime handle is missing.

4. Add to 10's current interactive behavior [CLI-1.1.1]:

   > After requesting STOP, the interactive client observes matching
   > acknowledgement or existing terminal evidence for the named
   > `INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget before escalating to KILL.
   > Terminal evidence ends the wait promptly; acknowledgement remains after
   > task-side runtime unwind.

5. Add to 14 [PY-2]'s context-owned capability contract:

   > Client system dump preserves the supplied resolved context, including
   > its broker target and artifact directory. Without an explicit output,
   > the export is `weft_export.jsonl` in that context's Weft directory.

Add this plan's backlink and nearby implementation mapping to touched specs.

## Implementation sequence and verification

1. Review this plan, the exact spec delta and retained MA-3 paragraph; promote
   the reviewed text. Record provenance limits and fresh-eyes dispositions.
2. Prove F2/F3/F5/F6/F7 red before implementation, then repair their existing
   owners. Parallel file ownership: identity agent owns BaseTask and identity
   tests; interactive agent owns run client and interactive tests; registry
   agent owns manager_runtime and serve/pruning tests; root owns dump,
   Manager warning, docs and integration. No further delegation.
3. Verify F1/F4 retained boundaries with existing tests and add absent-handle
   pruning cells at each age boundary. Review every completed slice independently,
   then run the full suite, Ruff, mypy and metadata/suppression checks before
   one forward correction commit. Do not edit source while the full gate runs.

Firing regressions: F2 unknown->explicit exact, unknown->unknown, recorded
exact->conflicting exact, published mapping plus direct control; F3 public
client dump with non-default directory and explicit broker, default and explicit
paths, typed failure; F5 real EOF-ignoring child with >1-second unwind and
exactly one terminal envelope/no premature KILL, bounded escalation with no
evidence, prompt existing terminal completion; F6 unknown foreground row gets
superseded while old message remains, live/PONG blocks, append failure blocks;
F7 real retained reserved row plus WARNING on injected broker deletion failure.
F1 selected private success and error outcomes cannot return normally without
reap proof. F4 missing/invalid/empty handles cross min_age and 300-second gates
without changing reader/stop proof. Keep broker queues and public client paths
real; mock only controlled process/clock failures and external proof probes.

Commands (after `. ./.envrc`): focused pytest modules with `-m '' -n 2`;
full `./.venv/bin/python -m pytest -m '' -n 2 -vv`; `./.venv/bin/ruff check .`;
full mypy over `weft bin integrations/weft_django/weft_django
extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox
extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`;
`./.venv/bin/python bin/ruff_suppression_index.py --check`; spec/plan metadata
tests. Format changed files with the repo Ruff; exclude unrelated churn.
Client acceptance includes output/default-path honesty, clean error boundaries
and no premature control escalation. No new parser or batch contract is added.

## Review and deviation record

Author fresh-eyes: distinguish private outcome from public delivery, registry
omission from death proof, and supersession append from peer deletion. These
are separate boundaries; repairing one by weakening another is rejected.
Reviewer availability: three in-session same-family reviewers are available;
cross-family review has not been run. Independent plan review is pending.
Original Plan 4's implementation record documents later stop-proof review,
but does not establish that the added paragraph was in the original reviewed
delta. This correction explicitly reviews it instead of backdating approval.


2026-09-08 plan review: PASS from all three independent same-family scoped
reviewers. Registry review first blocked overly broad external-supervisor
wording; narrowed it to active canonical public-request foreground owners,
newest per owner, excluding draining/terminal/ordinary external rows. Round 2
PASS. Interactive review corrected the lifecycle class name and named-budget
wording. Identity/context review passed exact-evidence precedence and resolved
broker custody. MA-3 retained stop-proof paragraph was explicitly reviewed.
Promotion baseline: `b605582c` plus the four spec deltas in this correction;
implementation begins only after this promotion. A real pruner probe already
confirmed missing-handle age60/min0 retained, age600/min900 retained,
age600/min0 deleted, all without errors.

Completed-slice review found the initial foreground fallback still changed
lower-TID ordering when a higher live manager existed. Accepted correction:
compare the scoped takeover candidate with the selected positive blocker and
process the lower TID first. Root and registry reviewer independently agreed
this restores the historical order, rather than inventing a new takeover rule;
the exact MA-1 clarification above was promoted before the revision. Dump
review also reproduced a raw relative-path FileNotFoundError outside the shared
owner's catch; path resolution moves inside the same typed export boundary.


## Completed verification

- F1/F2: the baseline identity regression produced two failures and four
  preservation passes. Exact handle evidence was overwritten by managed None.
  Final 15 focused identity/reap cells pass, including real worker trees,
  broker mapping, both direct control kinds, pure merge precedence and private
  success/error outcomes with failed reap. Independent identity review PASS.
- F3/F7: baseline public-client default path, supplied broker export contents
  and CLEAR warning tests all failed. Final dump/Manager selection passes
  67 tests. A follow-up public-client relative-path failure reproduced raw
  FileNotFoundError, then passed after moving path resolution into the shared
  typed boundary. Independent context/logging review PASS.
- F4/F6: valid missing/invalid/empty identity rows cross both age gates in
  twelve real-broker pruning cells. Two initial supersession/append-failure
  regressions and the lower-unknown/higher-live ordering test failed before
  repair. Final serve plus pruning selection passes 31 tests; positive runtime,
  PONG, failed append, excluded row classes and both TID orders are covered.
  Independent root review PASS after the ordering correction.
- F5: a separate baseline-method probe reproduced STOP followed by KILL after
  1.109 seconds. The corrected real-child test observes STOP only, more than
  one second of unwind, process death, one terminal envelope and the subsequent
  acknowledgement. All 32 related interactive tests pass. Controlled-clock
  cells also pin delayed acknowledgement and exact budget exhaustion. This
  demonstrates premature escalation, not a universal additional exit-delay
  measurement. Independent interactive review PASS.
- Final full suite, including slow tests: 4,582 passed, 16 skipped in 968.37s
  using `pytest -m '' -n 2 -vv`. Skips are eleven opt-in live-provider cases
  and five PostgreSQL-only cases under SQLite. Full Ruff, mypy (187 source
  files) and suppression reconciliation pass. No source or test edits occurred
  during this gate. Changed Python files pass the repository formatter.

The original seven implementation commits remain intact. This plan groups the
review corrections into one forward commit; no history rewrite or push.
