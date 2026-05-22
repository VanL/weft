# Prune Path Unification Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]; docs/specifications/10-CLI_Interface.md [CLI-6]
Superseded by: none

## 1. Goal

Unify Weft pruning so the CLI and manager-supervised `TaskMonitor` use one
canonical candidate-selection and exact-delete path. The current foreground
`weft system prune --family task-log` path can identify safe task-log retention
candidates that the TaskMonitor service misses because TaskMonitor builds
cleanup candidates from an incremental task-log checkpoint window. This plan
replaces duplicated pruning machinery with one core prune engine and makes
caller variation explicit through configuration arguments.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3]: defines
  `TaskMonitor` as an operational task-log monitor whose default `delete`
  processor may delete exact safe cleanup candidates only.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a]: defines
  manager-owned internal service supervision and states that the manager
  supervises TaskMonitor but does not run monitor processors itself.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: defines
  state observation, task-monitor operational output, prune behavior, and the
  cleanup boundary.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: define operational-output boundaries, runtime-state prune scope,
  retention prune safety, exact-message deletion, and evidence that must not be
  deleted.
- `docs/specifications/10-CLI_Interface.md` [CLI-6]: defines `weft system
  prune` flags, default dry-run behavior, archive requirements, force
  semantics, and JSON/report output.
- `docs/plans/2026-05-07-runtime-state-pruning-plan.md`: historical plan for
  explicit runtime-state pruning. It is completed context, not a second
  implementation target.
- `docs/plans/2026-05-07-task-local-reaper-retention-policy-plan.md`:
  historical plan for task-log and task-local retention pruning. It is
  completed context, not a second implementation target.
- `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`:
  active/draft context for manager-supervised TaskMonitor cleanup. This plan
  narrows its cleanup implementation to one shared prune path.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`,
  `docs/agent-context/runbooks/testing-patterns.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  planning, hardening, testing, and review guidance for boundary-crossing
  cleanup work.

## 3. Context and Key Files

Files to modify:

- `weft/core/pruning.py` or `weft/core/pruning/__init__.py`: new canonical
  prune engine. Use a package only if a single module becomes clearly too
  large during implementation; do not split preemptively.
- `weft/commands/runtime_prune.py`: convert to a CLI/command wrapper around
  the canonical engine, preserving current public output.
- `weft/commands/retention_prune.py`: convert to a CLI/command wrapper around
  the canonical engine, preserving current public output and archive/report
  behavior.
- `weft/core/task_monitoring.py`: remove cleanup-candidate discovery and raw
  queue deletion duplication. Keep lifecycle/anomaly candidate types here only
  if they remain TaskMonitor-specific.
- `weft/core/tasks/task_monitor.py`: call the canonical prune engine for
  cleanup work. Keep lifecycle observation separate from destructive cleanup.
- `weft/_constants.py`: move or reuse prune class/default constants from one
  place. Do not create duplicate class-name constants in the new engine.
- `tests/commands/test_runtime_prune.py`,
  `tests/commands/test_retention_prune.py`,
  `tests/cli/test_cli_system.py`: update command/CLI tests so they prove the
  wrapper behavior still matches the public contract through the shared engine.
- `tests/core/test_task_monitoring.py` and, if needed,
  `tests/core/test_manager.py`: add real broker-backed TaskMonitor regressions
  proving monitor cleanup selects the same candidates as CLI pruning.
- `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/07-System_Invariants.md`, and
  `docs/specifications/10-CLI_Interface.md`: update implementation mappings
  and related-plan backlinks after the code lands.
- `docs/plans/README.md`: add this plan row and keep metadata tests green.
- `docs/lessons.md`: add a durable lesson if implementation confirms the
  duplicate-path failure mode.

Read first:

- `weft/commands/retention_prune.py`: current full-history retention candidate
  selector and exact-delete apply loop. This is the most correct current
  task-log retention behavior.
- `weft/commands/runtime_prune.py`: current runtime-state candidate selector
  and exact-delete apply loop.
- `weft/core/task_monitoring.py`: current duplicated TaskMonitor cleanup
  candidates and `delete_processor`.
- `weft/core/tasks/task_monitor.py`: current persistent monitor cycle,
  checkpoint behavior, processor dispatch, and control snapshot fields.
- `weft/helpers.py`: queue-history iterators. Prune history reads must use
  generator-based iteration, not guessed fixed `peek_many()` windows.
- `tests/helpers/weft_harness.py`: preferred real broker/process harness.
- The source specs named above, especially `docs/specifications/05-Message_Flow_and_State.md`
  [MF-5] and `docs/specifications/07-System_Invariants.md` [OBS.13],
  [OBS.16], [OBS.17].

Current structure:

- The CLI path is split by family:
  - `weft/commands/runtime_prune.py` owns runtime-only `weft.state.*`
    candidate selection and deletion.
  - `weft/commands/retention_prune.py` owns task-log/task-local retention
    candidate selection, archive-before-delete behavior, and deletion.
- The TaskMonitor path is separate:
  - `weft/core/task_monitoring.py::build_task_monitor_cycle_snapshot()` scans
    task-log rows by `since_timestamp` and `limit`.
  - It derives retention cleanup candidates from only those scanned rows.
  - `weft/core/task_monitoring.py::delete_processor()` opens queues and
    deletes exact safe `TaskMonitorCandidate` rows itself.
- This creates two correctness problems:
  - task-log retention proof is batch-local in TaskMonitor but full-history in
    foreground prune;
  - exact-message deletion is implemented in more than one place.

Comprehension checks before editing:

- Can you explain why a task-log row scanned before its later terminal row may
  be permanently skipped by the current TaskMonitor checkpoint path?
- Can you name which pruning semantics are CLI-only policy
  (`--archive`, `--force`, report output) and which are core safety semantics
  (candidate classes, exact message IDs, queue scope, dry-run/apply)?
- Can you identify why the canonical engine cannot live in `weft/commands/`
  if `TaskMonitor` in `weft/core/tasks/` must use it?

## 4. Invariants and Constraints

- There must be one canonical candidate-selection path and one canonical
  exact-message deletion path. CLI and TaskMonitor may pass different policy
  arguments, but they must not maintain separate pruning algorithms.
- The canonical prune engine must live below both command wrappers and
  `TaskMonitor`, under `weft/core/`. Do not import `weft.commands.*` from
  `weft/core.*`.
- Keep `weft system prune` public CLI behavior stable:
  - default family remains `runtime-state`;
  - default mode remains dry-run;
  - `--apply` is required for deletion;
  - ordinary retention apply still requires archive records before deletion;
  - `--force --apply` remains a human override for ordinary retention
    protections only;
  - JSON summary keys must remain compatible unless a spec update explicitly
    changes them.
- Keep TaskMonitor cleanup operational only. TaskMonitor output, checkpoints,
  classifications, and prune summaries must not become lifecycle truth, status
  authority, or result authority.
- TaskMonitor with `processor=delete` may delete exact safe candidates only.
  It must not apply force-only retention cleanup, archive side effects, logging
  callbacks, unclaiming, moves, or recovery-sensitive deletion.
- TaskMonitor with `processor=report_only` must run the same candidate
  discovery path in non-apply mode. It must not call queue `delete()`.
- `jsonl_then_delete` remains fail-closed until the logging callback lands.
- Runtime-only `weft.state.*` queues remain runtime-only. Do not include them
  in dump persistence, retention archives, or task lifecycle truth.
- Retention cleanup may touch only the selected exact message IDs in
  `weft.log.tasks` and `T{tid}.*` task-local queues named by the spec.
- Runtime-state cleanup may touch only supported `weft.state.*` queues named by
  the spec and current CLI contract.
- Do not change queue names, TID format, TaskSpec schema, result payloads,
  reserved-queue policy, or manager supervision semantics in this slice.
- Do not introduce a new dependency.
- Do not solve performance with an approximate task-log window. Task-log
  retention correctness requires enough history to prove supersession. If the
  full scan is too expensive, stop and design an index with its own plan rather
  than reintroducing lossy cleanup.
- Do not use mock queues to prove prune correctness. Use real broker-backed
  queues through `WeftTestHarness` or `broker_env`.
- Stop and re-plan if the implementation starts adding a second monitor-only
  cleanup engine, a second CLI-only delete loop, or caller-specific candidate
  semantics hidden behind `if caller == ...` instead of explicit policy
  arguments.

## 5. Proposed Canonical Shape

Create one core prune engine with explicit policy inputs. The exact names can
change during implementation if the final API is cleaner, but the ownership
must not change.

Suggested module:

```text
weft/core/pruning.py
```

Suggested public core types:

```python
PruneFamily = Literal[
    "runtime-state",
    "task-log",
    "task-local",
    "retention",
    "all",
]

PruneApplication = Literal["dry_run", "delete"]
PruneArchivePolicy = Literal["not_allowed", "required", "best_effort"]
PruneForcePolicy = Literal["disabled", "enabled"]

@dataclass(frozen=True, slots=True)
class PruneConfig:
    family: PruneFamily
    application: PruneApplication
    force: bool
    runtime_queues: tuple[str, ...]
    task_filters: tuple[str, ...]
    class_filters: tuple[str, ...]
    min_age_seconds: float
    keep_recent_per_key: int
    keep_recent_per_task: int
    limit: int | None
    exclude_tids: tuple[str, ...] = ()
    archive_policy: PruneArchivePolicy = "not_allowed"
```

Suggested callable flow:

```text
run_prune(ctx, config, before_delete=None)
  -> validate config
  -> build candidates through one canonical selector
  -> apply filters and limit through one canonical sorter
  -> if dry_run: return result
  -> if delete: call optional before_delete(result-with-candidates)
  -> delete exact candidates through one canonical apply loop
  -> return result with applied candidates and errors
```

Variation belongs in arguments:

- CLI runtime prune passes `family="runtime-state"`, `application` from
  `--apply`, runtime queue filters from `--queue`, and no archive callback.
- CLI retention prune passes `family="task-log"`, `"task-local"`, or
  `"retention"`, `application` from `--apply`, force from `--force`, and an
  archive-before-delete callback when ordinary retention apply requires it.
- CLI `--family all` calls the same engine twice or once with `family="all"`.
  Choose the simpler option after reviewing output compatibility, but do not
  duplicate selection/deletion logic.
- TaskMonitor passes a monitor-specific config:
  - `family="all"` or explicit runtime-state plus retention families;
  - `application="delete"` only when built-in processor is `delete`;
  - `application="dry_run"` when built-in processor is `report_only`;
  - `force=False`;
  - `archive_policy="not_allowed"`;
  - `limit=batch_size`;
  - `exclude_tids=(monitor_tid,)`.

Do not encode these as separate engines. They are one engine with different
configuration.

## 6. Tasks

1. Add a red regression that proves TaskMonitor and CLI disagree today.
   - Outcome: a failing test captures the production bug before code changes.
   - Files to touch:
     - `tests/core/test_task_monitoring.py`
     - optionally `tests/commands/test_retention_prune.py` for a shared
       fixture/helper if it avoids duplication.
   - Read first:
     - existing `_write_log`, `_write_json`, and queue helper tests in
       `tests/core/test_task_monitoring.py`;
     - `tests/commands/test_retention_prune.py` for current foreground prune
       expectations.
   - Test shape:
     - Use `WeftTestHarness` or real broker-backed queues.
     - Write a task-log row for TID `A` that is old enough for retention.
     - Write enough unrelated task-log rows to ensure row `A` and its later
       terminal/proving row do not land in the same small TaskMonitor batch.
     - Write the later terminal row for TID `A`.
     - Prove foreground retention prune reports row `A` as
       `nonterminal_task_log_superseded`.
     - Prove the current TaskMonitor cleanup path does not delete or report
       the same candidate after advancing its checkpoint through batches.
   - Do not mock `build_task_monitor_cycle_snapshot()` or queue deletes.
   - Stop and re-evaluate if the test can pass by asserting on private
     function call counts instead of queue-visible candidate/deletion behavior.
   - Done signal: the test fails on the current duplicated implementation for
     the expected reason.

2. Introduce the core prune data model and pure result conversion helpers.
   - Outcome: there is one candidate/result schema rich enough to represent
     existing runtime-state and retention candidates without losing public CLI
     output fields.
   - Files to touch:
     - `weft/core/pruning.py`
     - `tests/core/test_pruning.py` or a similarly named new core test file.
   - Read first:
     - dataclasses in `weft/commands/runtime_prune.py`;
     - dataclasses in `weft/commands/retention_prune.py`.
   - Required design:
     - Use dataclasses with `frozen=True, slots=True`.
     - Represent family, queue, message ID, TID/key, candidate class, reason,
       safe ordinary deletion, report-only/force-only status, payload hash,
       payload size, and apply result.
     - Include enough metadata to reproduce current JSON summaries and reports.
     - Keep type hints complete and use `collections.abc` types where
       appropriate.
   - Do not add Pydantic models for this internal runtime path.
   - Do not keep separate runtime and retention candidate dataclasses after the
     migration unless they are thin compatibility views.
   - Tests:
     - candidate apply-result copying preserves identity fields;
     - candidate sorting is deterministic;
     - family/class filters behave identically for runtime and retention
       candidates.
   - Done signal: the new model has no queue side effects and passes focused
     pure tests.

3. Move runtime-state candidate discovery into the core engine.
   - Outcome: runtime-state candidate selection is canonical and no longer
     owned by `weft/commands/runtime_prune.py`.
   - Files to touch:
     - `weft/core/pruning.py`
     - `weft/commands/runtime_prune.py`
     - `tests/commands/test_runtime_prune.py`
   - Read first:
     - `weft/commands/runtime_prune.py::_build_candidates`;
     - existing tests for stale managers, superseded mappings, endpoints,
       streaming, and pipelines.
   - Required approach:
     - Move selection logic mechanically first. Preserve names/classes/reasons.
     - Keep command-layer rendering in `weft/commands/runtime_prune.py`.
     - Make the command wrapper call `run_prune()` or
       `build_prune_candidates()` through `WeftContext`.
     - Runtime prune must still never touch `weft.log.tasks`, task-local
       queues, spawn queues, or manager control queues.
   - Do not opportunistically redesign manager liveness checks in this task.
   - Tests:
     - existing runtime prune tests should still pass;
     - add one core-level test proving the engine returns the same candidate
       class and exact message ID as the old command contract for a superseded
       TID mapping.
   - Done signal: runtime prune command output is unchanged while selection
     comes from the core engine.

4. Move retention candidate discovery into the core engine.
   - Outcome: task-log and task-local retention candidate selection is
     canonical and no longer owned by `weft/commands/retention_prune.py`.
   - Files to touch:
     - `weft/core/pruning.py`
     - `weft/commands/retention_prune.py`
     - `tests/commands/test_retention_prune.py`
   - Read first:
     - `weft/commands/retention_prune.py::_read_task_log_rows`;
     - `_task_log_candidates`;
     - `_task_local_candidates`;
     - `_ctrl_out_candidates`;
     - `_outbox_candidates`;
     - `_ctrl_in_candidates`;
     - `_work_queue_candidates`.
   - Required approach:
     - Preserve full-history task-log retention proof. Do not use
       TaskMonitor's checkpoint window for this logic.
     - Preserve `keep_recent_per_task`, newest-terminal protection, age
       protection, active-manager protection, report-only classes, and force
       metadata.
     - Preserve task-local queue scope and exact-message IDs.
     - Preserve claimed outbox residue as recovery evidence, not ordinary
       deletion.
     - Keep archive/report rendering in command code unless moving it into the
       core engine clearly reduces duplication without pulling CLI formatting
       into the runtime path.
   - Do not add a separate "fast monitor retention scan" that answers a
     different question from foreground prune.
   - Tests:
     - existing retention prune tests should still pass;
     - add a core test where an old nonterminal log row is superseded by a
       later terminal row beyond a small artificial batch size, proving the
       engine still selects the old row.
   - Done signal: foreground `task-log`, `task-local`, and `retention` command
     behavior is unchanged while candidate discovery comes from the core
     engine.

5. Move exact-message deletion into one canonical apply loop.
   - Outcome: every destructive prune path uses the same queue grouping,
     persistence selection, exact message ID delete call, and apply-result
     recording.
   - Files to touch:
     - `weft/core/pruning.py`
     - `weft/commands/runtime_prune.py`
     - `weft/commands/retention_prune.py`
     - `weft/core/task_monitoring.py`
     - tests named above.
   - Required approach:
     - The core apply loop must group candidates by queue.
     - It must open `*.outbox` queues with `persistent=True`; other selected
       prune queues remain non-persistent unless existing code says otherwise.
     - It must call `queue.delete(message_id=...)` only for exact safe
       candidates selected by config.
     - It must treat missing rows as non-fatal idempotent misses unless the
       existing CLI contract requires an error.
     - It must return per-candidate applied/error state so CLI summaries and
       TaskMonitor control snapshots can report what happened.
   - Archive behavior:
     - Ordinary CLI retention apply must still write archive records before
       deletion.
     - Represent this as an explicit pre-delete callback or policy argument to
       the canonical engine.
     - TaskMonitor must pass no archive callback and must not write retention
       archives.
   - Do not leave `retention_prune._apply_candidates`,
     `runtime_prune._apply_candidates`, and
     `task_monitoring.delete_processor` as independent delete loops.
   - Tests:
     - a shared engine test deletes exact rows from a normal queue and a
       persistent `.outbox`;
     - command tests prove archive failure still prevents ordinary retention
       deletion;
     - TaskMonitor delete proof observes actual queue count decrease.
   - Done signal: `rg "def _apply_candidates|def delete_processor"` shows no
     duplicate destructive loop except thin wrappers delegating to the core
     engine.

6. Rewire CLI command wrappers to the canonical engine.
   - Outcome: public CLI and command APIs remain stable while all pruning
     behavior comes from the core engine.
   - Files to touch:
     - `weft/commands/runtime_prune.py`
     - `weft/commands/retention_prune.py`
     - `weft/cli/app.py` only if option validation has to move to preserve
       shared config semantics.
     - `tests/cli/test_cli_system.py`
   - Required approach:
     - Keep `cmd_prune()` and `cmd_retention_prune()` as public command-layer
       adapters.
     - Normalize CLI arguments into `PruneConfig`.
     - Convert `PruneResult` back into the existing summary/human/report
       output.
     - Preserve current exit codes and validation messages unless tests and
       specs are deliberately updated.
   - Do not make CLI import TaskMonitor internals.
   - Do not remove existing public command functions in this slice.
   - Tests:
     - existing CLI prune tests pass unchanged or with minimal updates for
       implementation mapping only;
     - `--family all` still reports runtime and retention summaries in the
       current shape;
     - invalid `--force` and invalid queue/class options still fail before any
       deletion.
   - Done signal: CLI behavior is stable and all prune commands delegate to the
     core engine.

7. Rewire TaskMonitor cleanup to the canonical engine.
   - Outcome: TaskMonitor report-only/delete modes use the same candidate
     selection and exact-delete path as CLI prune, with monitor-safe policy
     arguments.
   - Files to touch:
     - `weft/core/tasks/task_monitor.py`
     - `weft/core/task_monitoring.py`
     - `tests/core/test_task_monitoring.py`
   - Required approach:
     - Separate lifecycle/anomaly observation from cleanup.
     - Keep `build_task_monitor_cycle_snapshot()` or its successor for
       lifecycle candidates only, or add an explicit `include_cleanup=False`
       default for monitor lifecycle scans.
     - Add a TaskMonitor cleanup call that builds a `PruneConfig` and invokes
       the core engine.
     - Map `processor=delete` to `application="delete"`.
     - Map `processor=report_only` to `application="dry_run"`.
     - Keep `processor=jsonl_then_delete` fail-closed.
     - For custom `module:function` processors, keep them observational unless
       the spec is explicitly expanded. Destructive cleanup in built-in
       TaskMonitor mode must go through the core prune engine.
     - Exclude the monitor's own TID from task-log/task-local cleanup.
     - Force must be disabled.
     - Archive/logging callbacks must be disabled.
   - Control/status fields:
     - Report last prune candidates, deleted count, errors, warnings, and
       family/class counts from the canonical `PruneResult`.
     - Add phase activity such as `cleanup_scanning`, `cleanup_deleting`, and
       `waiting` if needed so ops can distinguish "alive but scanning" from
       "completed a cleanup cycle".
   - Do not let the TaskMonitor operational checkpoint decide whether a
     retention row is safe to delete. The checkpoint may control lifecycle log
     observation output; retention cleanup must use the canonical prune engine.
   - Tests:
     - the red regression from task 1 turns green;
     - `report_only` discovers the same candidates but leaves rows untouched;
     - `delete` removes exact candidates from `weft.log.tasks`,
       `weft.state.tid_mappings`, and persistent `.outbox` when those
       candidates are safe;
     - unsafe/force-only/report-only retention classes are not deleted by
       TaskMonitor.
   - Done signal: TaskMonitor can reduce the same first safe task-log
     candidates that foreground prune reports under matching defaults.

8. Remove duplicate dead paths and tighten imports.
   - Outcome: the codebase no longer invites future divergence.
   - Files to touch:
     - `weft/core/task_monitoring.py`
     - `weft/commands/runtime_prune.py`
     - `weft/commands/retention_prune.py`
     - `weft/core/pruning.py`
   - Required approach:
     - Delete or make private-thin any duplicate candidate builders that are no
       longer authoritative.
     - Delete duplicate exact-delete helpers.
     - Keep command rendering helpers if they are output formatting only.
     - Keep TaskMonitor lifecycle/anomaly helpers if they do not select cleanup
       deletion candidates.
   - Search gates:
     - `rg "_task_log_candidates|_task_log_retention_candidates|_apply_candidates|delete_processor"`
       should show one canonical implementation or thin compatibility wrappers
       only.
     - `rg "queue.delete" weft` should show expected exact-delete ownership
       and unrelated legitimate delete uses, not three prune loops.
   - Done signal: a zero-context engineer can identify the canonical prune
     engine by reading one module and can see CLI/TaskMonitor wrappers call it.

9. Update specs, plan metadata, and lessons.
   - Outcome: documentation reflects the new implementation boundary and the
     duplicate-path failure mode is durable knowledge.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/plans/2026-05-09-prune-path-unification-plan.md`
     - `docs/plans/README.md`
     - `docs/lessons.md`
   - Required doc updates:
     - specs should name `weft/core/pruning.py` as the shared implementation
       mapping for prune candidate selection and exact delete;
     - command specs should continue naming command wrappers for public CLI
       rendering;
     - TaskMonitor spec text should say cleanup uses the same prune engine as
       foreground `weft system prune`, with monitor-safe policy arguments;
     - add or update related-plan backlinks;
     - mark this plan `completed` only after implementation and verification.
   - Lesson to add if confirmed:
     - destructive cleanup logic must have one canonical candidate and apply
       path; monitor or CLI wrappers may vary policy but must not reimplement
       pruning semantics.
   - Done signal: `tests/specs/test_plan_metadata.py` passes and spec mappings
     point to the new shared engine.

## 7. Testing Plan

Use red-green TDD for the bug and shared-engine behavior.

Targeted red tests first:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -k "prune or cleanup or retention" -q
```

Core engine tests after adding `weft/core/pruning.py`:

```bash
./.venv/bin/python -m pytest tests/core/test_pruning.py -q
```

Command compatibility tests:

```bash
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py -q
```

CLI compatibility tests:

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q
```

TaskMonitor integration tests:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
```

Spec/metadata tests:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Static checks:

```bash
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Full local gate when targeted tests are green:

```bash
./.venv/bin/python -m pytest
```

Do not replace these with mock-only unit tests. The important evidence is
broker-visible: exact queue rows are selected or deleted, public command output
is stable, and TaskMonitor mode changes queue counts only through the shared
engine.

## 8. Specific Invariants To Test

- Same candidate invariant: for matching config, foreground prune and
  TaskMonitor cleanup select the same safe task-log candidate IDs.
- Cross-batch invariant: a task-log row remains eligible for TaskMonitor
  cleanup even if the proof that supersedes it appears outside the monitor's
  lifecycle observation batch.
- Exact-delete invariant: deletion always targets `(queue, message_id)` and
  never deletes by TID, candidate class, queue prefix, or payload substring.
- Persistence invariant: `.outbox` deletion opens persistent queues; ordinary
  task-log/runtime-state queues use non-persistent handles.
- Archive invariant: ordinary CLI retention apply writes archive records before
  deletion; TaskMonitor never writes archives.
- Force invariant: CLI `--force --apply` can select force-only candidates
  inside documented scope; TaskMonitor never force-deletes.
- Report-only invariant: `processor=report_only` and CLI dry-run select
  candidates but leave queue contents unchanged.
- Scope invariant: runtime-state pruning never touches `weft.log.tasks` or
  task-local queues; retention pruning never touches unsupported
  `weft.state.*` queues.
- Operational-output invariant: prune summaries and TaskMonitor statuses never
  become lifecycle truth for `status`, `result`, or task reconstruction.

## 9. Rollout And Ops Verification

Rollout is backward-compatible at the queue and CLI-contract level. Existing
queue rows, task-log rows, runtime-state rows, and task-local rows do not need
migration. Old and new versions may see the same queues, but only the new
version should run the manager-supervised TaskMonitor cleanup path after
deployment.

Post-deploy checks on ops:

```bash
ssh ops
cd governance
source .env
/opt/venv/bin/weft status
/opt/venv/bin/weft queue list | grep -E '^(weft\.log\.tasks|weft\.state\.tid_mappings|weft\.spawn\.(internal|requests)):'
/opt/venv/bin/weft system prune --family task-log --limit 20 --json
```

Expected healthy signals:

- `opsworker-serve` stays up and points to one live canonical manager.
- `weft.spawn.internal` is present and drains; public `weft.spawn.requests`
  does not accumulate internal service work.
- `weft.state.tid_mappings` continues to shrink or stays bounded.
- `weft.log.tasks` begins to shrink or at least stops growing solely from
  retained superseded rows once TaskMonitor completes delete cycles.
- Foreground dry-run task-log candidates decrease after monitor delete cycles.
- TaskMonitor status/control fields show completed cleanup cycles with
  candidate/deleted counts, not only long-lived `scanning`.

Unhealthy signals:

- foreground task-log dry-run still finds safe candidates while TaskMonitor
  reports zero cleanup candidates under matching defaults;
- `weft.log.tasks` continues rising while TaskMonitor reports successful delete
  cycles;
- monitor deletion errors cluster around missing persistence mode for
  `.outbox`;
- command output changes shape without an intentional spec update.

Rollback:

- If cleanup deletes unsafe evidence, immediately set
  `WEFT_TASK_MONITOR_PROCESSOR=report_only` or disable TaskMonitor on ops and
  redeploy the previous version. Foreground prune remains explicit/operator
  controlled.
- If monitor cleanup is correct but too slow, keep correctness and disable
  autonomous delete while planning an indexed retention optimization. Do not
  restore lossy checkpoint-window candidate selection.
- Because this plan does not change queue names or payload schema, rollback
  does not require data migration.

## 10. Independent Review Loop

This plan is boundary-crossing and destructive, so independent review is
required before implementation unless the user explicitly waives it.

Recommended review prompt:

```text
Read docs/plans/2026-05-09-prune-path-unification-plan.md and the related
code in weft/commands/runtime_prune.py, weft/commands/retention_prune.py,
weft/core/task_monitoring.py, and weft/core/tasks/task_monitor.py. Do not
implement anything. Look for errors, bad ideas, latent ambiguities, missing
tests, and places where this could still leave two prune paths. Could you
implement this confidently and correctly if asked?
```

Review findings must be resolved before coding. If the reviewer says the plan
is not implementable without guessing, update this plan first.

## 11. Fresh-Eyes Self-Review

I re-read the plan for drift against the request and found three risks that the
implementation must handle explicitly:

- Risk: moving everything into `weft/core/pruning.py` could become a vague
  "god module" complaint. Decision: accept a cohesive large module if it owns
  one domain boundary: prune candidate selection and exact deletion. Split only
  if mechanical size makes navigation worse, and keep one public engine.
- Risk: preserving TaskMonitor custom processors could accidentally preserve a
  second destructive path. Decision: built-in destructive cleanup must use the
  core engine. Custom processors are observational unless a future spec expands
  them.
- Risk: using full-history task-log scans in TaskMonitor may be slower than the
  current checkpoint scan. Decision: correctness wins. If ops needs speed, plan
  an indexed optimization after this unification. Do not keep a lossy window as
  the autonomous delete path.

After that review, the plan still points in the intended direction: one
canonical prune engine, explicit policy variation, no duplicate CLI/monitor
cleanup semantics.
