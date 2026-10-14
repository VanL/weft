# Supervisor terminal observations in operational collations

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.7]
Superseded by: none

Class: 5. Extends operational terminal projection across control evidence, durable collation and lifetime reporting. Hardening applies; no change to execution authority or default deletion policy.
Plan type: implementation with spec revision. This is the Weft-owned slice of [the ops recovery plan](../../../mm-governance/docs/plans/2026-10-13-ops-indexer-capture-and-result-recovery.md); it proposes work, not implementation or release authorization.

## Goal and evidence

Make TaskMonitor's operational status agree with exact retained supervisor failure evidence already recognized by the public task read model. Reuse one bounded reconciliation path for historical and future cases. Preserve the distinction between a supervisor observation and a task-owned lifecycle event.

Baseline: `af844d56096b5106b0c87f043f2cbc2b3c8a7bb4` (v0.9.110). Read-only ops inspection on October 13 evening CDT found 37 collations still `running`, each with an exact failed manager terminal in standard `ctrl_out`, and none with a current task-state row. Example `1791893046068387840`: running lifecycle last seen October 13 12:04:07.408 UTC, manager terminal failure October 13 12:05:51.047 UTC. That positive terminal message, not absent state, is the repair authority.

What changed: failure load exposed an older publication split. `Manager._write_manager_terminal_envelope()` has published local-only fallback since `d94d037a0` (April 30). The durable collation introduced in `72bc99dc` (May 16) ingests `weft.log.tasks`. October 12 `185a7c7b` improved task-owned terminal transport and fallback retries but did not bridge supervisor control into monitor status. No recent result-consumption regression was found. The owning application separately addresses abandoned nested results; Weft does not adopt application artifacts or create Django receipts.

- [ ] Exact supervisor failure updates the operational collation without fabricating a task-owned event.
- [ ] Original source, control queue/message ID, reported/observed times and bounded error remain distinguishable in summaries and durable reports.
- [ ] Late task-owned terminal evidence takes precedence even when its message timestamp is older than the supervisor observation.
- [ ] Maintenance is bounded/fair and independent of global-log ingestion catchup; read failures remain retryable.
- [ ] The projection neither consumes outboxes nor changes default reserved-input deletion authority.

## Context and invariants

Read AGENTS and the required context order, planning/hardening/review/testing runbooks, then [MA-1.6a], [MF-3]/[MF-5], [OBS.13] and its cleanup subclauses. Check the standing invariants and lessons before edits.

Owners: `core/task_evidence.py::peek_terminal_ctrl_out_evidence`, `coerce_terminal_envelope`, `select_terminal_envelope`, `reconciliation_for_terminal_ctrl_out` already validate exact TID, inspect without consuming and prefer task-owned proof. `core/monitor/task_monitor.py` owns bounded maintenance/service-worker scheduling and `_ingest_retained_task_log_rows`; `core/monitor/store.py` owns durable collation merge; `collation.py` owns pure reduction; `runtime.py` already classifies `wrapper_lost`; `lifetime_report.py` and `external_log.py` own structured report construction/export. Reuse these owners and configured standard task queues. Do not import commands into core or clone terminal parsing.

Keep task-state deletion solely in LivenessMonitor. Keep Manager publication local-only, and do not synthesize `work_failed` in the task's global log. Missing process/state, elapsed age and historical Manager cancellation prove no task verdict. Do not add a service, verdict table, new config knob, namespace scan per reactor turn, or result consumer to TaskMonitor. Maintain raw global-log message bounds/high-water bookkeeping and retry/export-before-delete rules.

Comprehension: why does public `wrapper_lost` already classify failed while collation says running? Why must a newer supervisor observation lose to a task-owned terminal? Why may status change while terminal task-log cleanup proof remains absent?

## Proposed spec delta

Promotion strategy **A**: independent review, then governing text/backlinks before code. Record the promotion identifier. These draft paragraphs are not the governing contract until promotion.

Append under [MF-5] operational Monitor projection, and synchronize [MA-1.6a] and [OBS.13]:

> TaskMonitor may reconcile an existing nonterminal operational collation from exact typed terminal evidence in that task's configured standard ctrl_out, using the same evidence selection and task-owned-before-supervisor precedence as the public task read model. Project the selected envelope's outcome and retain its source, including task-owned envelopes available only in ctrl_out. A manager wrapper-loss observation projects failure with source=manager, its original control message identity, and separate reported/observed time. Missing runtime state or process alone supplies no terminal outcome. A later task-owned terminal verdict supersedes the supervisor projection regardless of their timestamp order. Ordinary nonterminal lifecycle updates must not demote an already observed control-terminal outcome.

> This operational projection changes operational status and provenance in diagnostics only. It leaves terminal_seen, terminal_status, terminal_event, terminal_message_id and reserved_probe_needed unchanged, preserving the absence of task-owned terminal task-log evidence. It does not manufacture a lifecycle event, advance global-log bounds/high-water marks with a control-queue ID, consume a result, or grant additional inbox/reserved deletion authority under [OBS.13.7]. Structured lifetime reporting explicitly distinguishes the supervisor observation from a task-owned outcome and retains its exact provenance. Existing summary export/verification gates apply before any relevant evidence is removed.

> Reconciliation uses the existing Monitor maintenance cadence and configured batch bound, rotating candidates so an unavailable or still-live family does not starve retained failures. It runs independently of global task-log catchup. A probe failure preserves the collation/evidence and is reported through existing maintenance diagnostics; it remains retryable. No additional watcher or polling service is introduced.

## Implementation slices

1. Review/promote the delta, preserving [OBS.13.7]. Add plan backlinks and later reciprocal owner mappings when code exists. Stop if projection is being used to broaden reserved deletion or override task-owned evidence.
2. Add a small pure control-observation reducer beside existing collation reduction; reuse `peek_terminal_ctrl_out_evidence()` with the existing broker scope. Extend the store with an exact-TID projection operation. Write operational `status` and source identity in existing `diagnostics` JSON only, rather than a new table/schema or fabricated lifecycle update. Leave every `terminal_*` field and `reserved_probe_needed` unchanged; keep first/last raw log IDs unchanged. Add one explicit merge branch that retains the projected control outcome against subsequent running updates and permits task-owned terminal proof to supersede it. Project whichever envelope the shared selector chooses, not only manager failure. Use the same persisted provenance in `to_summary()` and `lifetime_report.py`; do not reinterpret supervisor evidence as task-authored completion.
3. Use one candidate set, `terminal_seen=0 AND disposition_at_ns IS NULL`, through a bounded, rotating maintenance batch. This already includes previously control-projected rows; no second tracking set is needed. Exclude ordinary task-log terminal families. Reuse the current maintenance worker/scheduling owner; no queue enumeration or complete history replay per candidate or admission decision. A failed item is retryable without freezing the batch. Rotation may use a process-local cursor and the existing store page selection, not a new durable schedule. Use the existing lifetime summary/export path, with original control message identity and bounded error, rather than a separate report or a new high-water exception. Current cleanup requires verified summary emission before deleting control evidence. Stop if this requires producer changes, a separate service or a new durable verdict queue.
4. Preserve the control observation through its verified report and current cleanup fences. Do not silently mark a supervisor-only family as eligible for task-log-terminal reserved cleanup. Test summary/report provenance, task-owned precedence, safe reads and repeated cycles. Synchronize spec implementation mappings/README/lessons and the Monitor implementation docs. One path handles the retained 37 examples and new wrapper-loss cases; no bespoke date-bound backfill.

## Tests and verification

Use existing Weft broker fixtures/harness and Monitor service workers; keep real broker rows and store transactions. Primary regression: a running task-log family, one exact manager failure ctrl_out, no current state queue, then one due Monitor maintenance cycle produces operational failed status with manager provenance. It must fail on baseline. The absence of ctrl_out must leave the family nonterminal. Table cases cover malformed/mismatched envelopes, read failure, repeated/restart processing, late task-owned success with older timestamp, a running log update after supervisor projection, and candidate rotation past a blocked family. Only external writer/OS fault boundaries may be substituted; do not mock the selector/reducer under test.

Assert global message bounds, every `terminal_*` field and `reserved_probe_needed` stay unchanged for control-only projection, reserved/inbox/outbox rows remain, and original local evidence cannot be removed before the summary's existing durable-write verification. Cover a task-owned ctrl_out-only envelope as well as supervisor failure. Test the same owner path against PostgreSQL. Do not add tests that assert the one-time cohort size, deployment choreography or private source text.

```bash
# Weft root: source .envrc first; clear a calling repo's Django selector for tests.
env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py tests/commands/test_task_evidence.py -q
env -u DJANGO_SETTINGS_MODULE ./bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py tests/commands/test_task_evidence.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest -m ""
env -u DJANGO_SETTINGS_MODULE ./bin/pytest-pg --all
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q -n 0
git diff --check
../backstitch/.venv/bin/backstitch check --repo-root . --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --test-root tests --format json
```

Confirm each named test path exists before adding coverage; put new cases in the actual owning module if a test layout changes. Record existing traceability debt from a current rerun and compare stable issue identities; do not claim global green while it fails. Use normal release/CI gates when shipping is authorized. Live proof: retained exact supervisor cases project failed with source; new wrapper loss is visible within bounded maintenance; actual task-owned late evidence still wins. Services merely being up is insufficient.

## Rollout, review and deviation log

No broker schema or producer transport change is planned. The extra JSON provenance is additive. Deploy the normal Weft release and advance mm-governance's lock normally. A forward corrective release may revert the projection behavior; retained logs remain honest historical observations, not recreated task events. Do not restore data or delete failure evidence. This plan is independent of receipt-backed nested result custody and indexer capacity relief.

Independent plan and completed-work review must check the exact proposed delta against the named baseline, current specs and cleanup invariants. Accepted risks: sampled bounded reconciliation latency, conservative reserved retention and unresolved tasks when no positive evidence survives. Pre-existing unrelated issues are observations unless worsened. Prefer simpler reuse and removal of extra machinery. Author fresh eyes must check producer/consumer authority, timestamp precedence and raw-log bookkeeping separately after draft. Different-family review is preferred when available; record availability and dispositions.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Evidence and review record

Append plan checks/review dispositions here; implementation completion requires current runtime gates, promotion identifier and live efficacy. No runtime efficacy is claimed by authoring this plan.

### Planning review record (October 13 CDT)

Reviewed source baseline: `af844d56096b5106b0c87f043f2cbc2b3c8a7bb4`. Reviewed task text before appending this record: SHA-256 `9ce46ade11e721a2b6defc4112519b8d5b73e498fffc6961ebf250d55a0049d4`. This uncommitted proposed plan is not implementation approval; pin the committed reviewed plan baseline before implementation.

Independent same-family reviewer `ops_plan_review` and available different-family Claude CLI reviewer both returned scoped PASS after corrections. Claude was restricted to read-only tools. The shared review unit was this plan and its linked application plan; reviews checked correctness, implementability and simpler reuse, not unrelated stack redesign.

| Finding | Disposition |
| --- | --- |
| F6: terminal fields can grant false task-log/cleanup authority | Accepted: operational status/diagnostics only; preserve all `terminal_*`, `reserved_probe_needed` and raw log bounds. Explicit merge branch protects status without proof inflation. |
| F7: selected task-owned ctrl_out-only outcome unspecified | Accepted: project the shared selector's chosen envelope, preserving source, including task-owned control-only outcomes. |
| F8: second growing projected-candidate category | Accepted simplification: one undisposed `terminal_seen=0` candidate set. |
| F9: extra report and high-water exception | Accepted simplification: existing lifetime summary/export fence, not a separate reporting path. |

Fresh-Eyes Review: fixed. Checked local versus global publication history, positive exact-TID evidence versus absent runtime state, task-owned precedence, ordinary running-update merges, every cleanup/proof field, candidate rotation and control-evidence export fence. No new producer event, deletion entitlement, consumer, durable cursor or polling service.

Planning checks: current `git diff --check`; `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q -n 0` (six passing tests). Confirmed named owning test paths. Runtime and traceability gates above remain required during implementation; they were not run or represented as green by authoring the plan.

### Revision Log

| Date | Trigger | Change | Class re-check |
| --- | --- | --- | --- |
| 2026-10-13 | Claude F6–F9 and author fresh eyes | Made all proof-field preservation explicit; include selected task-owned control evidence; remove second candidate category and extra report. Both scoped independent reviews repeated. | Remains Class 5; no broker schema/producer change or deletion-policy expansion. |
