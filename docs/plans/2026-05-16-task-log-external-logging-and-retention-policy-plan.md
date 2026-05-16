# Task Log External Logging And Retention Policy Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], [SB-0.4a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; docs/specifications/08-Testing_Strategy.md
Superseded by: none

## 1. Goal

Implement explicit `weft.log.tasks` external logging and retention semantics
inside the new `weft/core/monitor/` structure. The monitor should support a raw
debug-style external log mode and a standard collated summary mode, with
logging performed before deletion whenever an external log is configured.
Deletion must fail closed for rows or families whose required external log emit
fails, while the `TaskMonitorTask` itself should still start and report cached
degraded status through PONG. The plan keeps the Monitor table as the durable
rolling collation state for standard mode and prevents two independent cleanup
paths from owning the same `weft.log.tasks` rows.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: environment variable reference.
  This plan adds user-visible configuration and must keep the quick reference
  synchronized.
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  `TaskMonitorTask` is the internal monitor task and owns monitor-store
  orchestration, cached diagnostics, and broker effects on the reactor thread.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  Monitor-owned tables are derived operational state inside the already
  initialized Weft broker database. External log files are not queue data and
  must not change SimpleBroker queue semantics.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]:
  `weft.log.tasks` is runtime lifecycle evidence with operational retention
  requirements. PONG must use cached monitor state rather than scanning queues
  or querying the store.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  monitor logs, summaries, and tables are operational output only. Exact
  cleanup must delete only selected exact message IDs, and deletion must not
  turn monitor output into public lifecycle truth.
- `docs/specifications/08-Testing_Strategy.md`: new tests must use real broker
  queues for queue semantics and must be classified correctly for SQLite and
  Postgres-backed runs.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`: established
  the Monitor-owned durable table concept.
- `docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`: moved
  TaskMonitor runtime into `weft/core/monitor/` and hardened store writes,
  checkpointing, summary disposition, and exact delete reconciliation.
- `docs/agent-context/runbooks/writing-plans.md`: this plan follows the
  zero-context implementer standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  changes cleanup, retention, external output, failure semantics, and
  destructive deletion gates.
- `docs/agent-context/runbooks/testing-patterns.md`: required because this
  must not hide queue, file, or process behavior behind mocks.

The specs must be updated during implementation before the work is considered
done. Until then this plan is the active implementation proposal, not the
normative behavior source.

## 3. Current Context And Key Files

Read these files before editing:

- `AGENTS.md`: especially the constants rule, SimpleBroker boundary, test
  expectations, and "do not infer, read" guidance.
- `weft/_constants.py`: all production constants, env var parsers, defaults,
  and `load_config()` / `compile_config()` behavior live here.
- `weft/core/monitor/runtime.py`: `TaskMonitorRuntimeConfig` converts loaded
  config into typed monitor settings.
- `weft/core/monitor/task_monitor.py`: owns the persistent monitor reactor,
  cached PONG fields, monitor-store cycle, summary emission, and exact
  table-backed deletion.
- `weft/core/monitor/store.py`: owns Monitor table schema, checkpoints,
  rolling task-log collation rows, summary markers, and raw-deleted markers.
- `weft/core/monitor/sql.py`: SQL builder and trusted-identifier validation
  layer for Monitor-owned tables.
- `weft/core/monitor/collation.py`: pure reducer from decoded task-log rows to
  Monitor table updates. It must remain broker-free and file-free.
- `weft/core/monitor/task_log_scanner.py`: generator-backed non-consuming
  `weft.log.tasks` scanner and family selection helpers.
- `weft/core/monitor/cleanup.py`: bounded cleanup policy runner for
  Weft-owned cleanup queues. It currently owns malformed, collated,
  truncated-terminal, terminal-proven reserved, and broad older-than task-log
  cleanup policy stats.
- `weft/core/pruning/apply.py`: canonical exact-message delete helper. All
  broker row deletion must continue through this path.
- `weft/core/serve_log.py`: existing operational serve-log JSONL helper. Read
  this for local conventions, but do not reuse it for lifecycle external logs
  unless its abstraction fits without coupling serve logs to task-log
  retention.
- `tests/tasks/test_task_monitor.py`: current monitor PONG, cycle,
  table-backed deletion, and failure-disposition tests.
- `tests/core/test_monitor_store.py`: durable store behavior tests.
- `tests/core/test_task_monitor_cleanup.py`: cleanup policy behavior tests.
- `tests/core/test_task_log_scanner.py`: task-log family selection tests.
- `tests/system/test_constants.py`: config parsing and production constant
  location tests.
- `tests/specs/test_test_audit_policy.py`: shared test classification rules.

Comprehension checks before editing:

- Which method currently marks `summary_emitted_at_ns`, and why does deletion
  require that marker when table-backed deletion is enabled?
- Which code path deletes exact broker message IDs today, and why must the
  implementation keep using it instead of calling broker delete directly?
- Which fields in extended PONG are cached from the last cycle, and why must
  PONG not validate the external log path on demand?
- Which path currently uses `WEFT_TASK_MONITOR_LOG_SINK`, and why is that not
  the same contract as externally retaining `weft.log.tasks` lifecycle data?

## 4. Proposed Semantics

### 4.1 Configuration

Add these canonical env/config keys:

- `WEFT_LOG_TASKS_EXTERNAL_PATH`: optional path to the external task-log JSONL
  file. If unset or empty, external task-log logging is disabled. Do not use a
  broad name such as `WEFT_EXTERNAL_LOG`; the setting is specifically for
  `weft.log.tasks` retention output. Absolute paths are used as-is. Relative
  paths resolve under `WeftContext.logs_dir`, not the caller's current working
  directory.
- `WEFT_LOG_TASKS_EXTERNAL_MODE`: `collated` or `raw`. Defaults to `collated`
  when `WEFT_LOG_TASKS_EXTERNAL_PATH` is set. The value is ignored when no
  external path is configured except for validation.
- `WEFT_LOG_TASKS_EXTERNAL_ENABLED`: optional boolean override. Defaults to
  true when `WEFT_LOG_TASKS_EXTERNAL_PATH` is non-empty and false otherwise.
  If false, no external task-log logging is required even if the path is
  present.
- `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`: canonical minimum age before
  `weft.log.tasks` raw rows or collated families become eligible for logging
  and deletion. Default to `172800` seconds, 48 hours.

Do not keep `WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS` as a compatibility
alias. The public configuration has one retention knob:
`WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`. Existing internal field names may be
temporarily renamed during the slice, but the old env/config key must not be a
supported operator contract.

Keep `WEFT_TASK_MONITOR_LOG_SINK` for Monitor operational logs only. Do not use
it to decide whether `weft.log.tasks` lifecycle rows require external logging.

### 4.2 External Log Failure Semantics

The monitor should start even if the external log sink is configured but
unhealthy. Startup must not fail solely because a task-log external file is not
writable.

If external logging is enabled and a row or family requires logging:

- Validate the sink at monitor startup and on first cycle use. Cache health in
  the monitor instance.
- The sink may create parent directories under the resolved log root, matching
  existing Weft log behavior. It must still fail closed for permission errors,
  path-is-directory errors, and other write/open failures.
- If validation or an emit fails, do not delete the affected exact
  `weft.log.tasks` message IDs.
- Do not mark the affected Monitor summary as emitted when collated external
  logging fails.
- Continue unrelated cleanup that does not require that external emit, such as
  malformed `weft.state.tid_mappings` deletion.
- Record cached PONG diagnostics with enough information to answer "why is it
  not deleting?" without scanning or opening the file during PING.

This is fail-closed for external-log-required deletion and fail-open for
monitor service startup. That is the intended split.

If external logging is disabled:

- Logging is not a deletion precondition.
- Collated mode may record a no-op summary disposition and then delete exact
  raw rows if table-backed deletion is enabled.
- Raw external logging mode is inactive and must not write any files.

### 4.3 Policy Ownership

There must be one live deletion owner for `weft.log.tasks` in a given monitor
cycle:

- `raw` mode: the raw external policy owns eligible `weft.log.tasks` logging
  and deletion. It does not use the Monitor table.
- `collated` mode: the rolling Monitor table plus collated summary policy owns
  eligible `weft.log.tasks` logging and deletion.
- no external logging configured: existing cleanup behavior may remain active,
  but it must still be explicit which path owns `weft.log.tasks` deletion in
  the current cycle. If table-backed deletion is enabled, it should own
  deletion for table-known rows; the broad cleanup runner must not race it on
  the same exact message IDs.

The implementation must make that ownership decision in one place in
`TaskMonitorTask`, not spread it across several policy helpers.

### 4.4 Raw Mode

Raw mode is a debug-style lifecycle dump:

- It scans eligible `weft.log.tasks` rows through generator-backed queue reads.
- It emits one JSONL record per raw broker row before deletion.
- The external record should include at least:
  - schema version
  - `record_type: "task_log_raw"`
  - queue name
  - message ID
  - monitor TID
  - emitted timestamp
  - parsed payload when valid JSON object
  - raw body or a bounded raw-body preview when malformed
  - malformed reason when applicable
- After successful emit, it deletes only that exact broker message ID through
  the canonical exact prune helper.
- It does not write or read Monitor collation tables.
- It copies the retained task-log payload as logged. It must not try to
  recover redacted TaskSpec fields or expand data from task-local queues.

Treat raw records as `DEBUG` level in the Python logging facility. The file
handler for raw mode must be configured so the records are actually emitted.
Do not let a logger-level mismatch silently skip the record and then delete the
broker row.

### 4.5 Collated Standard Mode

Collated mode is the default when external logging is enabled:

- It uses rolling collation in the Monitor table. Each cycle ingests bounded
  task-log rows after the durable checkpoint using
  `MonitorStore.record_task_log_updates(...)`.
- It emits one JSONL summary per eligible task family.
- It deletes exact raw message IDs only after summary emission succeeds or, if
  external logging is disabled, after a no-op summary disposition is recorded.
- It uses the Monitor table's known message IDs for deletion, not a fresh
  in-memory family scan.

Terminal families:

- A family with terminal task-log evidence is eligible only after the terminal
  message or terminal timestamp is older than
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- Successful completed lifecycle proof does not require reserved-queue probes.
- Failure-like terminal records may keep `reserved_probe_needed` for a later
  explicit recovery policy, but this logging plan must not add broad reserved
  cleanup.

Suspected inactive families:

- A family with a start row and no terminal row may be summarized as
  `close_reason: "suspected_inactive"` only when the newest known message in
  the family is older than both:
  - `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`
  - `3 * effective_reporting_interval_seconds`
- The effective reporting interval must be derived conservatively from the
  recorded TaskSpec summary or lifecycle payload. If no numeric interval can
  be derived, do not suspect-close the family in this slice.
- Suspected inactive summaries are operational summaries, not terminal task
  truth. They must not change public status/result reconstruction.
- Deleting raw rows for suspected inactive families is allowed only after the
  summary emit succeeds and only for exact known raw message IDs owned by the
  Monitor table. If implementing this safely requires new liveness probes or a
  broader recovery contract, stop and split suspected-inactive deletion into a
  follow-up plan rather than guessing.

## 5. Invariants And Constraints

- `weft.log.tasks` remains runtime lifecycle evidence while retained, not
  audit, forensic, or legal-retention evidence.
- External logs are operational retention output. They must not become public
  task truth, result truth, status authority, or queue truth.
- Logging-before-delete applies only when external task-log logging is enabled
  and the specific policy requires logging.
- A configured but unhealthy external log sink must block deletion of affected
  `weft.log.tasks` rows and families. It must not prevent the TaskMonitor task
  from starting.
- PONG must stay lightweight. It may expose cached external-log configuration,
  last validation status, last emit error, blocked deletion counts, and last
  successful emit timestamp. It must not open files, validate paths, scan
  queues, query Monitor tables, or recompute candidates during PING.
- All broker row deletion must go through the canonical exact prune helper.
- Do not introduce a second task-log checkpoint outside the Monitor table for
  collated mode. Raw mode may use bounded scan windows, but it must not mutate
  the Monitor collation checkpoint.
- Raw mode must bypass the rolling Monitor-store task-log ingestion for that
  cycle when raw mode owns `weft.log.tasks` deletion. Otherwise the same rows
  can be processed by two owners.
- Do not add a new database table for external log files. The external log is
  a file sink controlled by Python logging.
- Do not add a new dependency. Use Python's standard `logging` package.
- Do not turn `WEFT_TASK_MONITOR_LOG_SINK` into the external lifecycle log
  contract. It remains monitor operational output.
- Keep task-log retention as one canonical duration. Do not maintain separate
  cutoff values that can drift silently.
- Keep tests broker-backed. Mock only the file logging sink or Python logging
  handler when proving sink failure behavior.

Stop and re-plan if:

- implementation wants to delete rows before confirming the required external
  log emit;
- raw and collated modes both attempt to delete the same `weft.log.tasks`
  message IDs in one cycle;
- suspected-inactive deletion requires new liveness or recovery semantics not
  described here;
- PONG implementation wants to perform active I/O;
- code starts writing ad hoc files outside a small external-log sink class;
- tests mock queue behavior rather than using real broker queues.

## 6. Task Breakdown

### 1. Specify and parse the new configuration.

- Outcome: loaded config has one canonical retention period and external
  task-log logging settings.
- Files to touch:
  - `weft/_constants.py`
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `tests/system/test_constants.py`
- Read first:
  - existing task-monitor env parsing in `weft/_constants.py`
  - `tests/system/test_constants.py`
- Implement:
  - constants for `WEFT_LOG_TASKS_EXTERNAL_PATH`,
    `WEFT_LOG_TASKS_EXTERNAL_ENABLED`,
    `WEFT_LOG_TASKS_EXTERNAL_MODE`, and
    `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`;
  - strict parsers for bool, mode, path string, and positive retention seconds;
  - removal of `WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS` as a public
    env/config parser.
- Tests:
  - default config has external logging disabled and retention `172800`;
  - path set with no mode enables external logging and defaults to `collated`;
  - raw mode parses;
  - invalid mode rejects;
  - non-positive retention rejects;
  - the old cutoff env var is not loaded as a recognized Weft config key.
- Done when:
  - `uv run pytest tests/system/test_constants.py -q` passes.

### 2. Extend `TaskMonitorRuntimeConfig`.

- Outcome: monitor runtime code has typed access to external logging and
  retention settings.
- Files to touch:
  - `weft/core/monitor/runtime.py`
  - `tests/tasks/test_task_monitor.py`
- Read first:
  - `TaskMonitorRuntimeConfig.from_config()`
  - existing PONG config assertions in `tests/tasks/test_task_monitor.py`
- Implement:
  - fields for external path, external enabled, external mode, and retention
    seconds;
  - replace the runtime cutoff field with
    `task_log_retention_period_seconds`;
  - do not retain a compatibility alias for the old field name.
- Tests:
  - construct config with raw and collated external modes;
  - assert PONG exposes cached config names using the new external-log fields,
    not only `log_sink`.
- Done when:
  - targeted monitor config/PONG tests pass.

### 3. Add a small external task-log sink wrapper.

- Outcome: one class owns Python logging setup, validation, JSONL formatting,
  and cached sink health.
- Files to add or touch:
  - `weft/core/monitor/external_log.py`
  - `tests/core/test_monitor_external_log.py`
  - `tests/conftest.py` if the new test file must be classified as shared
- Read first:
  - `weft/core/serve_log.py` for existing JSONL style
  - `weft/core/monitor/store.py` for cached status object style
- Implement:
  - a dataclass such as `ExternalTaskLogStatus` with enabled, mode, path,
    healthy, last_error, last_emit_at, emitted, and blocked counts;
  - an `ExternalTaskLogSink` that uses `logging.getLogger(...)` plus a
    `logging.FileHandler` or other stdlib file handler;
  - `validate()` that opens/configures the handler and caches failures;
  - `emit_raw(...)` and `emit_collated(...)` methods that return success or
    raise a narrow local exception such as `ExternalTaskLogError`;
  - JSONL records with stable schema version and `record_type`.
- Constraints:
  - no writes outside the sink class;
  - no queue or Monitor-store imports in the sink class;
  - no active validation from PONG.
- Tests:
  - raw emit writes one JSON object line;
  - collated emit writes one JSON object line;
  - unwritable path or failing handler reports unhealthy and raises;
  - malformed raw payload can still be represented safely.
- Done when:
  - `uv run pytest tests/core/test_monitor_external_log.py -q` passes.

### 4. Add Monitor table eligibility for retention-aged summaries.

- Outcome: store APIs can list terminal and suspected-inactive families only
  when they are older than the configured retention period.
- Files to touch:
  - `weft/core/monitor/store.py`
  - `weft/core/monitor/sql.py`
  - `tests/core/test_monitor_store.py`
  - `tests/core/test_monitor_sql.py`
- Read first:
  - `MonitorStore.list_unemitted_terminal_tasks(...)`
  - `weft_monitor_task_collations` schema in `weft/core/monitor/sql.py`
- Implement:
  - a retention-aware list API, for example
    `list_summary_ready_tasks(limit, now_ns, retention_seconds, include_suspected)`;
  - terminal eligibility based on terminal/completed timestamp age;
  - suspected-inactive eligibility only when an effective reporting interval is
    available and newest known row is older than both retention and 3x that
    interval;
  - summary metadata that records `close_reason`.
- Constraints:
  - do not query broker queues from the store;
  - do not infer public lifecycle terminal state;
  - do not add liveness probes in this task.
- Tests:
  - terminal family newer than retention is not selected;
  - terminal family older than retention is selected;
  - open family with no numeric interval is not suspect-closed;
  - open family older than retention and 3x known interval is selected as
    suspected inactive;
  - SQL builders use parameters for runtime values.
- Done when:
  - store and SQL tests pass on SQLite and through `uv run bin/pytest-pg` for
    those test files.

### 5. Implement collated external summary emission and fail-closed deletion.

- Outcome: collated mode logs summaries before marking summary emitted and
  before deleting exact raw rows.
- Files to touch:
  - `weft/core/monitor/task_monitor.py`
  - `weft/core/monitor/external_log.py`
  - `tests/tasks/test_task_monitor.py`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
- Read first:
  - `_run_monitor_store_cycle()`
  - `_emit_monitor_store_summaries()`
  - `_delete_monitor_store_task_log_rows()`
- Implement:
  - initialize and cache the external sink in `TaskMonitorTask`;
  - call the retention-aware store API;
  - emit collated external summary when external logging is enabled;
  - record no-op summary disposition when external logging is disabled;
  - mark summary emitted only after the required emit succeeds;
  - block table-backed raw deletion when required emit fails;
  - cache PONG fields for external log health and blocked deletion counts.
- Constraints:
  - PONG uses cached fields only;
  - do not use `WEFT_TASK_MONITOR_LOG_SINK` for this lifecycle external log;
  - do not delete rows from an unhealthy external-log-required family.
- Red-green tests:
  - first write a failing test where external logging is enabled and the sink
    raises; assert the broker row remains and Monitor table summary is not
    marked emitted;
  - then implement the sink integration;
  - add a success test where summary line exists before the broker row is
    deleted.
- Done when:
  - `uv run pytest tests/tasks/test_task_monitor.py -q` passes.

### 6. Implement raw mode as a separate policy path.

- Outcome: raw mode logs eligible raw task-log rows and deletes exact rows
  without using Monitor collation tables.
- Files to touch:
  - `weft/core/monitor/task_monitor.py`
  - `weft/core/monitor/cleanup.py` if policy stats should live there
  - `weft/core/monitor/external_log.py`
  - `tests/tasks/test_task_monitor.py`
  - `tests/core/test_task_monitor_cleanup.py`
- Read first:
  - `run_task_monitor_cleanup(...)`
  - `GeneratorTaskLogScanner.scan_window(...)`
  - exact-delete usage in `_delete_monitor_store_task_log_rows()`
- Implement:
  - skip `_run_monitor_store_cycle()` task-log ingestion when raw mode is the
    selected `weft.log.tasks` deletion owner for the cycle;
  - raw mode selection of rows older than retention;
  - raw mode logging through `ExternalTaskLogSink.emit_raw(...)`;
  - exact deletion only after emit success;
  - malformed raw task-log row logging with bounded raw body;
  - cached policy stats such as `task_log.raw_external_log_then_delete`.
- Constraints:
  - raw mode must not read or mutate Monitor collation tables;
  - raw mode must not run in the same cycle as collated table deletion for the
    same queue;
  - if external logging is disabled, raw mode should be rejected or treated as
    inactive. Prefer rejecting raw mode without a path because raw mode's point
    is external row logging.
- Tests:
  - raw mode writes two raw JSONL records and deletes two exact rows;
  - raw mode sink failure keeps rows and reports blocked count;
  - raw mode malformed row is logged and deleted only after successful emit;
  - raw mode does not create Monitor store rows when collation store is disabled.
- Done when:
  - raw mode targeted tests pass under SQLite and Postgres.

### 7. Centralize task-log deletion ownership.

- Outcome: one function decides which `weft.log.tasks` deletion owner runs in a
  cycle.
- Files to touch:
  - `weft/core/monitor/task_monitor.py`
  - `weft/core/monitor/cleanup.py`
  - `tests/tasks/test_task_monitor.py`
  - `tests/core/test_task_monitor_cleanup.py`
- Implement:
  - a small internal enum or literal such as `TaskLogDeletionOwner` with values
    `cleanup_policy`, `raw_external`, `collated_store`, and `none`;
  - owner selection from runtime config;
  - guard cleanup policy runner so it does not select broad task-log deletion
    when raw or collated owner is active for the same rows;
  - keep non-task-log cleanup policies, such as TID mapping cleanup, running.
- Constraints:
  - do not let ownership logic leak into individual row policies;
  - do not duplicate deletion calls;
  - do not change manager supervision.
- Tests:
  - collated mode does not also run broad task-log older-than deletion;
  - raw mode does not run collated table deletion;
  - TID mapping cleanup still runs when external task-log sink is unhealthy;
  - PONG policy stats distinguish "task-log deletion blocked by external log"
    from "policy did not run".
- Done when:
  - targeted monitor and cleanup tests pass.

### 8. Update PONG and operational observability.

- Outcome: operators can tell whether the monitor is collating, logging,
  blocked, or deleting without active PONG work.
- Files to touch:
  - `weft/core/monitor/task_monitor.py`
  - `tests/tasks/test_task_monitor.py`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
- Add cached PONG fields under `extended.task_monitor`, for example:
  - `task_log_external.enabled`
  - `task_log_external.mode`
  - `task_log_external.path`
  - `task_log_external.healthy`
  - `task_log_external.last_error`
  - `task_log_external.last_emit_at`
  - `task_log_external.last_emitted`
  - `task_log_external.last_blocked_deletions`
  - `task_log_retention_period_seconds`
- Tests:
  - PONG after successful external emit reports healthy and emitted count;
  - PONG after sink failure reports unhealthy/error and blocked count;
  - PONG before first cycle reports configured state without opening the file.
- Done when:
  - PONG tests pass and do not need sleeps beyond existing monitor cycle
    drivers.

### 9. Update documentation and implementation mappings.

- Outcome: specs describe the new behavior and point to the owning modules.
- Files to touch:
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/specifications/01-Core_Components.md`
  - `docs/specifications/04-SimpleBroker_Integration.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
  - `docs/specifications/08-Testing_Strategy.md` only if test guidance changes
  - `docs/plans/README.md` if plan status changes after implementation
- Required updates:
  - config reference for new env vars and old retention alias behavior;
  - `MF-5` logging-before-delete semantics;
  - `OBS.13` fail-closed deletion semantics for configured external logs;
  - implementation mapping for `weft/core/monitor/external_log.py`;
  - plan backlinks in touched specs.
- Done when:
  - `uv run pytest tests/specs/test_plan_metadata.py -q` passes.

## 7. Testing Plan

Use red-green TDD for behavior that can fail before implementation:

- external sink failure blocks deletion;
- collated summary must be emitted before raw message deletion;
- raw mode does not use Monitor tables;
- conflicting retention env vars reject;
- PONG remains cached and does not perform active sink validation.

Use real broker-backed queues for:

- `weft.log.tasks` row creation and exact deletion;
- Monitor table collation and checkpoint behavior;
- cleanup policy interaction with `weft.state.tid_mappings`;
- SQLite and Postgres contention-sensitive paths.

Mock or fake only:

- Python logging handler write failure;
- filesystem permission failure, when using a temp path or monkeypatch is
  simpler than depending on platform-specific permissions.

Specific test commands while implementing:

```bash
uv run pytest tests/system/test_constants.py -q
uv run pytest tests/core/test_monitor_external_log.py -q
uv run pytest tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py tests/core/test_task_log_scanner.py -q
uv run bin/pytest-pg tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py tests/core/test_task_monitor_cleanup.py
```

Final verification gates:

```bash
uv run pytest
uv run bin/pytest-pg
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests
git diff --check
```

Runtime observation after deploy:

- `weft task ping <task-monitor-tid>` should show cached
  `task_log_external` status and `task_log_retention_period_seconds`.
- If external logging is disabled, cleanup can still delete according to the
  configured deletion owner and PONG should show no external blocked deletions.
- If external logging is enabled and healthy, the external JSONL file should
  grow before `weft.log.tasks` row count decreases.
- If the external log path is unhealthy, `weft.log.tasks` rows that require
  logging should not decrease, PONG should show the error, and unrelated
  cleanup such as old TID mappings may still proceed.

## 8. Rollout And Rollback

Rollout:

- Ship with external logging disabled by default.
- Keep default retention at 48 hours.
- Keep table-backed deletion behind the existing
  `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` gate unless this implementation
  explicitly changes that spec and tests it.
- Operators can enable collated external logging by setting
  `WEFT_LOG_TASKS_EXTERNAL_PATH` and leaving mode unset.
- Operators can enable raw debug logging by setting
  `WEFT_LOG_TASKS_EXTERNAL_PATH` and `WEFT_LOG_TASKS_EXTERNAL_MODE=raw`.

Rollback:

- Unset `WEFT_LOG_TASKS_EXTERNAL_PATH` or set
  `WEFT_LOG_TASKS_EXTERNAL_ENABLED=0` to remove the logging-before-delete
  precondition.
- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` to disable destructive monitor
  cleanup while preserving observation.
- Disable table-backed deletion with `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED=0`
  if collated deletion behavior is suspect.
- The external JSONL file is append-only operational output; rollback must not
  require reading it back into Weft.

One-way doors:

- Deleting `weft.log.tasks` rows is destructive. Every deletion path must be
  exact-message-ID based, test-proven, and gated by successful required
  external logging.
- Changing retention env names is a public configuration change. Keep the old
  cutoff env as a compatibility alias in this slice.

## 9. Independent Review Loop

This plan is risky: it changes cleanup, external output, retention semantics,
and destructive deletion preconditions. Before implementation, run an
independent engineering review, preferably with `plan-eng-review` or another
available reviewer from a different agent family.

Review prompt:

> Read `docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`,
> `docs/specifications/05-Message_Flow_and_State.md` [MF-5],
> `docs/specifications/07-System_Invariants.md` [OBS.13], and the monitor
> modules under `weft/core/monitor/`. Look for errors, bad ideas, and latent
> ambiguities. Do not implement anything. Could a zero-context engineer
> implement this confidently and correctly?

The implementer must respond to each review finding explicitly. If review
finds that suspected-inactive deletion is underspecified, split that part into
a separate plan rather than weakening the fail-closed logging contract.

## 10. Out Of Scope

- No changes to public task status or result reconstruction.
- No new database tables beyond the existing Monitor-owned collation tables.
- No import of external logging output back into Weft.
- No new SimpleBroker API requirement. Use the current generator and exact
  delete paths.
- No broad task-local retention redesign.
- No manager supervision changes.
- No legal, forensic, or audit retention guarantees.
- No support for multiple external log destinations in this slice.
- No log rotation policy beyond using Python standard logging append behavior.
  If rotation is required later, plan it separately.
- No cross-process external-log file locking in this slice. The manager
  singleton contract should leave only one canonical monitor writing; if
  duplicate monitors exist during a fault, duplicate operational log lines are
  acceptable and must not affect task truth.

## 11. Fresh-Eyes Self Review

Self-review pass completed after drafting.

Findings and fixes:

- Finding: the phrase "external log" was too broad and could be implemented as
  a global Weft log. Fix: the plan now names task-specific env vars and keeps
  `WEFT_TASK_MONITOR_LOG_SINK` as monitor operational output only.
- Finding: raw and collated modes could both delete the same
  `weft.log.tasks` rows. Fix: the plan now requires one centralized deletion
  owner per cycle.
- Finding: retention could become two clocks because an old cutoff env already
  exists. Fix: the plan now makes the new retention env canonical and the old
  cutoff env a compatibility alias with conflict detection.
- Finding: suspected-inactive close semantics could accidentally delete live
  long-running task evidence. Fix: the plan now restricts suspected inactive
  eligibility to families with a numeric effective reporting interval and
  requires a stop-and-split gate if liveness semantics are needed.
- Finding: PONG observability could become active I/O. Fix: the plan now
  requires cached external-log status and explicitly forbids validation during
  PING.
- Finding: raw mode still looked like it could run store collation first and
  then perform raw deletion. Fix: the plan now says raw mode bypasses
  Monitor-store task-log ingestion for that cycle.
- Finding: external path resolution and raw log redaction boundaries were not
  explicit. Fix: the plan now resolves relative paths under
  `WeftContext.logs_dir` and forbids expanding redacted data.

Residual risk:

- Suspected-inactive deletion is the riskiest part. The plan includes a
  conservative definition, but independent review should still pressure-test
  whether it belongs in the first implementation slice.
