# Manager Serve Operational Log Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.6a], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/10-CLI_Interface.md [CLI-1.1.2], [CLI-5]
Superseded by: none

## 1. Goal

Add an opt-in operational log for `weft manager serve` deployments so an
operator can see exactly where a foreground-supervised manager is failing to
make progress: registry ownership, manager loop wakeups, internal service
convergence, spawn reservation and launch, and TaskMonitor cleanup cycles. The
operational log must be structured, rate-limited, written to process logs, and
controlled by a serve-scoped level such as `--level=info`, `--level=debug`, or
`--level=trace`. It must not write rows to `weft.log.tasks`, change task
lifecycle truth, or create a second execution path.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.4], [MA-1.6a], [MA-3]: manager registry heartbeat/leadership,
  manager-owned internal service supervision, and foreground `manager serve`
  lifecycle.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5], [MF-6]: task-log observation, TaskMonitor behavior, public/internal
  spawn flow, and strict internal-spawn priority.
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.2], [CLI-5]: `weft manager serve` foreground supervisor command and
  configuration ownership through `_constants.py`.

Related plans and how to read them:

- [`2026-05-11-manager-work-stealing-dispatch-plan.md`](./2026-05-11-manager-work-stealing-dispatch-plan.md)
  is the active draft for the current dispatch-progress failure. This
  operational-log plan should help prove that implementation, but must not
  silently implement dispatch behavior changes.
- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md)
  is completed and defines the manager service convergence state machine.
  Preserve its single reducer path.
- [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md)
  is completed and defines service-authority boundaries. The operational log
  may report service evidence, but must not weaken who can claim a singleton
  service.
- [`2026-05-09-internal-spawn-priority-queue-plan.md`](./2026-05-09-internal-spawn-priority-queue-plan.md)
  is completed and defines `weft.spawn.internal` priority. The operational log
  should prove whether that path is reached.
- [`2026-05-09-prune-path-unification-plan.md`](./2026-05-09-prune-path-unification-plan.md)
  is completed and defines shared prune behavior. TaskMonitor operational-log
  events must describe prune outcomes without introducing a second prune
  implementation.
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md),
  [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md),
  and [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
  define the planning, hardening, and review requirements for this risky
  manager-runtime observability slice.

## 3. Context And Key Files

Files to modify:

- `weft/_constants.py`
- `weft/cli/app.py`
- `weft/commands/serve.py`
- `weft/commands/manager.py` only if the serve command needs to pass options
  through `_serve_manager_foreground`
- `weft/core/manager_runtime.py` only if the foreground serve helper owns the
  final manager TaskSpec/config override application
- `weft/core/manager.py`
- `weft/core/manager_services.py` only if the reducer needs to expose a compact
  decision summary without duplicating reducer logic
- `weft/core/tasks/task_monitor.py`
- `tests/cli/test_cli_serve.py`
- `tests/core/test_manager.py`
- `tests/core/test_task_monitor.py` or the closest existing TaskMonitor test
  file if it has been renamed
- `tests/specs/test_plan_metadata.py` is not edited, but must pass
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`

Read first:

- `weft/_constants.py`: `_load_weft_env_vars`,
  `_normalize_weft_override_value`, and the existing TaskMonitor constants.
  All new env names, defaults, parser helpers, and config normalization belong
  here. Do not scatter raw `os.environ` reads into manager code.
- `weft/cli/app.py`: `manager_serve_command`. This is the Typer boundary for
  the new serve option.
- `weft/commands/serve.py`: `serve_command`. This is the thin command adapter
  for foreground manager serve.
- `weft/commands/manager.py` and `weft/core/manager_runtime.py`:
  `_serve_manager_foreground` and the foreground manager runtime construction.
  This is where serve-specific config overrides should be applied if the
  option is not purely environment-driven.
- `weft/core/manager.py`: `_read_active_manager_records`,
  `_evaluate_dispatch_ownership`, `_maybe_yield_leadership`,
  `_has_pending_messages`, `wait_for_activity`, `process_once`,
  `_run_managed_service_convergence`, `_reconcile_managed_services`,
  `_tick_managed_service`, `_enqueue_managed_service_request`,
  `_drain_internal_spawn_requests`, `_handle_work_message`,
  `_build_child_spec`, and `_launch_child_task`.
- `weft/core/manager_services.py`: `reduce_managed_service_state`,
  `ManagedServiceState`, `ManagedServiceEvidence`, `ManagedServiceDecision`,
  and existing candidate summary helpers.
- `weft/core/tasks/task_monitor.py`: `process_once`, `_run_monitor_cycle`,
  processor invocation, checkpoint advancement, and activity reporting.
- `tests/cli/test_cli_serve.py`: foreground serve process tests and helper
  process launch path.
- `tests/core/test_manager.py`: manager-owned service convergence,
  internal-spawn, registry, and dispatch tests.

Current structure:

- `weft manager serve` is the deployment pattern under `systemd`, `launchd`,
  Docker Compose, or another external supervisor. It runs the canonical manager
  in the foreground and exits when another live canonical manager already owns
  the context.
- Manager loop behavior is mostly visible only through task lifecycle events
  and process titles. Those surfaces are not enough when the manager is alive
  but failing to dispatch or supervise internal services.
- `weft.log.tasks` is already part of the observed load problem. The
  operational log must not write high-frequency records there.
- Existing Python logging is available, but ordinary logging is disabled by
  default unless configured. This plan should make the serve operational log
  explicitly opt-in and routed to process stderr/stdout so container logs
  capture it.
- Managed services already reduce through one path:
  `Manager._run_managed_service_convergence` ->
  `_reconcile_managed_services` -> `_tick_managed_service` ->
  `reduce_managed_service_state` -> `_enqueue_managed_service_request`.
  Operational-log instrumentation must observe this path, not fork it.

Comprehension checks before editing:

1. Which layer owns CLI option parsing, and which layer owns the Manager's
   runtime config?
2. Why is `weft.log.tasks` the wrong place for high-frequency manager
   operational-log events during this incident?
3. Which function applies service reducer side effects after a `start_now`
   decision?
4. Which queue proves TaskMonitor was enqueued, and which evidence proves it
   started as a child?
5. Which failures should the operational log report but not change: registry
   liveness ambiguity, enqueue failure, child launch failure, or processor
   errors?

## 4. Invariants And Constraints

- Operational-log events are observational only. They must not change task scheduling,
  manager leadership, service reducer decisions, queue deletion, or TaskMonitor
  pruning semantics.
- No operational-log event is lifecycle truth. Do not use operational-log
  events to infer task status, result availability, or cleanup eligibility.
- Do not write serve operational-log events to `weft.log.tasks`, per-task
  outboxes, control queues, or runtime state queues.
- Do not add a new durable queue or side database for operational-log events.
- Keep the durable execution spine unchanged:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Keep `weft manager serve` as the only CLI surface for the new option. Do not
  add broad logging flags to `weft run`, `weft manager start`, or ordinary task
  commands in this slice.
- All new env/config constants live in `weft/_constants.py`. Do not read raw
  operational log env vars in `manager.py`, `task_monitor.py`, or CLI modules.
- The CLI option must be serve-scoped and explicit. Use one generic manager-log
  option instead of several narrow incident-specific flags:
  `weft manager serve --level=info`, `--level=debug`, or `--level=trace`.
  `off` is the default level. Do not add a separate `--diagnostics` boolean.
- Level semantics:
  - `off`: no operational-log output.
  - `info`: low-volume state changes and periodic summaries suitable for
    production incident monitoring.
  - `debug`: branch decisions for manager ownership, service convergence,
    enqueue/drain, spawn launch, and TaskMonitor cycle summaries.
  - `trace`: bounded per-turn and per-message detail. Still rate-limited and
    bounded; never dump full payloads or secrets.
- Add an optional `--log-interval` only if the default interval is not enough.
  Do not overload `--level` with interval behavior.
- Add constants for event schema and rate limits in `_constants.py`, for
  example:
  - `MANAGER_SERVE_LOG_SCHEMA`
  - `WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT`
  - `WEFT_MANAGER_SERVE_LOG_LEVELS`
  - `MANAGER_SERVE_LOG_SCHEMA_VERSION`
  - `WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT`
  - `MANAGER_SERVE_LOG_EVENT_MAX_CHARS`
  - `MANAGER_SERVE_LOG_COMPONENTS`
- Add environment names in `_constants.py`, for example:
  - `WEFT_MANAGER_SERVE_LOG_LEVEL`
  - `WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS`
  These are optional defaults for supervised deployments that cannot easily
  pass CLI flags. `--level` must override env configuration for that
  invocation. `WEFT_MANAGER_SERVE_LOG_LEVEL=off` disables output.
- Operational log output must be structured JSON Lines, one object per line.
  Required indexing metadata on every event:
  - `schema`: stable string, e.g. `"weft.manager_serve_log"`
  - `schema_version`: integer constant
  - `event`: compact event name
  - `component`: one of the allowed component constants
  - `timestamp_ns`
  - `manager_tid`
  - `manager_tid_short`
  - `weft_context` when available
  - `runtime_handle_id` when available
  - `pid` when available
  - `loop_iteration` when emitted from manager loop paths
  - `severity`: `info`, `warning`, or `error`
  - `configured_level`: `info`, `debug`, or `trace`
  - `required_level`: the minimum manager-log level needed for this event
- Event level guidance:
  - `info` events: `manager_loop_summary`, `manager_registry_snapshot` only on
    changes or unhealthy states, `managed_service_decision` only for
    `start_now`, `schedule_restart`, duplicate cleanup, enqueue failure, or
    other non-stable decisions, and TaskMonitor cycle summaries.
  - `debug` events: all manager ownership decisions, service reducer decisions,
    managed-service enqueue/drain decisions, spawn launch results, and
    TaskMonitor processor summaries.
  - `trace` events: per-turn immediate-wake observations, per-message spawn
    reservation/ack details, bounded candidate lists, and other details that
    are too noisy for production `info`.
- Event payloads must be bounded. Truncate long candidate lists, payload
  excerpts, exception messages, and queue summaries using constants from
  `_constants.py`.
- Do not log secrets, broker passwords, full environment dicts, or full
  TaskSpec payloads. Log task names, TIDs, queue names, service keys, processor
  names, and bounded error strings.
- Operational-log events must be rate-limited. High-frequency loop events
  should emit on state changes and then no more often than the configured
  interval.
- Error-path operational-log events are best effort. An operational-log
  emission failure must not crash the manager, suppress a child launch, or
  change cleanup behavior.
- Existing `WEFT_DEBUG` and `WEFT_LOGGING_ENABLED` semantics must remain
  compatible. This feature may configure a local stderr JSONL handler for
  `manager serve`, but must not globally reinterpret those flags.
- No new dependency.

Stop and re-plan if:

- the implementation wants a second manager service reducer, second spawn
  drain, or separate TaskMonitor launch path;
- operational-log events require fixed-limit reads of append-only queues for
  correctness;
- output begins to include full spawn payloads, secrets, or unbounded task-log
  excerpts;
- the CLI option starts spreading into unrelated commands;
- tests mock away the manager loop, broker queues, or service reducer when the
  real path is practical.

## 5. Tasks

1. Specify the serve-scoped operational log contract.
   - Outcome: specs describe an opt-in `manager serve` operational log mode as
     operational output, not lifecycle truth.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Required edits:
     - In [MA-3], state that `manager serve` may enable structured process-log
       operational-log events for supervisor-managed deployments.
     - In [MF-5], state that operational-log events are operational output
       only and must not be written to `weft.log.tasks`.
     - In [CLI-1.1.2], document `--level=off|info|debug|trace` and optional
       `--log-interval`.
     - In [CLI-5], list the new env vars and their precedence.
   - Constraints:
     - Do not describe operational-log events as status truth or pruning
       evidence.
     - Do not add a new queue contract.
   - Tests: none for docs alone.
   - Done when: the spec language agrees with this plan and preserves the
     queue/state invariants above.

2. Add constants and config parsing in `_constants.py`.
   - Outcome: all operational-log defaults, env names, allowed levels, schema
     names, component names, event bounds, and parser behavior are centralized.
   - Files to touch:
     - `weft/_constants.py`
     - tests for config parsing if such tests exist; otherwise add focused
       assertions to the closest constants/config test file.
   - Approach:
     - Add `Final[...]` constants near related manager/TaskMonitor constants,
       not at random module locations.
     - Add `_parse_manager_serve_log_level` that accepts only
       `off`, `info`, `debug`, and `trace`.
     - Add `_parse_manager_serve_log_interval` using the existing
       positive-float or positive-int parser style. The interval must reject
       zero and negative values.
     - Extend `_load_weft_env_vars()` with the new env vars.
     - Extend `_normalize_weft_override_value()` so command-layer overrides can
       pass strings for level and int/float/str for interval.
     - Update `load_environment()` docstring only enough to mention the new
       operational-log keys.
   - Required constants:
     - `MANAGER_SERVE_LOG_SCHEMA`
     - `MANAGER_SERVE_LOG_SCHEMA_VERSION`
     - `MANAGER_SERVE_LOG_EVENT_MAX_CHARS`
     - `MANAGER_SERVE_LOG_CANDIDATE_LIMIT`
     - `MANAGER_SERVE_LOG_CHILD_LIMIT`
     - `WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT`
     - `WEFT_MANAGER_SERVE_LOG_LEVELS`
     - `WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT`
     - `MANAGER_SERVE_LOG_COMPONENTS`
   - Suggested env vars:
     - `WEFT_MANAGER_SERVE_LOG_LEVEL`
     - `WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS`
   - Tests:
     - env default is `off`;
     - level parsing accepts `off`, `info`, `debug`, and `trace`;
     - level parsing rejects unknown values;
     - interval rejects zero/negative/non-numeric values;
     - command override normalization preserves level and numeric interval.
   - Stop if:
     - an implementation reads operational log env vars outside `_constants.py`.
   - Done when:
     - config tests pass and mypy sees concrete value types.

3. Add the CLI and command plumbing for `manager serve`.
   - Outcome: operators can enable the manager operational log with
     `weft manager serve --level=info`, `--level=debug`, or `--level=trace`.
   - Files to touch:
     - `weft/cli/app.py`
     - `weft/commands/serve.py`
     - `weft/commands/manager.py` or `weft/core/manager_runtime.py` only as
       needed to pass config overrides into the foreground manager invocation.
     - `tests/cli/test_cli_serve.py`
   - Approach:
     - Keep Typer parsing in `weft/cli/app.py`.
     - Keep `weft/commands/serve.py` as a thin adapter. It should build
       context, prepare explicit config overrides, and call the existing
       foreground manager helper.
     - Apply overrides through the existing config path. Do not attach ad hoc
       attributes to `Manager`.
     - If `_serve_manager_foreground` currently does not accept overrides,
       extend its signature narrowly for config overrides and update callers.
   - Tests:
     - `weft manager serve --help` includes `--level` and, if implemented,
       `--log-interval`.
     - Starting `manager serve --level=info` launches the manager and causes
       at least one operational-log JSONL line on stderr/stdout without changing
       the existing foreground behavior.
     - Starting with no `--level` and no env override emits no operational-log
       JSONL line.
     - Starting with `WEFT_MANAGER_SERVE_LOG_LEVEL=debug` emits debug-level
       operational-log events, and an explicit `--level=off` suppresses them.
   - Stop if:
     - the option is added to `weft manager start`, `weft run`, or global app
       options.
   - Done when:
     - CLI tests prove the operational log is serve-scoped, level-controlled,
       and opt-in.

4. Implement a small operational log emitter, preferably in `weft/core/manager.py`
   unless reuse pressure proves a helper module is needed.
   - Outcome: Manager can emit bounded structured JSONL events with the
     required indexing metadata and rate limiting.
   - Files to touch:
     - `weft/core/manager.py`
     - `weft/_constants.py` only if Task 2 missed a bound or component
     - `tests/core/test_manager.py`
   - Approach:
     - Add private Manager helpers such as `_manager_log_enabled()`,
       `_manager_log_allows(required_level)`,
       `_log_event_base()`, `_emit_serve_log()`,
       `_emit_serve_log_rate_limited()`, and `_truncate_log`.
     - Use `print(..., file=sys.stderr, flush=True)` or a local logger handler
       only after checking existing logging setup. The output must reliably
       reach Docker/systemd process logs when enabled.
     - Every event object must contain the indexing metadata named in section
       4.
     - On serialization failure, fall back to one bounded error
       operational-log event or suppress the event. Never raise from
       operational-log emission.
   - Tests:
     - instantiate a Manager with operational logging enabled through config
       overrides, emit one event, parse JSON, and assert required metadata.
     - assert an `info` configuration suppresses `debug` and `trace` events,
       while `debug` includes `info` and `debug` but suppresses `trace`.
     - assert long strings/lists are bounded.
     - assert operational-log level `off` is silent.
   - Do not mock:
     - manager construction, context, or broker setup more than existing tests
       already do. This is not a pure formatting function detached from
       Manager identity.
   - Stop if:
     - the helper wants to become a public logging framework or a second
       status API.
   - Done when:
     - the emitter is private, bounded, typed, and covered by focused tests.

5. Add manager loop and ownership operational log.
   - Outcome: process logs can explain hot-loop conditions and registry
     ownership decisions.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Instrumentation points:
     - `_read_active_manager_records`: emit when stale records are pruned,
       when own registration is refreshed, and when active records reduce to
       zero or more than one canonical record.
     - `_evaluate_dispatch_ownership`: emit state changes among `self`,
       `other`, `none`, and `unknown`.
     - `process_once`: emit a rate-limited loop summary while operational log is
       enabled.
     - `wait_for_activity`: emit a rate-limited event when it returns
       immediately because pending messages exist.
   - Required event names:
     - `manager_registry_snapshot`
     - `manager_ownership_decision`
     - `manager_loop_summary`
     - `manager_wait_immediate_wake`
   - Required payload fields:
     - active manager tids and count;
     - leader tid;
     - ownership state;
     - stale/pruned count;
     - pending public/internal/control booleans or counts, using cheap
       `has_pending` checks unless a count is already available;
     - child count and bounded child/service summary;
     - `should_stop` and `draining`.
   - Tests:
     - a stale active manager record produces a registry operational-log event
       when the configured level permits it.
     - a pending public spawn queue with no drain progress produces a bounded
       loop/wait operational-log event without consuming the message.
   - Stop if:
     - operational-log instrumentation performs full queue-history scans in the
       hot path just to calculate counts.
   - Done when:
     - a test can distinguish "manager woke because public spawn is pending"
       from "manager is blocked by ownership."

6. Add managed-service convergence operational log.
   - Outcome: logs show why heartbeat and TaskMonitor are or are not being
     started.
   - Files to touch:
     - `weft/core/manager.py`
     - `weft/core/manager_services.py` only if a compact decision summary
       should be exposed by the reducer without duplicating reducer logic.
     - `tests/core/test_manager.py`
     - `tests/core/test_manager_services.py` if reducer summary is added.
   - Instrumentation points:
     - `_run_managed_service_convergence`: pass count, active/stable interval,
       include flags, internal drain attempted, internal drain count.
     - `_reconcile_managed_services`: desired keys, pending keys,
       keys needing evidence.
     - `_tick_managed_service`: service state before and after reducer,
       candidate summaries, reducer action, canonical live candidate, enqueue
       target, enqueue success/failure.
     - `_enqueue_managed_service_request`: target queue and write result.
     - `_drain_internal_spawn_requests`: attempted rounds and processed count.
   - Required event names:
     - `managed_service_convergence`
     - `managed_service_reconcile`
     - `managed_service_decision`
     - `managed_service_enqueue`
     - `internal_spawn_drain`
   - Required payload fields:
     - `service_key`;
     - `spawn_pending`, `active_tid`, `launched_once`, `next_allowed_ns`,
       `uncertain_attempts`, `last_uncertain_reason`;
     - `pending_spawn`;
     - bounded `candidates` with `tid`, `state`, `source`, and `reason`;
     - reducer `action`;
     - enqueue queue name;
     - drain count.
   - Tests:
     - when TaskMonitor is enabled and no evidence exists, operational-log
       events show
       `action=start_now`, enqueue target `weft.spawn.internal`, and a later
       internal drain attempt.
     - when a pending TaskMonitor spawn exists, operational-log events show
       `pending_spawn=true` and do not enqueue a duplicate.
   - Stop if:
     - implementation duplicates reducer branching in operational-log code
       instead of observing the reducer decision.
   - Done when:
     - a failing ops case could be classified from logs as one of:
       "not desired", "pending spawn", "enqueue failed", "internal drain did
       not process", "launch failed", or "candidate considered live."

7. Add spawn reservation and child-launch operational-log events for manager-owned
   service requests.
   - Outcome: logs show whether a service spawn request is consumed, parsed,
     launched, and acknowledged.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Instrumentation points:
     - `_handle_work_message`: source queue, reserved queue, timestamp,
       service key if present, and control launch permission.
     - `_build_child_spec`: validation failure reason for operational-log
       events, without logging full payloads.
     - `_launch_child_task`: child tid/name, internal runtime class, service
       key, process pid/runtime handle summary, launch success/failure.
     - reserved ack after launch: ack success/failure.
   - Required event names:
     - `spawn_reserved`
     - `spawn_spec_validation_failed`
     - `child_launch_result`
     - `spawn_ack_result`
   - Required payload fields:
     - source queue;
     - reserved queue;
     - message timestamp;
     - child tid/name when known;
     - internal runtime class;
     - service key;
     - launch result;
     - bounded error text.
   - Tests:
     - an internal TaskMonitor spawn emits reservation, launch, and ack
       operational-log events in order.
     - invalid internal service payload emits validation failure
       operational-log events while preserving existing reserved policy
       behavior.
   - Stop if:
     - tests assert exact full JSON object equality; assert required fields
       instead so new bounded fields can be added without brittle churn.
   - Done when:
     - service-spawn logs identify the exact point where a service spawn died.

8. Add TaskMonitor lifecycle and processor-cycle operational-log events.
   - Outcome: when TaskMonitor runs under the serve operational log, logs show its
     resolved config and cleanup progress without adding a logging callback to
     the prune processor.
   - Files to touch:
     - `weft/core/tasks/task_monitor.py`
     - `tests/core/test_task_monitor.py` or the existing closest equivalent
     - `weft/_constants.py` only if TaskMonitor-specific operational-log bounds
       are needed.
   - Approach:
     - Pass operational-log configuration through the TaskSpec/env/config path
       already used for TaskMonitor settings. Do not add a second callback or
       processor mode.
     - Emit one startup/config event when the task starts or on first
       `process_once`.
     - Emit a rate-limited cycle summary after each processor result or after
       failures.
     - Reuse existing processor summary data. If the processor summary does
       not expose enough information, extend that summary in the prune path
       rather than re-computing cleanup decisions in TaskMonitor.
   - Required event names:
     - `task_monitor_config`
     - `task_monitor_cycle`
     - `task_monitor_processor_error`
   - Required payload fields:
     - processor name;
     - enabled state;
     - interval;
     - batch size;
     - sink/checkpoint settings without secrets;
     - scanned count;
     - candidate count;
     - deleted count;
     - skipped count by reason if already available;
     - checkpoint advanced yes/no;
     - next wake time or interval.
   - Tests:
     - TaskMonitor emits config and cycle summary when the configured level
       permits those events.
     - Operational-log level `off` stays silent.
     - Processor errors are logged but existing failure behavior is unchanged.
   - Stop if:
     - implementation adds `jsonl_then_delete` logging callback behavior in
       this slice. That is explicitly later work.
   - Done when:
     - process logs can show "TaskMonitor exists and deleted N rows" or
       "TaskMonitor exists but processor failed/skipped all candidates."

9. Add final CLI and runtime smoke coverage.
   - Outcome: the serve-scoped operational log can be exercised through the public
     CLI and remain silent by default.
   - Files to touch:
     - `tests/cli/test_cli_serve.py`
   - Test shape:
     - Start `weft manager serve --level=info --context <tmp>` with
       `WEFT_TASK_MONITOR_ENABLED=1`.
     - Wait for a bounded operational log line containing
       `managed_service_decision` or `manager_loop_summary`.
     - Parse the line as JSON and assert required indexing metadata.
     - Verify the manager still registers exactly one live canonical manager.
     - Verify no operational log row appears in `weft.log.tasks` by scanning for
       the operational log schema string in the task log queue.
   - Do not mock:
     - CLI process, manager process, broker queues, or service convergence.
   - Stop if:
     - this test requires sleeps instead of existing wait helpers. Add a small
       helper that waits for a matching stderr/stdout line with a deadline.
   - Done when:
     - the real foreground serve path proves opt-in operational logging without
       behavior changes.

10. Update documentation indexes and implementation notes.
    - Outcome: plan and spec traceability stays bidirectional.
    - Files to touch:
      - `docs/plans/README.md`
      - `docs/specifications/03-Manager_Architecture.md`
      - `docs/specifications/05-Message_Flow_and_State.md`
      - `docs/specifications/10-CLI_Interface.md`
      - `docs/lessons.md` only if implementation reveals a reusable lesson.
    - Required edits:
      - Add this plan to `docs/plans/README.md` with status `draft`.
      - Add backlinks under each relevant spec's `Related Plans`.
      - Update nearby implementation mapping notes if functions or option
        ownership change.
    - Done when:
      - `tests/specs/test_plan_metadata.py` passes.

## 6. Testing Plan

Use red-green TDD where practical.

- First red test: `tests/cli/test_cli_serve.py` should assert
  `weft manager serve --level=info` exists and emits one parseable
  operational-log JSONL record with required indexing metadata. It should fail
  before CLI and emitter work lands.
- Second red test: `tests/core/test_manager.py` should construct a manager with
  TaskMonitor enabled and operational-log level `debug`, run service
  convergence, and assert an operational-log record shows
  `service_key=_weft.service.task_monitor`, `action=start_now`, and enqueue
  target `weft.spawn.internal`. It should fail before convergence
  operational-log events land.
- Third red test: TaskMonitor operational logging should emit config and cycle
  summary when the level permits them. It should fail before TaskMonitor
  instrumentation lands.

Test files:

- `tests/cli/test_cli_serve.py`
- `tests/core/test_manager.py`
- `tests/core/test_task_monitor.py` or the closest existing TaskMonitor test
  module
- closest existing `_constants.py` config tests, or a new focused test file if
  none exists
- `tests/specs/test_plan_metadata.py`

What to assert:

- Operational logging is off by default.
- `--level=info|debug|trace` is scoped to `weft manager serve`.
- `WEFT_MANAGER_SERVE_LOG_LEVEL` works for supervised deployments, and CLI
  options override env.
- Every emitted operational-log line is valid JSON with required indexing
  metadata.
- Operational-log payloads are bounded and redact secrets.
- `info`, `debug`, and `trace` levels expose progressively more detail without
  changing manager behavior.
- Manager loop, ownership, service convergence, spawn launch, and TaskMonitor
  cycle events each expose the branch decision needed to diagnose failure.
- No operational-log output is written into `weft.log.tasks`.
- Existing manager behavior is unchanged when operational logging is disabled.

What not to mock:

- SimpleBroker queues.
- manager foreground process for CLI smoke coverage.
- manager service reducer decisions.
- TaskMonitor processor invocation when a real small broker-backed test is
  practical.

Acceptable mocks:

- `sys.stderr` or logging sink capture for unit-level emitter tests.
- A forced processor error in a TaskMonitor test, only to prove
  operational-log events are best-effort and do not change failure behavior.

Edge cases to include:

- long error string truncation;
- too many service candidates truncation;
- invalid env interval rejected;
- `--level=info` with TaskMonitor disabled still emits manager loop summaries
  but not TaskMonitor service decisions as desired internal work.
- `--level=off` suppresses env-enabled logging for that invocation.

Out of scope for tests:

- full production Docker Compose test;
- exact line ordering across concurrent child processes;
- measuring CPU reduction. Operational logs expose the issue; they do not fix
  it.

## 7. Verification And Gates

Per-task verification:

```bash
uv run pytest tests/specs/test_plan_metadata.py -q
uv run pytest tests/cli/test_cli_serve.py -q
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/core/test_task_monitor.py -q
```

If the TaskMonitor tests live elsewhere, substitute the existing task-monitor
test module found by `rg "TaskMonitor|task_monitor" tests`.

Final gates:

```bash
uv run pytest tests/cli/test_cli_serve.py tests/core/test_manager.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft
```

Full-suite gate:

- Run the full suite if the implementation changes manager runtime signatures,
  TaskMonitor processor summaries, or shared config normalization in a way that
  can affect many commands.

Post-deploy observation:

- Deploy with `weft manager serve --level=info` or
  `WEFT_MANAGER_SERVE_LOG_LEVEL=info`.
- Do not use `weft status` while investigating control-PING load.
- Use `docker compose logs opsworker-serve` or supervisor logs to verify:
  - one active manager loop summary appears at the configured interval;
  - `debug` adds branch-level service and spawn decisions when needed;
  - `trace` adds bounded per-turn/per-message details only when explicitly
    requested;
  - ownership decisions name the active manager and leader;
  - managed-service decisions explain heartbeat and TaskMonitor state;
  - TaskMonitor config and cycle records appear if it starts;
  - no operational log records are written to `weft.log.tasks`.

## 8. Rollout And Rollback

Rollout:

- Ship operational logging disabled by default.
- Enable only on foreground-supervised deployments that need incident
  visibility.
- Prefer env enablement for Docker Compose/systemd where changing command
  lines is slower; prefer CLI flag in tests and ad hoc local runs.

Rollback:

- Disable operational logging by removing `--level`, passing `--level=off`, or
  setting `WEFT_MANAGER_SERVE_LOG_LEVEL=off`.
- Since operational-log events do not write durable state or change scheduling,
  rollback must not require queue cleanup.
- If enabling operational logging causes unexpected log volume, lower the
  level, increase the interval, or disable the feature. Do not alter
  TaskMonitor or manager state to undo operational logging.

One-way doors:

- None intended. Adding a public CLI option is a compatibility commitment, so
  name it conservatively and document it before release.

## 9. Independent Review Loop

Before implementation, ask a different agent family to review the plan.
Suggested prompt:

> Read `docs/plans/2026-05-11-manager-serve-operational-log-plan.md` and the
> referenced code paths. Do not implement. Look for latent ambiguity, wrong
> file ownership, unsafe logging, poor test design, and any way the operational
> log could change manager behavior. Could you implement this confidently and
> correctly if asked?

Required review focus:

- `weft/_constants.py` ownership and env parsing.
- `weft manager serve` option scoping.
- whether required operational-log metadata is sufficient for indexing and
  incident triage.
- whether any proposed instrumentation risks queue scans, state mutation, or
  secret leakage in hot paths.
- whether tests are sufficiently real and not mock-heavy.

The implementing engineer must address each review point by updating this
plan, changing the implementation, or explicitly recording why the issue is
out of scope.

## 10. Out Of Scope

- Fixing dispatch progress, stale manager ownership, or TaskMonitor launch
  bugs. This plan adds visibility for those issues.
- A general logging framework for all Weft commands.
- A new `weft status` replacement.
- Writing operational-log events into `weft.log.tasks`.
- Adding a logging callback processor such as `jsonl_then_delete`.
- Changing prune eligibility or deletion behavior.
- Adding new durable queues or state tables.
- Adding Docker Compose specific code to core Weft.

## 11. Fresh-Eyes Review

Review pass 1:

- Ambiguity found: "proper indexing metadata" could mean plan README indexing
  or log-event indexing. This plan now includes both: `docs/plans/README.md`
  index maintenance and required JSONL event metadata.
- Ambiguity found: the operational log sink could drift into `weft.log.tasks`
  because that is Weft's normal lifecycle log. This plan explicitly forbids
  that and requires process logs.
- Ambiguity found: implementation might add a broad global operational log flag.
  This plan scopes the CLI option to `weft manager serve` and uses env defaults
  only for supervisor deployments.
- Ambiguity found: TaskMonitor operational log could become the postponed logging
  callback. This plan forbids implementing `jsonl_then_delete` callback
  behavior in this slice.

Review pass 2:

- The task list names files, functions, tests, stop gates, and done signals.
- The invariants say what must not change.
- The tests use real broker/manager paths where behavior matters.
- The plan remains an observability slice and does not drift into fixing the
  dispatch bug.
