# SimpleBroker 8.0 Upgrade Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.5]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-4]; docs/specifications/12-Pipeline_Composition_and_UX.md [PL-3.2]
Superseded by: none

Class: 5: spec-changing and risky. The requested dependency update changes the
normative core and PostgreSQL floors and adopts backend API v8 plus SQL schema
v6. The released default-selection contract also crosses the pipeline compiler,
pipeline task, internal spawn queue, and Manager launch boundary. Hardening is
mandatory because SQL migration is a one-way cutover without a backup restore.

Escalation history: the task began as Class 4 because a coordinated major
dependency update can alter the durable queue path. Reading the governing spec
showed that the supported dependency floors are normative, so the task
escalated to Class 5 before any repository edit.

## Goal

Upgrade Weft to the published `simplebroker` 8.0.0 and `simplebroker-pg` 4.0.0
pair, preserve the existing Weft queue and pipeline contracts, repair the one
proven pipeline child-order dependency, make Manager idle tracking read the
newest pending manager-owned message as its existing contract says, and
document the required cold SQL schema migration and restore-based rollback.

Deployment context: optimize for one controlled internal cutover and the Weft
paths the owner's organization actually runs. Do not add a permanent v7 reader,
mixed-version lane, cross-version test harness, or speculative migration
framework. Retain backup and restore guidance for current data, and verify
Weft-owned runtime paths against the current SQLite and PostgreSQL backends.

## Requested Outcomes

- [x] Raise every root SimpleBroker floor to core 8.0.0 and PostgreSQL 4.0.0.
- [x] Refresh the lockfile with only the coordinated package artifacts and
      generated Weft requirement metadata changed.
- [x] Preserve dependency-friendly pipeline child launch order under
      SimpleBroker 8's ascending public-message-ID selection.
- [x] Use SimpleBroker 8's public newest-first bounded selection for Manager
      idle tracking so a later pending manager-owned message resets activity.
- [x] Update exact floor tests, integration spec text, README guidance, and
      the Unreleased changelog.
- [x] Prove the upgrade through the released artifacts on SQLite and the
      canonical live-PostgreSQL wrapper, then run the full static and default
      suite gates.
- [x] Complete independent plan/spec review before implementation and
      independent work review before any completion claim.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
  owns the dependency floors, backend-neutral queue boundary, isolated config,
  watcher integration, and supported public SimpleBroker surfaces.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.5] requires the final
  idle-timeout decision to refresh manager-owned broker activity rather than
  trust a cached backend-global timestamp.
- `docs/specifications/10-CLI_Interface.md` [CLI-4] owns the delegated raw
  queue JSON contract and its SimpleBroker-major reference.
- `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-3.2] requires
  generated edge and stage children to launch as ordinary waiting tasks on the
  canonical task spine with stable identities.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-4], [MF-6] owns the
  pipeline and internal spawn queue flow. No payload, queue-name, or lifecycle
  change is intended.
- `../simplebroker/CHANGELOG.md` 8.0.0 defines ascending public-ID default
  selection, backend API v8, coordinated extension 4.0.0 floors, SQL schema v6,
  the downtime-only migration, and restore-based rollback.
- `../simplebroker/docs/specs/14-timestamp-selection.md` [SB-SELECT-5] and
  `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-4], [SB-API-7],
  [SB-API-11] define selection order, backend handshake, and schema admission.
- `../simplebroker` immutable tags `v8.0.0` and
  `simplebroker_pg/v4.0.0` resolve to release SHA
  `194dea5bd4841f3c7be36be44f5657e9a20817e1`.
- `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md` is the
  partially landed predecessor for the current 7.5.1/3.10.0 floor. Its public
  closeable-iterator and config-isolation adaptations remain required; this
  plan supersedes only its dependency-floor target and release verification.

## Context and Key Files

Files to modify:

- `pyproject.toml`, `uv.lock`: coordinated core and PG floors plus exact
  registry artifacts.
- `weft/core/pipelines.py`: allocate each generated entry/inter-stage edge TID
  before its downstream stage TID so ascending public-ID selection preserves
  the already-required dependency-friendly launch order.
- `weft/core/manager.py`: request newest-first selection when probing each
  manager-owned input queue for its latest pending timestamp.
- `tests/tasks/test_pipeline_runtime.py`: retain the real queue bootstrap
  regression and make the public-ID ordering premise explicit beside its
  expected `edge, stage, ..., exit edge` launch sequence.
- `tests/core/test_manager.py`: prove two pending manager-owned messages advance
  the idle probe to the newer message ID.
- `tests/system/test_optional_extras.py`: exact core and PG floor assertions,
  including backend API v8 wording.
- `docs/specifications/04-SimpleBroker_Integration.md`: promote the coordinated
  floors, backend API/schema contract, cutover/rollback rule, versioned
  `resolve_isolated_config()` reference, and plan backlink.
- `docs/specifications/10-CLI_Interface.md`: update the delegated raw queue JSON
  major-version reference without changing its shape.
- `README.md`, `CHANGELOG.md`: operator-facing dependency and cold-cutover
  guidance plus the pipeline upgrade fix.
- this plan and `docs/plans/README.md`: execution, review, and evidence record.

Read first:

- `weft/core/pipelines.py::compile_linear_pipeline`: currently generates a
  stage TID before the edge TID that feeds that stage.
- `weft/core/tasks/pipeline.py::_ordered_child_taskspec_payloads`: deliberately
  submits each edge before its downstream stage, with the final exit edge last.
- `weft/core/spawn_requests.py::submit_spawn_request`: exact child TIDs are
  preserved through `Queue.insert_messages()`.
- `tests/tasks/test_pipeline_runtime.py::test_pipeline_task_bootstraps_all_children_before_any_stage_runs`:
  real SQLite proof of the externally observed internal spawn order.
- `bin/pytest-pg`: canonical live-PostgreSQL provisioning and DSN ownership.

Shared paths and boundaries:

- Keep `_ordered_child_taskspec_payloads()` as the single dependency-friendly
  launch-order owner. Do not introduce a second launch path or an alternate
  broker mode.
- Keep exact child TIDs. Do not replace `insert_messages()` with ordinary
  writes, renumber compiled children after validation, or use a private
  insertion-order column.
- Use only `simplebroker`, `simplebroker.ext`, `simplebroker.commands`, and
  package-root `simplebroker_pg` public exports already allowed by the import
  boundary test.
- Use the new bounded `order="newest"` surface only for Manager idle-activity
  probes. The pipeline needs ascending dependency order, and newest-first there
  would reverse the whole graph.

## Root Cause and Reproduction

Root cause hypothesis, confirmed: SimpleBroker 7.5.1 returned exact inserts in
private storage insertion order, while SimpleBroker 8.0.0 returns them in
ascending public message-ID order. `compile_linear_pipeline()` allocates
`stage_tid` then `edge_tid`, but `PipelineTask` submits edge then stage. The
queue therefore exposes stage then edge under v8, violating the existing
pipeline bootstrap contract.

The Manager idle probe has a second, independent ordering mismatch. Its method
contract says it returns the newest pending manager-owned input timestamp, but
the implementation calls default `peek_one()`, which returns the oldest
pending public ID. SimpleBroker 8 provides the public bounded
`order="newest"` selector needed to implement the existing contract directly.

Observed before implementation:

- Locked 7.5.1:
  `env -u PYTHONPATH uv run --frozen pytest -q tests/tasks/test_pipeline_runtime.py -k bootstraps_all_children_before_any_stage_runs`
  passed.
- Published 8.0.0/4.0.0 overlay:
  `env -u PYTHONPATH uv run --frozen --with simplebroker==8.0.0 --with simplebroker-pg==4.0.0 pytest -q tests/tasks/test_pipeline_runtime.py -k bootstraps_all_children_before_any_stage_runs`
  failed at the exact expected child-TID sequence.
- The same overlay also failed
  `test_pipeline_bootstrap_fatal_exit_keeps_primary_when_rollbacks_fail` at
  the same first-two-child ordering assertion.
- The target artifact probe resolved core 8.0.0 and PG 4.0.0 outside the
  sibling checkout, and PG metadata requires `simplebroker>=8.0.0`; public
  backend API is v8.
- The full target overlay completed with 4,306 passed, 5 skipped, and 5 failed.
  The two pipeline failures are the upgrade regression. The other three are
  duplicate policy failures caused by four Ruff findings in the pre-existing
  untracked `tests/benchmarks/test_tid_mapping_capacity_benchmarks.py`; that
  file is outside this slice and must remain untouched.
- A separate temp-target probe under both dependency lines wrote two manager
  inbox messages and `_read_broker_timestamp(force=True)` returned the
  first/older ID. This confirms the Manager method-contract mismatch before
  adopting the v8 selector.

## Invariants and Constraints

- Preserve TID format, nonzero signed-64-bit range, and immutability. Only the
  allocation sequence changes; a compiled child's TID is never rewritten.
- Preserve the canonical
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine.
- Preserve queue names, payload shapes, forward-only lifecycle states,
  reservation policy, TaskSpec immutability, and runtime-only queue handling.
- Preserve pipeline launch intent: each edge is available before the stage
  that waits on it, and the exit edge is submitted last.
- Preserve the internal spawn lane and exact-ID insertion path. No public CLI,
  result, control, or pipeline JSON shape changes.
- Preserve complete isolated `ResolvedConfig` handling and watcher ownership.
  Ambient `BROKER_*` values remain unable to tune or redirect Weft.
- Core 8.0.0 and PG 4.0.0 deploy and roll back as a pair. Backend API v8 does
  not accept the old extension.
- SQL schema v6 is incompatible with v7 clients. Migration requires all old
  clients and sidecar transactions to stop first. Rollback requires restoring
  the whole pre-migration target before reinstalling the prior package pair.
- Keep real SQLite queues and the live PG wrapper in verification. Do not mock
  ordering, queue settlement, watcher behavior, or process lifecycle. Schema
  migration behavior remains owned by SimpleBroker's release suite and is not
  reimplemented as a permanent Weft cross-version harness.
- Preserve unrelated dirty work. In particular, do not rewrite the active TID
  mapping/monitor changes in `weft/_constants.py`, `weft/core/tasks/base.py`,
  monitor modules, their tests, or their specs/plans.
- No unrelated dependency update, formatting sweep, queue abstraction, or
  module split.

## Hardening Comprehension Checks

1. Why does changing insertion order not fix the pipeline? SimpleBroker 8
   selects by public message ID, so exact inserts are exposed by ID regardless
   of the order in which `insert_messages()` is called.
2. Why allocate the edge TID first? The existing child launch contract is edge
   before downstream stage. Making allocation order match that contract lets
   v8's ascending selection preserve it without changing queue semantics.
3. Why not use `order="newest"`? Descending all child IDs would put the exit
   edge and later stages first, which is the opposite of dependency order.
4. Which queue proves the fix? `weft.spawn.internal`, drained through a real
   SimpleBroker Queue after `PipelineTask.process_once()`.
5. What stops rollout? Any live v7 client or sidecar transaction, lack of a
   full target backup, plugin/core version mismatch, changed queue payload or
   lifecycle behavior, or a need for a private SimpleBroker API.

## Spec Baseline

- Weft baseline: `33e1ab767046e6a7e22904d5198840b174552798` for
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/04-SimpleBroker_Integration.md`,
  `docs/specifications/10-CLI_Interface.md`, and
  `docs/specifications/12-Pipeline_Composition_and_UX.md` at plan authoring.
  Spec 04 is clean at that baseline; unrelated worktree edits exist in other
  specs and must be preserved.
- Upstream release baseline:
  `194dea5bd4841f3c7be36be44f5657e9a20817e1`, tagged `v8.0.0` and
  `simplebroker_pg/v4.0.0`, with published wheels independently resolved by
  `uv --with` outside the sibling checkout.
- Plan type: implementation with spec revision.
- Promotion strategy: B, atomic. The floor, SQL cutover warning, package lock,
  pipeline compatibility change, tests, README, and changelog move together;
  no useful supported mixed state exists.
- Promotion baseline identifier:
  `33e1ab767046e6a7e22904d5198840b174552798` plus this uncommitted
  upgrade-owned worktree slice. Pre-existing TID-mapping/Monitor changes and
  untracked benchmark work remain outside the promotion delta.

## Proposed Spec Delta

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1]

Replace the dependency-floor paragraph with:

> Weft requires SimpleBroker 8.0.0 or newer. Installations using the optional
> PostgreSQL backend require `simplebroker-pg` 4.0.0 or newer. These coordinated
> floors provide backend API v8, ascending public-message-ID default selection,
> surrogate-free SQL schema v6, bounded dump watermarks, immutable
> invocation/handle configuration snapshots, typed queue result overloads,
> public closeable queue iterator types, and the synchronized watcher lifecycle
> contract used by Weft.

Append immediately after that paragraph:

> Upgrading a SQLite or PostgreSQL target from the v7 package line to v8 is a
> coordinated cold cutover. Stop all v7 clients and sidecar transactions, take
> a whole-target backup, install the 8.0.0/4.0.0 package pair, migrate and verify
> once, and then restart only v8 clients. V7 and v8 clients must not share a
> schema-v6 target. Rollback requires restoring the complete pre-migration
> target before reinstalling the prior package pair.

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Replace `SimpleBroker 7.5.1's public resolve_isolated_config()` with
`SimpleBroker 8.0.0's public resolve_isolated_config()` and add this plan to the
top implementation links and `## Related Plans`.

No intended pipeline behavior changes. [PL-3.2] already requires stable child
identity and dependency-friendly ordinary child launch; the code change
restores that behavior under the promoted SimpleBroker contract.

### `docs/specifications/10-CLI_Interface.md` [CLI-4]

Replace `Raw delegated queue JSON follows SimpleBroker 7 directly` with
`Raw delegated queue JSON follows SimpleBroker 8 directly` and add this plan
to `## Related Plans`. No JSON shape or CLI behavior changes.

No text change is required in [MA-1.5]. Its forced manager-owned broker
activity refresh is already normative; the Manager code change makes the
existing `newest pending` method contract true using the new public selector.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [SB-0.1] | An exploratory PG cutover probe uses a real PostgreSQL target. | The first probe passed a PostgreSQL URL as a legacy string path, so v7 created a local SQLite artifact instead of opening PG. | Reproduced from the unexpected `postgresql:/` worktree path, moved that generated artifact to Trash, corrected the probe, and reran it successfully before the permanent cross-version harness was removed. | No spec change; this corrected exploratory verification rather than product behavior. |
| [SB-0.1] | Keep a permanent SQLite/PG v7-to-v8 migration harness in Weft. | The owner rejected that scope after reviewing the completed diff, so the helper and slow system test were removed. | Schema migration, sidecar preservation, and old-client rejection are SimpleBroker-owned behavior. Weft retains its ordering regressions, current-backend integration coverage, and cold-cutover guidance. | No spec change; the normative operational boundary remains valid. |

## Tasks

1. Finish the upgrade inventory and plan review.
   - Let the full released-artifact default suite finish and classify every
     failure against the 7.5.1 baseline and current dirty tree.
   - Run full mypy against the v8 overlay to catch public typing drift before
     source edits.
   - Have an independent reviewer inspect this plan, the exact proposed spec
     delta, upstream 8.0.0 changelog, `compile_linear_pipeline()`,
     `_ordered_child_taskspec_payloads()`, the Manager idle probe, and the
     failing tests.
   - Stop if another production assumption, public API removal, or migration
     dependency appears; update the deviation log and plan before editing.

2. Promote the dependency contract and minimal pipeline fix atomically.
   - Raise all manifest floors and update exact floor tests.
   - Run `uv lock --upgrade-package simplebroker --upgrade-package simplebroker-pg`;
     reject unrelated lock churn.
   - In `compile_linear_pipeline()`, generate the edge TID before the
     downstream stage TID. Keep object construction and dependency metadata on
     the same current path. Add a short blast-radius comment at the allocation
     point that names ascending-ID selection and the paired regression.
   - Tighten the existing real-queue pipeline bootstrap test so it states both
     the monotone public-ID premise and observed dependency launch sequence.
   - Add a real-queue Manager test with two pending inbox messages, then pass
     `order="newest"` to the bounded idle probe. Keep backend-global `last_ts`
     excluded.
   - Apply the reviewed spec, README, and changelog text in the same slice.
   - Stop if the change needs ID rewriting, queue-mode changes, private APIs,
     or a second bootstrap path.

3. Verify the smallest behavior, then the full blast radius.
   - Prove the formerly failing pipeline test green under the locked 8.0.0
     artifacts.
   - Run pipeline/compiler, exact-floor, queue, dump/load, context/config,
     watcher, import-boundary, plan-metadata, and spec-hygiene suites.
   - Run the full default suite, full mypy, Ruff, lock/frozen-sync, and diff
     checks.
   - Run the pipeline and nearest shared integration tests through
     `bin/pytest-pg`; the wrapper owns Docker provisioning and environment.
   - Record exact counts, skips, and any dirty-tree contamination. A failing
     gate is evidence to fix or classify, not a result to hide.

4. Reconcile traceability and complete review.
   - Record the promotion baseline identifier and close every deviation row.
   - Verify spec/plan/code backlinks and rerun the repository traceability
     gate from current state.
   - Request independent completed-work review over the exact diff. Reproduce
     findings before accepting them, record dispositions below, and rerun
     affected gates after fixes.
   - Do not mark this plan `completed` or call the work ready to land without a
     user-owned commit. If left uncommitted for review, report the changed file
     list and current evidence without a completion claim.

## Testing Plan

- Red evidence already exists from the published v8 overlay and the real queue
  test. This satisfies failing-test-first without synthesizing a new mock.
- `tests/tasks/test_pipeline_runtime.py` owns the exact regression: compiled
  child IDs must be monotone in dependency-friendly launch order, and draining
  `weft.spawn.internal` must expose that same edge/stage sequence.
- `tests/core/test_manager.py` owns newest pending manager-input selection for
  idle tracking with two real queued messages.
- `tests/core/test_pipelines.py` protects compiler stability and child metadata.
- `tests/system/test_optional_extras.py` enumerates every core/PG manifest floor.
- `tests/architecture/test_import_boundaries.py` prevents a private API fix.
- `tests/commands/test_queue.py`, `tests/commands/test_dump_load.py`,
  `tests/context/`, and `tests/tasks/test_multiqueue_watcher.py` protect the
  public queue, schema-independent dump/load, isolated config, and watcher
  surfaces affected by a major broker release.
- Live PG verification must use `bin/pytest-pg`; an unset ambient DSN is not a
  gap. Do not substitute handwritten `PostgresRunner` fakes for the released
  extension.
- Mixed live v7/v8 operation remains explicitly unsupported upstream and
  unsafe by schema contract. The operator guidance states that boundary; Weft
  does not carry a compatibility lane or duplicate SimpleBroker's migration
  admission tests.

## Verification Commands and Expected Outcomes

Per-task diagnostic and focused gates:

```bash
. ./.envrc
env -u PYTHONPATH uv run --frozen --with simplebroker==8.0.0 \
  --with simplebroker-pg==4.0.0 pytest -q \
  tests/tasks/test_pipeline_runtime.py \
  tests/core/test_manager.py \
  -k 'bootstraps_all_children_before_any_stage_runs or bootstrap_fatal_exit_keeps_primary_when_rollbacks_fail or idle_probe_uses_newest_pending_timestamp'
uv lock --check
uv sync --all-extras --frozen
./.venv/bin/python -m pytest -q \
  tests/system/test_optional_extras.py \
  tests/core/test_manager.py \
  tests/core/test_pipelines.py \
  tests/tasks/test_pipeline_runtime.py
./.venv/bin/python -m pytest -q \
  tests/commands/test_queue.py \
  tests/commands/test_dump_load.py \
  tests/context \
  tests/tasks/test_multiqueue_watcher.py \
  tests/architecture/test_import_boundaries.py \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py
```

Final gates:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check \
  weft/core/manager.py weft/core/pipelines.py \
  tests/core/test_manager.py tests/tasks/test_pipeline_runtime.py \
  tests/system/test_optional_extras.py
./.venv/bin/python bin/pytest-pg --fast \
  tests/system/test_optional_extras.py \
  tests/core/test_pipelines.py \
  tests/core/test_manager.py \
  tests/tasks/test_pipeline_runtime.py \
  tests/commands/test_queue.py
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-sb8-backstitch.json
uv lock --check
git diff --check
```

All targeted product, static, and metadata commands must exit 0. Run full-tree
Ruff as an observation too, but its only accepted nonzero result is the exact
four pre-existing findings in the untracked
`tests/benchmarks/test_tid_mapping_capacity_benchmarks.py`. Record that baseline
verbatim and prove this slice adds no finding; do not edit, suppress, or stage
the unrelated file merely to make the command green. Treat Backstitch the same
way if its known repository baseline is nonzero: record unchanged debt and
prove this slice adds no error or warning rather than claiming a false clean
gate.

Installed-artifact smoke, after frozen sync:

```bash
env -u PYTHONPATH ./.venv/bin/python - <<'PY'
from importlib.metadata import version
from simplebroker.ext import BACKEND_API_VERSION

assert version("simplebroker") == "8.0.0"
assert version("simplebroker-pg") == "4.0.0"
assert BACKEND_API_VERSION == 8
print("SimpleBroker 8 artifact smoke: passed")
PY
```

## Rollout, Rollback, and Observation

Rollout order:

1. Stop every Weft/SimpleBroker v7 process and sidecar transaction using the
   target.
2. Take a whole SQLite file or PostgreSQL database/schema backup that includes
   caller-owned sidecar state.
3. Install core 8.0.0 and PG 4.0.0 together.
4. Open and verify the target once so schema v5 migrates to v6 under the
   backend's migration lock.
5. Restart only v8 processes; observe manager/task startup, queue selection,
   and pipeline child launch/result flow.

Observable success: a normal task reaches terminal state with a readable
result; a two-stage pipeline exposes internal children in
`edge, stage, edge, stage, exit edge` launch order and completes; PG connection
statistics and watcher fan-in still use one existing public backend path.

Rollback: stop all v8 processes, restore the complete pre-migration target,
reinstall the 7.5.1/3.10.0 pair, and restart. Never point v7 at schema v6 and do
not attempt reverse DDL or partial broker-table repair.

One-way door: the first successful SQL schema-v6 migration. The package edit
is reversible; the migrated target is not backward-readable without restore.

## Independent Review Loop

Plan/spec review target: this file, its exact `## Proposed Spec Delta`,
`docs/specifications/03-Manager_Architecture.md` [MA-1.5],
`docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1]/[SB-0.4],
`docs/specifications/10-CLI_Interface.md` [CLI-4],
`docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-3.2], upstream
8.0.0 changelog/specs, `weft/core/pipelines.py`,
`weft/core/tasks/pipeline.py`, `weft/core/manager.py::_read_broker_timestamp`,
and the failing pipeline tests.

Review stance: PASS only if a zero-context engineer can implement the plan
confidently and the plan does not degrade queue, pipeline, schema, or rollback
safety. Flag performative process, missing real-backend proof, or a simpler
correct fix.

Completed-work review target: the exact diff against baseline `33e1ab7`, with
unrelated pre-existing dirty files explicitly excluded. Focus on regressions,
unintended schema promises, wrong TID/launch ordering, missing docs, lock churn,
and verification gaps.

## Fresh-Eyes Self-Review

Author review before independent dispatch:

- The fix is local to the compiler allocation sequence; it does not alter the
  launch reducer or broker selection semantics.
- The plan states the hidden compiler/task/broker coupling and binds it to a
  real queue test.
- Rollout and rollback are explicit, including the schema-v6 one-way door.
- The proposed spec text states both the supported floor and the operational
  cutover rule; no plan-only behavior is left as normative truth.
- The Manager change adopts one new public selector exactly where the existing
  method contract requires newest pending activity; it does not alter queue
  processing order.
- Residual risk: the broad v8 suite and live PG wrapper may expose another
  dependency on private insertion order or schema shape. Implementation must
  stop and revise this plan if that occurs.

The required plan and completed-work reviews both passed.

## Review Log

| Date | Reviewer | Scope | Verdict | Findings and disposition |
|------|----------|-------|---------|--------------------------|
| 2026-08-28 | independent plan reviewer | Plan and proposed spec delta | BLOCKED | Required an executable SQLite/PG v7-to-v8 cutover and negative reopen proof, PG coverage for the Manager selector, and an achievable Ruff gate in the dirty tree. All three were added to the plan; re-review required. |
| 2026-08-28 | independent plan reviewer | Revised plan and proposed spec delta | PASS | Confirmed the temporary-target SQLite/PG cutover proof, Manager PG gate, and dirty-tree Ruff baseline resolve all prior blockers without adding mixed-version or external-user scope. |
| 2026-08-28 | independent completed-work reviewer | Upgrade-owned diff and verification evidence | PASS | Found no blocking issue. Re-ran floors/pipeline/Manager, targeted Ruff, lock, SQLite/PG cutover, metadata/hygiene, import boundaries, queue/config/watcher coverage, mypy, and PG Manager tests. Confirmed the plan remains draft only because the slice is uncommitted. |
| 2026-08-28 | owner scope decision | Permanent cross-version migration tests | REMOVE | Keep Weft-owned ordering and current-backend regressions; rely on SimpleBroker for migration, sidecar-preservation, and old-client schema-admission coverage. Cold-cutover guidance remains. |

## Execution Log

| Date | Slice | Evidence | Result |
|------|-------|----------|--------|
| 2026-08-28 | Baseline and red reproduction | Locked 7.5.1 pipeline regression; published 8.0.0/4.0.0 overlay; installed metadata/API probe | 7.5.1 passed; 8.0.0 failed at exact child sequence; target artifacts and backend API v8 confirmed. |
| 2026-08-28 | Broad target inventory | Full v8 overlay; full v8 mypy; plan metadata; temp-target Manager idle probe under v7 and v8 | 4,306 passed, 5 skipped, 2 upgrade regressions, plus 3 duplicate policy failures from unrelated untracked Ruff debt; mypy and metadata clean; Manager returned the older pending ID under both lines. |
| 2026-08-28 | Dependency and runtime slice | Manifest/lock diff; focused floor, Manager, and pipeline tests; installed-artifact smoke | Lock changed only core 7.5.1 to 8.0.0, PG 3.10.0 to 4.0.0, and Weft requirement metadata; 15 focused tests passed; backend API v8 confirmed. |
| 2026-08-28 | Exploratory cutover probe | Temporary SQLite and disposable PostgreSQL 18 targets | Both probes passed after the initial false-PG setup was corrected. The permanent cross-version harness was later removed by owner direction because this migration behavior belongs to SimpleBroker. |
| 2026-08-28 | Focused blast radius | Pipeline/Manager/compiler/floor group; queue/dump/context/watcher/import/spec group; full mypy; targeted Ruff | All focused tests passed with backend-specific skips only; 187-source mypy and every touched Python file are clean. |
| 2026-08-28 | Full default and live PG | Default pytest; `bin/pytest-pg --fast` over floor/compiler/Manager/pipeline/queue | Default: 4,309 passed, 5 skipped, with only 3 Ruff-policy failures caused by the unrelated untracked benchmark's four findings. Live PG: 426 passed, 11 SQLite-specific skips. |
| 2026-08-28 | Traceability observation | Backstitch current tree versus `33e1ab7` archive | Existing baseline remains nonzero (27 errors); this slice adds no error or warning fingerprint in touched specs 04 or 10. |
| 2026-08-28 | Owner-requested close | Removed the permanent cross-version migration harness; reran Weft-owned upgrade tests, targeted Ruff, lock, and diff checks | Relevant tests passed with three expected backend skips; all remaining close gates passed. |

## Out of Scope

- Adopting SimpleBroker's new CLI `--newest` feature or changing normal Weft
  queue-processing order. Only the Manager's bounded idle probe selects newest.
- Redesigning pipelines, internal spawn scheduling, Manager capacity, queue
  cleanup, or Monitor storage.
- Supporting live mixed v7/v8 clients or adding a reverse schema migration.
- Supporting caller modifications inside SimpleBroker-reserved tables,
  indexes, or constraints. Caller-owned sidecar tables remain protected by
  whole-target backup/restore guidance.
- Updating `simplebroker-redis`, which Weft does not declare.
- Completing, refactoring, or landing unrelated dirty TID-mapping work.
