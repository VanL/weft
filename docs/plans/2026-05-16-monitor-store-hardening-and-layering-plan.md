# Monitor Store Hardening And Layering Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; docs/specifications/08-Testing_Strategy.md
Superseded by: none

## 1. Goal

Harden and clean up the TaskMonitor durable collation store so it is clearly
layered, follows SimpleBroker's SQL and transaction patterns, handles
foreseeable crash and retry cases, remains lightweight under SQLite
multi-process contention, and has comprehensive tests across unit, real
SQLite, and Postgres-backed paths. As part of the cleanup, move the
`TaskMonitor` implementation into `weft/core/monitor/` so the monitor
subsystem has one obvious home. The goal is technical excellence for the
Monitor path, not a broader cleanup redesign.

This plan follows the completed
[`2026-05-16-monitor-durable-collation-store-plan.md`](./2026-05-16-monitor-durable-collation-store-plan.md).
That prior plan established the Monitor-owned table concept. This plan fixes
the issues found in fresh-eyes review: missing explicit transaction boundaries,
unclear deletion reconciliation, ambiguous summary-emission semantics, thin
function-level spec traceability, and insufficient contention-focused testing.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitor` is an internal task and owns monitoring/observation. The
  Monitor table must stay operational and must not become task lifecycle or
  result authority.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  Weft must use `WeftContext` and the resolved SimpleBroker broker target. The
  Monitor may manage its own non-queue operational tables, but it must not
  provision the broker database or parse backend targets outside the context
  path.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]:
  `weft.log.tasks` is runtime lifecycle evidence; Monitor table collation is a
  derived read model; raw task-log cleanup must delete exact message IDs only;
  PONG must stay cached/lightweight.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  operational evidence and exact-message cleanup invariants. Destructive cleanup
  must have explicit proof and must not alter task truth.
- `docs/specifications/08-Testing_Strategy.md`: shared vs `sqlite_only`
  classification and broker-heavy testing expectations.
- `docs/agent-context/runbooks/writing-plans.md`: zero-context implementation
  plan requirements.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  touches persistence, cleanup, crash recovery, and queue history.
- `docs/agent-context/runbooks/testing-patterns.md`: required because the
  implementation must not hide broker/process bugs behind mocks.
- `../simplebroker/simplebroker/_runner.py`: SQL runner transaction contract.
- `../simplebroker/simplebroker/_backends/sqlite/schema.py`: local schema
  mutation examples that call `begin_immediate()` before multi-statement writes.
- `../simplebroker/simplebroker/_backends/sqlite/maintenance.py`: destructive
  maintenance examples that loop in bounded transactions.
- `../simplebroker/simplebroker/_sql/sqlite.py` and
  `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/_sql.py`: SQL
  namespace pattern and qmark-placeholder convention.
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`:
  Postgres runner behavior; it keeps a thread connection only inside an explicit
  transaction and otherwise returns it after each `run()`.

The source specs already describe Monitor-owned operational tables. This plan
does not change that product direction. It tightens the implementation and may
require narrow wording updates so the specs state the transaction, summary
disposition, and deletion-reconciliation contracts precisely.

## 3. Current Context And Key Files

Read these before editing:

- `AGENTS.md`: especially the project philosophy, SimpleBroker layer boundary,
  constants rule, and test expectations.
- `docs/agent-context/engineering-principles.md`: especially "Queues Are the
  Canonical State", "Real Broker and Process Tests Beat Mock-Heavy Tests", and
  "Keep Traceability Bidirectional".
- `docs/agent-context/runbooks/testing-patterns.md`: broker-heavy tests run
  under xdist pressure; a flaky broker-heavy test is usually exposing a real
  isolation or contention problem.
- `weft/context.py`: `WeftContext.broker()` and `service_context_key()`.
- `weft/core/monitor/sql.py`: Monitor SQL builders and identifier validation.
- `weft/core/monitor/store.py`: Monitor table access, schema creation,
  collation upserts, checkpointing, summary markers, and raw deletion markers.
- `weft/core/monitor/collation.py`: pure task-log row to Monitor update
  reducer. It must stay broker-free.
- `weft/core/monitor/task_log_scanner.py`: generator-backed task-log scanning.
  Queue history reads must stay generator-based.
- `weft/core/tasks/task_monitor.py`: current location of service scheduling,
  cached PONG, Monitor store cycle orchestration, summary emission, and
  table-backed deletion. This plan moves that implementation to
  `weft/core/monitor/task_monitor.py` and removes the old module.
- `weft/core/tasks/__init__.py`: currently re-exports `TaskMonitor` for
  callers that import task primitives from `weft.core.tasks`. Remove that
  re-export as part of the package ownership move.
- `weft/core/manager.py`: currently resolves the internal task-monitor runtime
  class from `weft.core.tasks.task_monitor`. Update this import after the move.
- `weft/commands/task_monitor.py`: foreground `weft system task-monitor`
  command currently imports `TaskMonitor` and `make_task_monitor_taskspec`
  from the old tasks path. Update it to the new monitor path.
- `weft/core/pruning/apply.py`: canonical exact-message delete helper. Reuse
  this for broker row deletion.
- `tests/core/test_monitor_store.py`: current store tests.
- `tests/core/test_monitor_sql.py`: SQL builder tests.
- `tests/tasks/test_task_monitor.py`: Monitor task, PONG, store-cycle, and
  deletion tests.
- `tests/core/test_task_monitor_cleanup.py`: cleanup policy tests.
- `tests/helpers/weft_harness.py`: integration harness patterns and cleanup
  rules.
- `../simplebroker/simplebroker/_runner.py`: `SQLRunner.begin_immediate`,
  `commit`, and `rollback` contract.
- `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`:
  transaction state and connection-return behavior.

Comprehension checks before editing:

- Which methods in `MonitorStore` currently call `commit()` or `rollback()`
  without first calling `begin_immediate()`?
- Why does the Postgres runner make `rollback()` ineffective for prior
  autocommitted statements unless `begin_immediate()` was called?
- Which layer should decide when a Monitor cycle runs, and which layer should
  own table writes?
- Why is a crash between broker deletion and Monitor-table marking not fixed
  by table transactions alone?
- Why must PONG report cached state rather than querying the store?
- Which tests must use real broker queues rather than mocking `Queue`?

## 4. Files To Modify

Primary code files:

- `weft/core/monitor/store.py`
  - Add explicit write transaction handling.
  - Add or tighten batch ingest APIs.
  - Add idempotent raw-deletion reconciliation semantics.
  - Add function-level `Spec:` docstrings for spec-owned boundaries.
- `weft/core/monitor/sql.py`
  - Add SQL builders for any reconciliation queries.
  - Keep runtime values parameterized and dynamic SQL limited to validated
    code-owned identifiers.
- `weft/core/monitor/task_monitor.py`
  - New owner for `TaskMonitor`, `make_task_monitor_taskspec`,
    `noop_task_monitor_target`, and related private helpers currently in
    `weft/core/tasks/task_monitor.py`.
  - Simplify orchestration so it gathers scan updates and delegates durable
    writes to store-level APIs.
  - Clarify summary disposition semantics.
  - Treat "already absent" exact broker rows as deletion success for Monitor
    table bookkeeping when the exact row had already been proven deletable.
- `weft/core/tasks/task_monitor.py`
  - Delete this file after moving its implementation to
    `weft/core/monitor/task_monitor.py`.
  - Do not leave a compatibility shim or re-export module.
- `weft/core/tasks/__init__.py`
  - Remove the `TaskMonitor` re-export. The Monitor task belongs to
    `weft.core.monitor`, not the generic task primitive package.
- `weft/core/manager.py`
  - Update the task-monitor runtime class import to the new monitor path.
- `weft/commands/task_monitor.py`
  - Update foreground command imports to the new monitor path.
- `weft/_constants.py`
  - Add any new production constants or config defaults here only.
  - Do not define production defaults in Monitor modules or tests.
- `docs/specifications/04-SimpleBroker_Integration.md`
  - Tighten [SB-0.4a] implementation notes for transaction and context
    layering, if the code changes the current wording.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Clarify summary disposition, raw-row deletion marking, crash replay, and
    PONG cached diagnostics.
- `docs/specifications/07-System_Invariants.md`
  - Add or tighten invariants around transaction-backed Monitor table updates
    and idempotent exact cleanup.
- `docs/specifications/08-Testing_Strategy.md`
  - Update only if new test classification guidance is needed.

Test files:

- `tests/core/test_monitor_store.py`
  - Store transaction, replay, reconciliation, and real-broker behavior.
- `tests/core/test_monitor_sql.py`
  - New SQL builders and identifier/placeholder behavior.
- `tests/tasks/test_task_monitor.py`
  - Task-level orchestration, PONG, summary disposition, table deletion, and
    SQLite contention coverage.
- `tests/core/test_task_monitoring.py`
  - Update imports to the new monitor path where these tests are testing
    monitor runtime behavior.
- `tests/core/test_manager.py`, `tests/commands/test_status.py`
  - Update expected implementation mappings or dotted paths only where the
    runtime contract changes. Do not preserve old monitor function targets.
- `tests/core/test_task_monitor_cleanup.py`
  - Only update if shared exact-delete behavior or result shapes change.
- `tests/system/test_constants.py`
  - Update if new constants or env vars are added.
- `tests/conftest.py`
  - Update shared-module classification if new test files need classification.

Files not to modify unless a failing test proves it is necessary:

- `weft/core/manager.py`: manager supervision is not part of this cleanup.
- `weft/commands/status.py` and `weft/commands/result.py`: public task status
  and result reconstruction must not depend on Monitor tables.
- `../simplebroker/`: read it for patterns, but do not change SimpleBroker for
  this Weft cleanup unless a real SimpleBroker bug is found and discussed
  separately.

## 5. Invariants And Constraints

- `weft.log.tasks` remains runtime lifecycle evidence. The Monitor table is a
  derived operational read model.
- Monitor table rows must never become public task truth, result truth, or a
  required input for `weft task status` or `weft result`.
- The Monitor may create, verify, and additively migrate only its own tables in
  an already initialized Weft broker database.
- Store code must use the resolved `WeftContext` and broker target. It must not
  parse Postgres DSNs, derive SQLite file paths, or open an unrelated database.
- Runtime values in SQL must be parameters. Dynamic SQL may include only
  code-owned table, index, column, and placeholder fragments after validation.
- Multi-statement store writes must call `runner.begin_immediate()` and pair
  it with `commit()` or `rollback()`. Do not rely on autocommit plus a
  misleading rollback.
- Keep SQLite writer locks short. Do not hold one giant transaction across a
  full 5000-row scan window if a bounded chunked write would preserve replay
  semantics.
- Checkpoints must advance only after all corresponding Monitor store writes
  are durable.
- Replay must be safe. Reprocessing a row after a crash must not duplicate
  child message rows, regress terminal status, or corrupt summary state.
- Raw task-log deletion must use exact message IDs through
  `apply_exact_prune_candidates`.
- For Monitor table-backed deletion, an exact broker row that is already absent
  after selection should be treated as idempotently deleted for Monitor table
  bookkeeping. Otherwise a crash after broker deletion but before table marking
  can leave the Monitor store stuck forever.
- PONG must remain lightweight. It may include cached store status and last
  cycle diagnostics, but it must not scan queues or query Monitor tables while
  answering PING.
- `log_sink="none"` must have explicit semantics. It should be treated as a
  successful no-op summary disposition if table-backed deletion is enabled,
  and that meaning must be documented and tested.
- No new ORM, migration framework, generic Weft database layer, or broad plugin
  system.
- No public CLI shape change is part of this cleanup.
- Moving `TaskMonitor` is a hard ownership move. There is no compatibility
  shim for `weft.core.tasks.task_monitor`. All first-party imports, generated
  internal service specs, tests, and spec mappings must use
  `weft.core.monitor.task_monitor`.
- There are no outside TaskMonitor specs to preserve. Do not design a
  migration layer for old external TaskMonitor function targets.

Stop and re-plan if:

- the implementation starts adding status/result reads from Monitor tables;
- a second exact-delete path appears outside `weft/core/pruning/apply.py`;
- the old `weft/core/tasks/task_monitor.py` file remains after the move;
- any first-party code still imports `weft.core.tasks.task_monitor` after the
  move;
- transaction logic is duplicated by copy/paste in every method instead of one
  small local store helper;
- the code wants to mock `simplebroker.Queue` for a behavior that can be
  tested with `broker_env` or `WeftTestHarness`;
- a proposed SQLite contention test relies on brittle sleeps instead of
  bounded process joins, PONG diagnostics, and queue-visible evidence;
- the change requires a SimpleBroker API addition to be correct. If that
  happens, stop and discuss the SimpleBroker API first.

## 6. Target Design

### 6.0 Package Ownership Move

Move the TaskMonitor implementation from `weft/core/tasks/task_monitor.py` to
`weft/core/monitor/task_monitor.py`.

Target ownership:

- `weft/core/monitor/task_monitor.py` owns the task-shaped Monitor runtime:
  `TaskMonitor`, `TaskMonitorCallback`, `make_task_monitor_taskspec`,
  `noop_task_monitor_target`, PONG construction, heartbeat registration,
  store-cycle orchestration, summary disposition, and table-backed deletion.
- `weft/core/tasks/task_monitor.py` is deleted. Do not leave a compatibility
  module.
- `weft/core/tasks/__init__.py` stops exposing `TaskMonitor`. The Monitor
  runtime is not a generic task primitive.
- `weft/core/monitor/__init__.py` should no longer say the task primitive lives
  in `weft.core.tasks.task_monitor`. Update it to describe the package as the
  Monitor subsystem.

Import migration:

- Update first-party code to import from `weft.core.monitor.task_monitor`.
- Update generated internal service function targets to use
  `weft.core.monitor.task_monitor`.
- Add a stale-reference gate that fails if first-party code still imports
  `weft.core.tasks.task_monitor`.
- Search for exact strings:
  - `weft.core.tasks.task_monitor`
  - `core/tasks/task_monitor.py`
  - `from weft.core.tasks.task_monitor`
  - `import weft.core.tasks.task_monitor`
  - `TaskMonitor`
- Update docs/spec implementation mappings to the new path. Do not document an
  old-path public surface.

Do not move unrelated task primitives out of `weft/core/tasks/`. This is a
Monitor-specific package ownership cleanup.

### 6.1 Store Transaction Helper

Add one private transaction helper in `weft/core/monitor/store.py`, near the
store access code:

```python
@contextmanager
def _write_transaction(runner: _SQLRunner) -> Iterator[None]:
    ...
```

Required behavior:

- call `runner.begin_immediate()` before yielding;
- if `begin_immediate()` fails, propagate the error and do not call
  `rollback()`;
- after a successful begin, call `commit()` on success;
- after a successful begin, call `rollback()` on any exception and re-raise;
- do not swallow `rollback()` errors unless there is an existing repo pattern
  that explicitly does so. There is not one here.

This mirrors SimpleBroker's schema and maintenance style while avoiding
copy/paste transaction blocks in every public store method. Keep it private to
the Monitor store. Do not add a generic transaction utility in `weft/helpers.py`.

Use this helper for all store mutation methods:

- `ensure_schema()`
- `set_checkpoint()`
- `upsert_task_event()` if it remains public
- the new batch ingest method
- `mark_summary_emitted()`
- `mark_messages_deleted()`
- any new reconciliation method that writes

Read-only methods such as `get_checkpoint()`, `get_task()`,
`list_unemitted_terminal_tasks()`, and `list_deletable_task_log_messages()` must
not open write transactions.

### 6.2 Batch Ingest API And Cleaner Orchestration

Move per-row durable write orchestration out of `TaskMonitor`.

Add a store API with this shape, using exact names only if they fit the final
code:

```python
def record_task_log_updates(
    self,
    queue_name: str,
    updates: Sequence[MonitorTaskEventUpdate],
    *,
    checkpoint_message_id: int | None,
) -> MonitorStoreIngestResult: ...
```

The method should:

- open one resolved broker connection through `WeftContext.broker()`;
- write updates in bounded chunks, not one connection per row and not one
  huge transaction for the whole scan;
- merge each update with any existing task row;
- upsert the task collation row and child raw-message row in the same chunk
  transaction;
- advance the checkpoint only after all chunks have committed successfully;
- return counts needed by `TaskMonitor` for cached PONG fields:
  rows considered, updates written, distinct updated TIDs, terminal TIDs, and
  checkpoint written.

Chunking rule:

- Add a production constant in `weft/_constants.py`, for example
  `TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT`.
- Consider an env/config key only if it is already consistent with the nearby
  `WEFT_TASK_MONITOR_*` config pattern. If added, document it in
  `docs/specifications/00-Quick_Reference.md` and update
  `tests/system/test_constants.py`.
- Default should be small enough to avoid long SQLite writer locks under
  multi-process load and large enough to avoid excessive connection churn.
  Start with 100 unless measurement or existing constants argue otherwise.

`TaskMonitor._run_monitor_store_cycle()` should:

- scan with `GeneratorTaskLogScanner`;
- reduce rows with `update_from_task_log_row`;
- filter out the monitor's own TID;
- pass the updates and high-water checkpoint to `record_task_log_updates()`;
- keep PONG/cached diagnostics in `TaskMonitor`;
- not call store `upsert_task_event()` in a loop.

Keep `upsert_task_event()` only if tests or callers still need it. If retained,
make it a thin wrapper over the batch method with no checkpoint.

### 6.3 Raw Deletion Marking And Reconciliation

Fix deletion bookkeeping so it converges after all ordinary timing failures:

1. Normal success:
   - Store selects exact raw message IDs that are terminal, summary-disposed,
     and not raw-deleted.
   - TaskMonitor calls `apply_exact_prune_candidates()`.
   - For every candidate with `error is None`, mark the Monitor child message
     deleted, whether `queue.delete(message_id=...)` returned `True` or `False`.
   - `True` means deleted now; `False` with no error means already absent.
   - Do not mark rows when `error is not None`.

2. Parent raw-deleted convergence:
   - In the same store transaction that marks child messages deleted, mark the
     parent task row `raw_deleted_at_ns` when all known child rows are marked
     deleted.
   - Add a reconciliation query for rows where `raw_deleted_at_ns IS NULL`,
     at least one known child row exists, and no known child rows have
     `deleted_at_ns IS NULL`.
   - Call that reconciliation from `mark_messages_deleted()` after the child
     updates, and expose a small public method only if tests need to exercise
     it directly.

3. Crash window after broker delete but before table mark:
   - The next cycle will select the same exact message IDs.
   - The exact delete helper may report `deleted=False` with no error because
     the row is already gone.
   - Treat this as successful deletion for Monitor bookkeeping.
   - This is safe because selection already proved the row was eligible, and
     the row's absence is idempotent from the broker point of view.

Do not make the Monitor store directly query SimpleBroker's `messages` table to
verify missing broker rows. That would couple the store to SimpleBroker's queue
schema and bypass the exact-delete helper. If exact delete cannot distinguish
missing from failed, use `error is None` as the idempotence signal.

### 6.4 Summary Disposition Contract

Clarify the currently ambiguous `summary_emitted_at_ns` behavior.

Use this contract:

- `summary_emitted_at_ns` means "the terminal summary disposition completed."
- For `log_sink="stdout"` or `log_sink="disk"`, disposition completes only
  after the JSON summary write succeeds.
- For `log_sink="none"`, disposition completes as an intentional no-op because
  the operator chose no external summary sink.
- If stdout/disk emission raises, do not mark `summary_emitted_at_ns`.
- Table-backed raw deletion may require this disposition barrier, so
  `log_sink="none"` must still be able to pass the barrier by explicit no-op.

Update names where it improves clarity, but avoid a durable schema rename unless
it is necessary. Prefer code/docstring language such as "summary disposition"
around the existing `summary_emitted_at_ns` field. If a field rename is truly
needed, it becomes a schema migration and must be planned as a separate
versioned migration task.

### 6.5 Function-Level Spec Traceability

Add `Spec:` lines to public or boundary-owning functions whose behavior is
defined by the specs. At minimum:

- `MonitorStore.ensure_schema()`
- `MonitorStore.record_task_log_updates()` or equivalent
- `MonitorStore.set_checkpoint()`
- `MonitorStore.mark_summary_emitted()`
- `MonitorStore.list_deletable_task_log_messages()`
- `MonitorStore.mark_messages_deleted()`
- `TaskMonitor._run_monitor_store_cycle()`
- `TaskMonitor._emit_monitor_store_summaries()`
- `TaskMonitor._delete_monitor_store_task_log_rows()`
- new SQL-builder functions that encode exact deletion/reconciliation

Do not add noisy comments to every helper. The goal is traceability for
contract boundaries, not decoration.

## 7. Bite-Sized Tasks

1. Add failing transaction-boundary tests.
   - Outcome: prove the current store is missing SimpleBroker-style explicit
     transaction boundaries.
   - Files to touch:
     - `tests/core/test_monitor_store.py`
   - Read first:
     - `../simplebroker/tests/test_transaction_error_propagation.py`
     - `../simplebroker/simplebroker/_runner.py`
     - `../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`
   - Test approach:
     - Use a tiny fake `_SQLRunner` only for transaction ordering. This is an
       acceptable mock because transaction ordering is the private contract
       under review and cannot be observed through queue contents.
     - Keep real broker tests for actual store behavior in later tasks.
     - Add tests that prove write paths call `begin_immediate()` before writes,
       commit on success, rollback after a write failure, and propagate begin
       failures without rollback.
   - Do not:
     - Mock `simplebroker.Queue` or task monitor queue behavior.
     - Assert on every SQL string in these transaction tests.
   - Done when:
     - the new tests fail against the current code for lack of begin calls.

2. Add the private store transaction helper and apply it to existing mutation
   methods.
   - Outcome: all current store mutations follow SimpleBroker's explicit
     transaction contract.
   - Files to touch:
     - `weft/core/monitor/store.py`
     - `tests/core/test_monitor_store.py`
   - Reuse:
     - SimpleBroker's `runner.begin_immediate(); try: ... commit(); except:
       rollback(); raise` pattern.
   - Constraints:
     - keep the helper private to `store.py`;
     - do not introduce a generic transaction framework;
     - do not open write transactions for read-only methods.
   - Tests:
     - make the transaction-boundary tests green;
     - keep existing real-broker store tests green.
   - Done when:
     - `uv run pytest tests/core/test_monitor_store.py -q` passes.

3. Add real-broker tests for replay and transaction correctness.
   - Outcome: prove store behavior with real SQLite broker state, not only a
     fake runner.
   - Files to touch:
     - `tests/core/test_monitor_store.py`
   - Test approach:
     - Use `broker_env` or `build_context()` with a real temporary context.
     - Test that repeated `ensure_schema()` stays idempotent.
     - Test that replaying start, terminal, then start still preserves terminal
       state.
     - Add a failure/replay test around batch ingest or upsert: simulate a
       failure before checkpoint advancement, then replay and assert the final
       store state is correct. If direct fault injection requires a fake runner,
       pair it with a real-broker replay test.
   - Do not:
     - assert on private connection objects;
     - sleep to "wait for commit".
   - Done when:
     - real SQLite store tests pass without mocks for queue behavior.

4. Move `TaskMonitor` into the monitor package.
   - Outcome: the Monitor runtime and Monitor support modules live together
     under `weft/core/monitor/`, with no old module retained.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/tasks/task_monitor.py`
     - `weft/core/tasks/__init__.py`
     - `weft/core/monitor/__init__.py`
     - `weft/core/manager.py`
     - `weft/commands/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
     - `tests/core/test_task_monitoring.py`
     - `tests/core/test_manager.py`
     - `tests/commands/test_status.py`
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required implementation:
     - move the implementation body to `weft/core/monitor/task_monitor.py`;
     - delete `weft/core/tasks/task_monitor.py`;
     - update first-party imports to the new path;
     - remove `TaskMonitor` from `weft/core/tasks/__init__.py`;
     - update generated internal service function targets to the new path;
     - update spec implementation mappings to the new path.
   - Old-path requirement:
     - remove the old module path entirely. There are no outside TaskMonitor
       specs to preserve.
   - Tests:
     - update imports in existing tests to the new path;
     - add a stale-reference test or spec check that first-party code no longer
       imports `weft.core.tasks.task_monitor`;
     - run the manager and foreground monitor tests that exercise class
       resolution and command imports.
   - Stop if:
     - the move creates circular imports between `weft.core.monitor` and
       `weft.core.tasks`;
     - the old module is kept in any form;
     - the implementation wants to change the TaskSpec function target or
       public CLI shape.
   - Done when:
     - `uv run pytest tests/tasks/test_task_monitor.py tests/core/test_task_monitoring.py tests/core/test_manager.py::test_manager_resolves_task_monitor_runtime_class -q`
       passes, adjusting the exact manager test selector to the real test name
       if needed.

5. Introduce the batch ingest API and move per-row store writes out of
   `TaskMonitor`.
   - Outcome: `TaskMonitor` orchestrates scan and cached diagnostics from
     its new home in `weft/core/monitor/task_monitor.py`, while `MonitorStore`
     owns durable write sequencing and checkpoint advancement.
   - Files to touch:
     - `weft/_constants.py`
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/core/test_monitor_store.py`
     - `tests/tasks/test_task_monitor.py`
     - `docs/specifications/00-Quick_Reference.md` if a config key is added
     - `tests/system/test_constants.py` if a constant/config key is added
   - Reuse:
     - `GeneratorTaskLogScanner`
     - `update_from_task_log_row`
     - `service_context_key`
   - Constraints:
     - checkpoint advances only after corresponding store writes succeed;
     - chunks commit in bounded write transactions;
     - no status/result reader uses the Monitor store;
     - PONG remains cached.
   - Tests:
     - add a store-level batch ingest test that verifies multiple TIDs,
       terminal preservation, child message refs, and checkpoint update;
     - update TaskMonitor tests to assert the same PONG counters still reflect
       rows processed, tasks updated, terminal tasks, and checkpoint state;
     - if a write-batch config is added, test config parsing/defaults through
       the existing constants/config tests.
   - Stop if:
     - the implementation wants to put checkpoint logic back in
       `TaskMonitor`;
     - chunking logic starts to look like a generic batch framework.
   - Done when:
     - `uv run pytest tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -q`
       passes.

6. Fix raw deletion bookkeeping for idempotent missing rows and parent
   reconciliation.
   - Outcome: Monitor table deletion state converges even if a process dies
     after broker deletion but before table marking.
   - Files to touch:
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/core/test_monitor_sql.py`
     - `tests/core/test_monitor_store.py`
     - `tests/tasks/test_task_monitor.py`
   - Reuse:
     - `apply_exact_prune_candidates` in `weft/core/pruning/apply.py`.
   - Required behavior:
     - mark selected Monitor child rows deleted when exact delete returns no
       error, even if `deleted` is false because the broker row is already
       absent;
     - do not mark rows when exact delete reports an error;
     - mark parent raw-deleted when all known child rows are deleted;
     - add reconciliation for parent rows already in that all-children-deleted
       state.
   - Tests:
     - SQL builder test for the reconciliation query shape and identifier
       validation.
     - Store test: after marking all child messages deleted, parent
       `raw_deleted_at_ns` is set.
     - Store test: if child rows are already marked deleted but parent raw
       marker is null, reconciliation fixes the parent.
     - TaskMonitor test: a selected exact row that is already absent from
       `weft.log.tasks` is still marked deleted in the Monitor table and does
       not loop forever.
   - Do not:
     - query SimpleBroker's `messages` table directly from Monitor store;
     - create a second exact-delete helper;
     - treat delete errors as success.
   - Done when:
     - targeted store, SQL, and task monitor deletion tests pass.

7. Clarify and test summary disposition semantics.
   - Outcome: `log_sink="none"` no longer looks like an accidental bypass; it
     is an explicit successful no-op summary disposition.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
     - `docs/specifications/05-Message_Flow_and_State.md`
   - Required behavior:
     - stdout/disk summary writes must complete before marking summary
       disposition done;
     - `none` records disposition as intentionally suppressed;
     - failed disk/stdout writes do not mark the summary emitted/disposed and
       do not unlock table-backed deletion.
   - Tests:
     - existing `log_sink="none"` table deletion test should remain, but rename
       or extend it to assert intentional no-op disposition.
     - add a disk/stdout failure test by patching only the file/write boundary
       or print boundary, not broker queues.
     - assert table-backed deletion does not run for a terminal row whose
       summary disposition failed.
   - Stop if:
     - the fix requires a schema field rename. That should become a separate
       migration plan unless absolutely necessary.
   - Done when:
     - `uv run pytest tests/tasks/test_task_monitor.py -q` passes.

8. Add SQLite contention-focused coverage.
   - Outcome: prove the new store write path does not create obvious
     multi-process SQLite contention regressions.
   - Files to touch:
     - `tests/tasks/test_task_monitor.py` or `tests/core/test_monitor_store.py`
     - `tests/conftest.py` only if classification requires it
   - Test design:
     - Use real SQLite broker queues and, where practical, a real spawned
       process. Use `multiprocessing.get_context("spawn")`.
     - Have one process or worker write a bounded number of messages while the
       monitor ingests a bounded task-log window with a deliberately small store
       write batch size.
     - Assert writer completion, no monitor store error in cached diagnostics,
       correct checkpoint movement, and visible store rows.
     - Keep timeouts generous enough for CI but bounded enough to catch lock
       stalls.
   - What not to mock:
     - do not mock `Queue.write`, `Queue.delete`, `Queue.peek_generator`, or the
       broker runner for this contention test.
   - Avoid:
     - fixed sleeps as correctness proof;
     - broad xdist serialization;
     - tests that hold a SQLite lock forever and then assert a timeout.
   - Done when:
     - the test is marked/classified appropriately, passes alone, and passes
       with the nearby TaskMonitor tests.

9. Add Postgres parity coverage.
   - Outcome: prove the store follows Postgres runner transaction behavior and
     does not rely on SQLite-only semantics.
   - Files to touch:
     - `tests/core/test_monitor_store.py`
     - `tests/tasks/test_task_monitor.py` if needed
   - Test design:
     - Keep relevant store tests marked `shared` so they run through
       `uv run bin/pytest-pg`.
     - Include at least one test that would fail under autocommit partial write
       assumptions if the code forgot `begin_immediate()`.
     - Use real broker-backed queues for task monitor integration.
   - Commands:
     - `uv run bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py`
   - Done when:
     - targeted Postgres tests pass.

10. Tighten function-level documentation and spec mappings.
   - Outcome: a zero-context engineer can navigate from spec to code and back
     for every Monitor store boundary.
   - Files to touch:
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/task_monitor.py`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required updates:
     - add concise `Spec:` docstrings to boundary methods named in section 6.5;
     - update implementation mappings if names or ownership move;
     - describe summary disposition and idempotent missing-row deletion.
   - Do not:
     - add broad comments to obvious helper functions;
     - cite whole spec files where exact section codes exist.
   - Done when:
     - docs and code have bidirectional traceability and
       `uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q`
       still passes.

11. Run final gates and inspect for regressions.
    - Outcome: prove the cleanup is safe across local static checks, default
      tests, and Postgres tests.
    - Commands:
      - `uv run pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py -q`
      - `uv run pytest tests/core/test_task_monitoring.py tests/core/test_manager.py tests/commands/test_status.py -q`
      - `uv run bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py`
      - `uv run pytest`
      - `uv run bin/pytest-pg`
      - `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
      - `uv run ruff check weft`
      - `git diff --check`
    - Done when:
      - all commands pass, or any failure is root-caused and fixed rather than
        skipped.

## 8. Testing Plan

Use a layered test strategy. Each layer catches a different class of mistakes.

1. SQL-builder tests (`tests/core/test_monitor_sql.py`).
   - Use plain unit tests.
   - Assert identifier validation rejects unsafe names.
   - Assert new reconciliation SQL uses qmark placeholders and validated
     identifiers.
   - Do not test every whitespace detail.

2. Transaction-order tests (`tests/core/test_monitor_store.py`).
   - Use a fake `_SQLRunner` only to prove begin/commit/rollback ordering and
     begin-failure behavior.
   - This is the only planned fake-runner seam. It is appropriate because
     transaction calls are not visible through broker queue contents.
   - Pair these with real-broker tests so the fake does not become the only
     proof.

3. Real SQLite store tests (`tests/core/test_monitor_store.py`).
   - Use `build_context()` or `broker_env` with a real temporary broker.
   - Assert schema idempotency, replay safety, checkpoint behavior,
     batch ingest, summary markers, deletion markers, and reconciliation.
   - These tests must not mock SimpleBroker queues.

4. TaskMonitor integration tests (`tests/tasks/test_task_monitor.py`).
   - Use real queues from `broker_env`.
   - Assert PONG uses cached diagnostics and does not query or scan while
     answering PING.
   - Assert table-backed deletion goes through exact-message delete and that
     already-absent rows are treated idempotently when there is no delete error.
   - Assert failed summary disposition blocks table-backed deletion.
   - Assert the new `weft.core.monitor.task_monitor` import path is used by
     manager, command, and task monitor tests.
   - Add a stale-reference check that fails if first-party code imports
     `weft.core.tasks.task_monitor`.

5. SQLite contention test.
   - Use a real SQLite broker and a spawned writer or tightly controlled
     concurrent worker.
   - Keep the workload small and deterministic.
   - Assert bounded progress and absence of `database is locked` or monitor
     store errors.
   - Do not use sleep as the correctness proof. Sleeps inside polling loops are
     acceptable only with explicit deadlines.

6. Postgres parity tests.
   - Keep store tests backend-neutral where possible so they run under
     `uv run bin/pytest-pg`.
   - The Postgres path matters because without explicit `begin_immediate()`,
     each `run()` can autocommit and return the connection before rollback.

Regression tests to write red first when practical:

- transaction helper not calling `begin_immediate()`;
- begin failure calls rollback incorrectly;
- child messages all deleted but parent `raw_deleted_at_ns` remains null;
- already-absent exact broker row loops forever in Monitor deletion;
- failed summary write still unlocks deletion;
- PONG triggers a store query or cleanup scan.

Avoid these weak tests:

- tests that only assert private counters after mocking all store methods;
- tests that inspect a single happy-path SQL string and skip real broker state;
- tests that use `time.sleep()` as the only proof that SQLite contention is OK;
- tests that mark a failure as `xfail` because it is "timing-sensitive".

## 9. Rollout And Rollback

Rollout:

- Ship the transaction and layering cleanup with existing defaults preserved.
- If a new store-write batch-size config is added, default it conservatively
  and document it.
- Do not enable a more aggressive deletion policy in the same change. This
  plan hardens the existing table-backed deletion gate; it does not broaden
  what is eligible for deletion.
- After deploy, verify with `weft task ping <task-monitor-tid>`:
  - `last_cycle.store.available` or equivalent cached store status is true;
  - checkpoint advances across cycles;
  - collation rows processed and terminal tasks observed are nonzero on active
    systems;
  - table-backed deletion counts progress when enabled;
  - `last_errors` and `collation_store_error` stay empty;
  - PONG latency stays low because it is cached.

Rollback:

- The Monitor tables are derived operational state. Rolling back code may leave
  extra Monitor rows in place, but public task status/result must not depend on
  them.
- Existing table schema should remain backward-compatible. Do not rename
  fields in this cleanup unless a separate migration is added.
- If the cleanup causes unexpected contention, disable table-backed deletion
  and/or collation through existing Monitor config first, then roll back code.
- If a new batch-size config is added, operators can lower it to reduce SQLite
  write-lock duration before rolling back.

One-way doors:

- Broker row deletion is destructive. This plan must not broaden deletion
  eligibility.
- Treating already-absent exact rows as deleted is safe only after the Monitor
  has already selected those exact rows as eligible. Do not apply that rule to
  arbitrary queue rows.

## 10. Independent Review Loop

This work is risky because it touches persistence, cleanup, SQL, and
multi-process contention. It needs review before implementation begins and
again before completion if implementation changes the design.

Recommended reviewer:

- Use `plan-eng-review` or a different agent family reviewer if available.
- If no external reviewer is available, record that limitation in the final
  implementation notes and do an explicit second self-review before coding.

Review prompt:

> Read `docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`,
> `weft/core/monitor/store.py`, `weft/core/monitor/task_monitor.py`,
> `weft/core/tasks/__init__.py`,
> `weft/core/monitor/sql.py`, `docs/specifications/04-SimpleBroker_Integration.md`
> [SB-0.4a], and `docs/specifications/05-Message_Flow_and_State.md` [MF-5].
> Look for errors, bad ideas, missing tests, layering mistakes, and SQLite or
> Postgres contention risks. Do not implement anything. Could you implement this
> confidently and correctly if asked?

Review feedback must be handled explicitly:

- accepted findings update this plan before implementation;
- rejected findings get a short written reason;
- if the reviewer cannot implement confidently, do not start implementation
  until the ambiguity is fixed.

## 11. Out Of Scope

- No new public CLI commands.
- No new ORM or generic database abstraction.
- No broad SimpleBroker API change.
- No status/result dependency on Monitor tables.
- No manager supervision changes.
- No new cleanup policies beyond the existing table-backed exact deletion
  semantics.
- No schema field rename unless a separate migration task is added.
- No attempt to solve every possible crash between every broker operation and
  every Monitor table write. This plan fixes the known exact-delete bookkeeping
  convergence path and keeps all other store writes replay-safe.

## 12. Fresh-Eyes Self-Review

Pass 1 findings:

- The first draft risked implying that a table transaction alone could solve
  the crash between broker deletion and table marking. That is false because
  broker deletion and Monitor table marking are two separate systems. The plan
  now explicitly treats already-absent exact broker rows as idempotent success
  when there is no delete error, and adds parent-row reconciliation.
- The first draft risked over-generalizing transaction handling into a shared
  helper outside the Monitor store. That would be premature. The plan now keeps
  the helper private to `store.py`.
- The first draft did not spell out how to avoid SQLite writer-lock pressure.
  The plan now requires bounded store write chunks and a real SQLite contention
  test.
- The first draft left `log_sink="none"` ambiguous. The plan now defines it as
  intentional no-op summary disposition and requires tests.
- A later review noted that the TaskMonitor implementation itself should move
  into `weft/core/monitor/`. The plan now makes that a hard package ownership
  move: delete `weft/core/tasks/task_monitor.py`, remove the tasks-package
  re-export, and update all first-party references to the monitor package.

Pass 2 findings:

- The plan stays aligned with the discussed direction: clean up the Monitor
  table path, follow SimpleBroker patterns, improve layering, and strengthen
  tests. It does not drift into a new cleanup architecture or a generic DB
  layer.
- Residual risk: the exact default store write batch size may need tuning after
  implementation and ops observation. The plan handles this by using a
  conservative default and cached PONG diagnostics, not by broadening scope.

Self-review status: the plan is implementable after the independent review loop
in section 10. If the reviewer finds a layering or test-design flaw, update
this plan before coding.
