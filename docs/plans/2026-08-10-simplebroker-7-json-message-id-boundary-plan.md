# SimpleBroker 7 JSON Message-ID Boundary Plan

Status: completed
Source specs: `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.2], [SB-0.3], [SB-0.4a], Operational Notes; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1], [CLI-1.1.2], [CLI-1.2.1], [CLI-1.2], [CLI-1.3], [CLI-4], [CLI-6]
Superseded by: none

Class: 5. This raises a load-bearing dependency major-version floor and changes
Weft-owned public JSON representations and accepted dump/input forms. It
requires normative spec revision, explicit boundary inventory, red-green
proof, hardening, and independent review.

Plan type: implementation with spec revision.

## Goal

Raise Weft's supported floors to `simplebroker>=7.0.0` and
`simplebroker-pg>=3.5.2`. Make every Weft-owned external JSON representation of
a SimpleBroker message ID an exact 19-digit ASCII decimal string produced by
the public package-root `simplebroker.format_message_id` helper. Keep Python
objects, broker and Monitor relational message-ID columns, stored
broker-message bodies, internal control/work JSON, TIDs, comparison logic, and
Unix-clock measurements in their current types. Store explicitly owned exact
broker-ID fields inside Monitor table JSON as canonical strings and normalize
them back to integers on read.

Accept exact message-ID inputs as either integers or canonical strings at
Weft-owned boundaries and immediately normalize accepted values to `int`.
There is no compatibility writer, dump-version branch, schema migration, or
generic JSON-number rewrite.

## Source Documents

- Repository process: `AGENTS.md`; `docs/agent-context/README.md`;
  `docs/agent-context/decision-hierarchy.md`;
  `docs/agent-context/principles.md`;
  `docs/agent-context/engineering-principles.md`; the planning, hardening,
  review-loop, runtime-context, testing-pattern, and adversarial-acceptance
  runbooks; `docs/agent-context/lessons.md`; and `docs/lessons.md`.
- Governing Weft specs: the three files and references in the metadata above.
- SimpleBroker source tag `v7.0.0` at
  `b58ef6619927812adfb6d03d2d1838ab421449f1`, including the package-root
  formatter export, exact-ID normalizer, dump/load behavior, CLI JSON behavior,
  and message-identity/API specs.
- SimpleBroker PostgreSQL extension tag `v3.5.2`.
- Taut precedent:
  `../taut/docs/plans/2026-08-10-simplebroker-7-json-id-boundary-plan.md` and
  its current implementation delta. Adopt its explicit field inventory,
  fixed unsafe adjacent IDs, tolerant integer-or-canonical-string readers,
  immediate integer normalization, and package-root formatter use. Also adopt
  its later A3 correction: a string input must equal the formatter's canonical
  result, because upstream validation otherwise accepts some padded or
  non-ASCII decimal forms. Do not copy Taut's MCP cursor rules or
  persistence-component machinery, which Weft does not own.

## Spec Baseline

- `8fdb54f27371f3c02a3d4a937368b8bc4bf30663` for the three governing Weft
  specs.
- Upstream contract baseline:
  `b58ef6619927812adfb6d03d2d1838ab421449f1` (`v7.0.0`) and PostgreSQL
  extension `v3.5.2`.
- Promotion strategy: A (spec first). After this plan receives an independent
  corrective `PASS`, promote the exact requirement text and plan backlinks
  before changing dependencies or production code. Each implementation slice
  then adds its reciprocal code/test mapping. Record the promotion baseline
  identifier in the Execution Log only after the spec edit exists.
- Promotion baseline identifier:
  `8fdb54f27371f3c02a3d4a937368b8bc4bf30663 + reviewed specification
  worktree delta 2026-08-10`.

## Boundary Inventory

Formatting is by semantic owner, never by spelling or numeric magnitude.

| External surface | Exact field(s) | Semantic source | Projection owner | Closest test owner |
|---|---|---|---|---|
| Delegated raw queue JSON | `timestamp`; broker status `last_timestamp` | SimpleBroker row/high-water IDs | SimpleBroker v7 `commands`/watcher | `tests/cli/test_cli_queue.py` |
| Weft bounded queue move/watch JSON | `timestamp` | SimpleBroker row ID | `weft/commands/queue.py` | `tests/commands/test_queue.py`, `tests/cli/test_cli_queue.py` |
| SimpleBroker dump v1 | header `last_ts`; message `id` | Broker high-water/row ID | upstream `dump_lines()`; Weft `_load_support.py` when rebuilding filtered apply lines | `tests/commands/test_dump_load.py`, `tests/commands/test_dump_load_sqlite_only.py` |
| Project status JSON | `broker.last_timestamp`; manager fields below; `services[].updated_at`; task fields below | Broker high-water and registry/task-log row IDs | `weft/commands/system.py`, shared explicit projections | `tests/commands/test_status.py`, `tests/cli/test_cli_system.py` |
| Task list/status JSON | `last_timestamp`, except wall-clock reconciliation cases below; broker-backed `reconciliation.observed_at` | Task-log, ctrl-out, outbox, or PONG row ID | shared task-snapshot JSON projection used by `weft/commands/system.py` and `weft/cli/app.py` | `tests/cli/test_cli_list_task.py`, `tests/commands/test_status.py`, `tests/commands/test_task_evidence.py` |
| Task ping JSON | top-level `observed_at` only | matched ctrl-out row ID | `weft/cli/app.py`; `task_ping()` Python result remains integer | `tests/cli/test_cli_list_task.py`, `tests/commands/test_task_commands.py` |
| Status watch JSON | event `timestamp` | task-log row ID | `weft/commands/system.py` | `tests/commands/test_status.py` |
| Manager list/status JSON | top-level `timestamp`, `_pong_live_at`, and `metadata.supersession_observed_timestamp` when present | registry row, matched PONG row, and observed competing registry row | one explicit manager-record JSON projection in `weft/commands/manager.py`, reused by status | `tests/commands/test_manager_commands.py`, `tests/cli/test_cli_manager.py` |
| Verbose manager-start JSON | `timestamp` | manager registry row ID | `weft/commands/run.py` | `tests/commands/test_run.py`, `tests/cli/test_cli_run.py` |
| Foreground manager operational JSONL | top-level `message_timestamp`, `observed_timestamp`, `superseded_message_id` when present | spawn/reserved row, observed registry row, published superseding row | exact top-level field whitelist in `weft/core/serve_log.py` | `tests/core/test_serve_log.py` |
| Foreground task-monitor JSONL/JSON | `last_task_log_timestamp`, `checkpoint_timestamp`, and conditionally broker-backed `reconciliation.observed_at` | task-log row/checkpoint or reconciled ctrl-out/outbox/PONG row ID | `weft/commands/task_monitor.py` external record/summary builders | `tests/commands/test_task_monitor.py` |
| Monitor raw/collated external JSONL | raw `message_id`; task `first_message_id`, `last_message_id`, nullable `terminal_message_id` | task-log row IDs | `weft/core/monitor/external_log.py` plus the explicit task-summary projection used by `task_monitor.py` | `tests/core/test_monitor_external_log.py`, `tests/tasks/test_task_monitor.py` |
| Monitor lifetime external JSONL/deferred handoff | `subject.message_id`; `monitor.message_id`, `first_message_id`, `last_message_id`, nullable `terminal_message_id`; owned `observations.message_id`/`message_ids` when present | exact cleanup/collation row IDs | explicit non-mutating external projection in `weft/core/monitor/lifetime_report.py`, invoked before both direct and deferred handoff | `tests/core/monitor/test_lifetime_report.py`, `tests/tasks/test_task_monitor.py` |
| Monitor table JSON text | checkpoint meta `value_json.message_id`; collation `lifecycle_json.message_id`, nested pipeline `lifecycle_json.checkpoint.message_id`, and `bookkeeping_json.last_message_id`; deferred `body_json.subject.message_id`, `monitor.message_id`/`first_message_id`/`last_message_id`/nullable `terminal_message_id`, `observations.message_id`, and every `observations.message_ids[]` | exact task-log, work/reserved, pipeline checkpoint, and lifetime-report broker IDs | explicit write/read projection in `weft/core/monitor/store.py` and `weft/core/monitor/lifetime_report.py`; raw deferred JSON remains canonical for flush while `body()` returns integer-domain values; relational ID columns stay integer | `tests/core/test_monitor_store.py` |
| Runtime/retention prune JSONL | candidate `message_id` | exact prune row ID | `weft/core/pruning/runtime.py`, `weft/core/pruning/retention.py` | `tests/commands/test_runtime_prune.py`, `tests/commands/test_retention_prune.py` |

Task reconciliation is intentionally conditional:

- `reconciliation.observed_at` and the corresponding snapshot
  `last_timestamp` are broker-domain for `terminal_ctrl_out`, `wrapper_lost`,
  `result_without_terminal`, and `live_pong`; format them when present.
  `terminal_monitor_store` makes `last_timestamp`, and any future
  `reconciliation.observed_at` when present, broker-domain.
- `claimed_result_without_terminal`, `stale_created`, and `stale_liveness`
  `reconciliation.observed_at` values are generated by `time.time_ns()` and
  stay JSON numbers.
- Classify snapshot `last_timestamp` independently. Ordinary task-log replay,
  including runtime conflicts, stale status, stale liveness, superseded
  manager/internal-service, and runtime-missing reconciliations, retains a
  task-log row ID and is formatted. `terminal_monitor_store` retains a Monitor
  relational message ID and is formatted. A `last_timestamp` sourced from
  claimed-result/stale-created evidence, a pipeline-status body, or a terminal
  control-envelope body (`ctrl_out_terminal`) is a Unix clock and stays
  numeric.

### Values that remain integers or retain their current type

- Public Python snapshots, dataclasses, command/client return values, and
  queue-entry timestamps.
- SimpleBroker, Monitor, PostgreSQL, and SQLite relational columns and
  comparisons.
- TIDs. They are already decimal strings in TaskSpec and public task identity
  surfaces; this change does not reinterpret them.
- Counts, PIDs, schema versions, exit codes, byte sizes, durations, and Unix
  clock measurements such as `emitted_at_ns`, `started_at`, `completed_at`,
  `updated_at_ns`, manager operational `timestamp_ns`, PONG-body `timestamp`,
  and the wall-clock reconciliation cases named above.
- Stored queue bodies and internal process/control/work JSON, including task
  state-change `message_id`, Monitor checkpoint/result and PONG summaries, and
  pipeline event bodies. JSON syntax alone does not make an internal stored or
  transported value an external contract. The exception is the narrow set of
  Monitor-owned exact-ID fields stored in table JSON named in the inventory;
  those are canonical strings at rest and normalize back to integers on read.
- `_service_owner_payload`, raw PONG bodies, other manager metadata, opaque
  payloads, TaskSpec dictionaries, diagnostics, runner handles, and
  application extension data are never traversed or rewritten by key name.
- Human output remains decimal text with its current labels and ordering.

## Invariants and Constraints

- Every non-null external broker message ID is exactly 19 ASCII digits and is
  produced by `simplebroker.format_message_id`; null stays null.
- Conversion is explicit at each owned field. Do not add a recursive walker,
  heuristic based on key names or magnitude, custom JSON encoder, private
  SimpleBroker import, or duplicate formatter implementation.
- Define one Weft exact-input normalizer in a narrow shared helper. It calls
  `format_message_id(value)`, requires string input to equal that canonical
  result, and then returns `int(canonical)`. Exact Python/client message-ID
  inputs and dump v1 `id`/`last_ts` use it immediately at ingress.
- Exact string inputs therefore reject padded whitespace, non-ASCII decimal
  digits, wrong length, and other noncanonical spellings. All inputs reject
  booleans, floats/exponent forms, negatives, and values outside the broker
  range. Dump message ID `0` remains reserved and is rejected; header
  `last_ts=0` is valid and becomes `"0000000000000000000"` on output.
- `simplebroker-dump` stays version 1. Writers emit canonical strings. Readers
  accept exact JSON integer tokens or canonical strings and normalize them to
  integers. This is representation tolerance, not a legacy-version subsystem.
- Lifetime `report_id` identity is representation-independent. Compute it from
  the existing internal integer report, then produce a non-mutating external
  copy; do not change durable dedup identity for a formatting-only migration.
- No dependency is added. Core 7.0.0 and PG 3.5.2 move together because the PG
  release is paired with the core major.
- Existing queue delivery, ID ordering, TID immutability, reserved-queue
  policy, public field names, CLI exit classes, and backend behavior do not
  change.
- Stop and re-plan if a required fix changes a public Python return type,
  database column, broker body, internal IPC field, report identity, or
  requires a private upstream API.

## Proposed Spec Delta

The following is exact proposed markdown, not a summary.

### `04-SimpleBroker_Integration.md`

After the opening paragraph of [SB-0.1], insert:

> Weft requires SimpleBroker 7.0.0 or newer. Installations using the optional
> PostgreSQL backend require `simplebroker-pg` 3.5.2 or newer. These paired
> floors provide the supported public message-ID formatter and matching backend
> contract.

After the reserved-ID paragraph in [SB-0.2], insert:

> SimpleBroker message IDs remain integers in Python, broker/backend columns,
> Monitor relational message-ID columns, and internal process, control, and
> work protocols. At an external JSON boundary, or in a Weft-owned exact-ID
> field embedded in JSON text stored in a Monitor table, every non-null exact
> SimpleBroker message ID is a 19-character ASCII decimal string produced by
> the public `simplebroker.format_message_id` helper; null remains null.
> Monitor readers immediately normalize those owned stored strings back to
> integers. Counts, PIDs, TIDs, Unix-clock measurements, and opaque or internal
> JSON retain their owned types. Formatting is explicit by field and semantic
> source; Weft does not install a generic encoder or traverse arbitrary
> mappings by key name.
>
> Weft-owned exact-ID input boundaries accept either an integer or the canonical
> 19-character string. They validate with the supported formatter, require a
> string input to equal the formatter's canonical result, and immediately
> normalize the accepted value to `int`. Range cursors retain their existing
> contracts.

After the [SB-0.3] implementation mapping, insert:

> Structured JSON stored inside broker messages is internal data, not an
> external JSON formatting boundary. Task/control/PONG bodies, TaskSpecs,
> manager service-owner payloads, pipeline events, diagnostics, and extension
> payloads are not recursively rewritten. Only the explicitly owned external
> fields named by the CLI and state-observation specs, plus the exact Monitor
> table JSON-at-rest fields named in [SB-0.4a], use [SB-0.2] formatting.

After the first paragraph of [SB-0.4a], insert:

> Monitor relational message-ID and checkpoint columns remain integers. Exact
> broker IDs embedded in Monitor-owned JSON text are canonical strings at rest:
> checkpoint metadata `value_json.message_id`, collation
> `lifecycle_json.message_id`, nested pipeline
> `lifecycle_json.checkpoint.message_id`, collation
> `bookkeeping_json.last_message_id`, deferred
> `body_json.subject.message_id`, `body_json.monitor.message_id`,
> `body_json.monitor.first_message_id`, `body_json.monitor.last_message_id`,
> nullable `body_json.monitor.terminal_message_id`,
> `body_json.observations.message_id`, and every
> `body_json.observations.message_ids[]`. Reads normalize those exact fields back
> to integers before they enter Python domain objects. Other task, state,
> resource, diagnostic, and extension mappings remain opaque and are not
> traversed. External Monitor JSONL is projected at the final write or
> durable-deferred handoff boundary, and deterministic lifetime-report identity
> is computed before that projection.

Replace the two Operational Notes bullets beginning “Weft dump/load uses” and
“Dump message records must carry” with:

> - Weft dump/load uses SimpleBroker `simplebroker-dump` v1 NDJSON and preserves
>   included broker message IDs through SimpleBroker's public import path. The
>   header `last_ts` and message `id` fields are canonical 19-character strings
>   when written. Those message IDs are task IDs for spawn requests, so an
>   import path that cannot perform exact-ID import must fail before writes
>   begin rather than silently allocate new message IDs.
> - Load accepts a canonical string or an exact JSON integer token for header
>   `last_ts` and message `id`, validates and immediately normalizes either form
>   to an internal integer, and rebuilds canonical string records for
>   SimpleBroker apply. Message ID `0` is rejected during validation before
>   aliases or messages are written; header `last_ts=0` remains valid. Dump
>   version 1 is unchanged and no compatibility-writer branch is added.

### `10-CLI_Interface.md`

After the `--verbose` bullet in [CLI-1.1.1], insert:

> - verbose manager-start JSON formats its registry-row `timestamp` under the
>   [SB-0.2] external string rule; the Python manager record remains integer

After the manager operational-log paragraph in [CLI-1.1.2], insert:

> Foreground manager operational JSONL formats only its owned broker-ID fields
> `message_timestamp`, `observed_timestamp`, and `superseded_message_id` as
> [SB-0.2] strings when present. Its event clock `timestamp_ns`, counts, PIDs,
> TIDs, and all other diagnostic fields retain their current types.

After “can emit JSON” in [CLI-1.2.1], insert these bullets:

> - external status JSON uses [SB-0.2] strings for
>   `broker.last_timestamp`, manager `timestamp`/`_pong_live_at` and owned
>   `metadata.supersession_observed_timestamp`, service `updated_at`, status
>   watch `timestamp`, and broker-backed task `last_timestamp` or
>   `reconciliation.observed_at`
> - task reconciliation classifications `terminal_ctrl_out`, `wrapper_lost`,
>   `result_without_terminal`, and `live_pong` carry broker-backed
>   `observed_at` when present. `claimed_result_without_terminal`,
>   `stale_created`, and `stale_liveness` observations are Unix clocks and stay
>   JSON numbers
> - task `last_timestamp` is independently classified by its source. Ordinary
>   task-log replay, including `runtime_conflict`, `stale_status_payload`,
>   `stale_liveness`, superseded manager/internal-service, and runtime-missing
>   reconciliations, retains a task-log row ID and uses [SB-0.2].
>   `terminal_monitor_store` retains a Monitor relational message ID and also
>   uses [SB-0.2]. A timestamp sourced from
>   `claimed_result_without_terminal`, `stale_created`, a pipeline-status body,
>   or a terminal control-envelope body (`ctrl_out_terminal`) is a Unix clock
>   and stays a JSON number
> - the same task projection is used by project status, `task list --json`, and
>   `task status --json`; public Python snapshots retain integer values

After the `weft task ping TID` bullet in [CLI-1.2], insert:

> - task-ping JSON formats the top-level matched-row `observed_at` as an
>   [SB-0.2] string; the nested PONG body, including its Unix-clock
>   `timestamp`, is opaque and unchanged, and the Python command result keeps
>   `observed_at` as `int | None`

After the manager-list diagnostic paragraph in [CLI-1.3], insert:

> Manager list/status JSON uses the same explicit manager-record projection as
> project status: top-level registry `timestamp`, matched-row `_pong_live_at`,
> and owned `metadata.supersession_observed_timestamp` follow [SB-0.2]. Other
> metadata, including `_service_owner_payload`, is opaque and unchanged.

After “command-local JSON output follows” in [CLI-4], insert:

> Raw delegated queue JSON follows SimpleBroker 7 directly. Weft-owned bounded
> move/watch JSON formats only the broker-row `timestamp` with
> `simplebroker.format_message_id`. Exact Python/client `message_id` arguments
> accept `int | str`, normalize immediately through [SB-0.2], and pass only an
> integer to queue operations. Human queue output and Python `QueueEntry`
> timestamps remain unchanged.

Replace the [CLI-6] bullet beginning “dump message records must carry” with:

> - dump header `last_ts` and message `id` use the [SB-0.2] canonical string
>   representation when written. `system load` accepts canonical strings or
>   exact JSON integer tokens and immediately normalizes them to integers;
>   message ID `0` is rejected before any write, including dry-run validation,
>   while header `last_ts=0` is valid

After the task-monitor JSON bullets in [CLI-6], insert:

> - foreground task-monitor external JSONL and its disk-mode JSON command
>   summary format task-log `last_task_log_timestamp` and
>   `checkpoint_timestamp` with [SB-0.2]. Task summaries also format
>   `reconciliation.observed_at` for `terminal_ctrl_out`, `wrapper_lost`,
>   `result_without_terminal`, and `live_pong`; wall-clock reconciliation stays
>   numeric. The checkpoint file and Python `TaskMonitorResult` remain
>   integer-valued
> - runtime/retention prune candidate JSONL and archives format candidate
>   `message_id` with [SB-0.2]; counts, ages, run clocks, and human output remain
>   numeric/current

### `05-Message_Flow_and_State.md`

After the foreground manager operational-log bullet in [MF-5], insert:

> - external JSONL is an [SB-0.2] boundary only for the manager-owned
>   `message_timestamp`, `observed_timestamp`, and `superseded_message_id`
>   fields. Operational `timestamp_ns` is a Unix-clock measurement and remains
>   numeric; arbitrary diagnostic mappings are not traversed.

After the foreground task-monitor/checkpoint bullets in [MF-5], insert:

> - task-log row/checkpoint IDs remain integers during scanning, reduction,
>   foreground checkpoint-file persistence, and Python result construction.
>   Monitor-table checkpoint JSON follows the explicit string-at-rest rule
>   below. External task-monitor JSONL and command JSON project
>   `last_task_log_timestamp`/`checkpoint_timestamp` to [SB-0.2] strings.
>   Task-summary `reconciliation.observed_at` follows the same conditional
>   broker-ID versus wall-clock rule as [CLI-1.2.1].

After the supervised TaskMonitor durable-collation paragraph in [MF-5], insert:

> Relational message-ID columns remain integers. Monitor-owned exact broker IDs
> embedded in table JSON text use [SB-0.2] strings at rest: checkpoint metadata
> `value_json.message_id`, collation `lifecycle_json.message_id`, nested
> pipeline `lifecycle_json.checkpoint.message_id`, collation
> `bookkeeping_json.last_message_id`, and the owned fields of deferred external
> `body_json.subject.message_id`, `body_json.monitor.message_id`,
> `body_json.monitor.first_message_id`, `body_json.monitor.last_message_id`,
> nullable `body_json.monitor.terminal_message_id`,
> `body_json.observations.message_id`, and every
> `body_json.observations.message_ids[]`. Store reads immediately restore these
> fields to integers in Python. Other TaskSpec, state, resource, diagnostic,
> and extension mappings are opaque and are not recursively rewritten.

After the lifetime-report paragraph ending with downstream deduplication by
`report_id`, insert:

> External Monitor raw records format `message_id`; collated summaries format
> `first_message_id`, `last_message_id`, and nullable
> `terminal_message_id`; lifetime reports format owned `subject`, `monitor`,
> and observation message-ID fields according to [SB-0.2]. Builders, Monitor
> relational message-ID columns, checkpoint/result objects, PONG/processor
> summaries, and cleanup candidates remain integer-valued; the owned Monitor
> JSON-at-rest fields named above are strings and normalize back to integers on
> read. Lifetime `report_id` is computed from the internal integer report
> before a non-mutating external projection, so representation alone does not
> change durable dedup identity. A deferred external write stores that
> projected record so later flush emits the same canonical JSON shape.

## Tasks

1. Independent plan review and correction.
   - Reviewer receives this plan, cited specs/runbooks, upstream v7 source,
     Taut precedent, and all boundary owners in the inventory.
   - Done only when every blocking finding is dispositioned and a corrective
     pass returns explicit `PASS`.

2. Promote the reviewed spec requirements before implementation.
   - Apply the exact text above at the named anchors, add this plan to each
     touched spec's `Related Plans`, and record the promotion baseline in this
     plan's Execution Log.
   - Add reciprocal implementation mappings with each later code slice; the
     final documentation task reconciles them rather than delaying promotion.

3. RED to GREEN: dependency floors and locks.
   - Strengthen dependency-floor tests first and observe failure against
     `simplebroker>=6.0.2` / `simplebroker-pg>=3.5.1`.
   - Raise `pyproject.toml` core and each PG extra floor, regenerate `uv.lock`,
     and inspect exact resolved versions.
   - Update maintained README/changelog claims without disturbing unrelated
     worktree changes.

4. RED to GREEN: external JSON output by vertical owner.
   - Add fixed adjacent IDs above `2**53` to queue; status/task/manager/run;
     manager serve logs; foreground task-monitor; Monitor external
     raw/collated/lifetime reports; and prune report tests.
   - Expected literals are direct constants, never computed with the
     production formatter. Prove JSON parsing preserves adjacent IDs, nullable
     IDs stay null, named wall clocks/counts/PIDs stay numeric, and opaque
     nested payloads are unchanged.
   - Apply package-root formatter calls only in the explicit projections named
     by the inventory. Keep Python objects and internal records integer-valued,
     except for the named canonical Monitor JSON-at-rest fields; normalize
     those immediately when read into Python objects.

5. RED to GREEN: exact inputs and dump/load.
   - Add the shared canonical exact-input normalizer, then broaden public
     command/client exact `message_id` inputs to `int | str` and normalize once
     before queue operations.
   - Make Weft's dump parser validate both message `id` and header `last_ts`,
     accept integer tokens or canonical strings, immediately normalize to
     integers, keep import records/report ranges integer-valued, and rebuild
     canonical v7 string lines for `load_lines()`.
   - Prove both accepted token types round-trip exactly above `2**53`; reject
     padded whitespace, non-ASCII digits, wrong length, bool, float/exponent,
     negative/out-of-range, and reserved-zero message IDs before writes.

6. Complete reciprocal traceability and verification.
   - Update changed module docstrings and spec implementation mappings, then
     reconcile plan backlinks and the plan index surgically around existing
     uncommitted user edits.
   - Run focused tests after each tracer, then full default tests, mypy, Ruff,
     doc/plan gates, lock inspection, `git diff --check`, and status review.

7. Independent completed-work review.
   - Review the exact scoped delta against promoted specs and upstream v7.
   - Reproduce and disposition every finding. The standing constraints are
     integer Python and relational storage values, canonical strings in the
     named Monitor JSON-at-rest fields, explicit projection, canonical exact
     inputs, unchanged report identity, and no opaque-payload traversal.

## Testing Plan

Use real SimpleBroker queues, dump/load functions, CLI JSON serialization, and
Monitor/manager JSONL sinks. Do not mock the formatter or compute expected
values with it. Targeted mocks are allowed only for existing I/O/fault seams
outside serialization.

The focused owner set is:

```text
./.venv/bin/python -m pytest tests/system/test_optional_extras.py -q
./.venv/bin/python -m pytest tests/commands/test_queue.py tests/cli/test_cli_queue.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_list_task.py \
  tests/commands/test_task_commands.py tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py -q
./.venv/bin/python -m pytest tests/commands/test_run.py tests/cli/test_cli_run.py -q
./.venv/bin/python -m pytest tests/core/test_serve_log.py -q
./.venv/bin/python -m pytest tests/commands/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_monitor_external_log.py \
  tests/core/monitor/test_lifetime_report.py tests/core/test_monitor_store.py -q
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py -q
./.venv/bin/python -m pytest tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py -q
```

The lifetime acceptance test must prove one internal report keeps integer ID
fields, external projection does not mutate it, projected fields are canonical
strings, and `report_id` is identical before and after projection.

Each production slice starts with a firing observable assertion and records an
intended RED failure before implementation.

## Verification and Gates

```text
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
```

Also run the repository's plan/doc gates that cover touched files, inspect
`uv.lock` for SimpleBroker 7.0.0 and SimpleBroker-PG 3.5.2, run
`git diff --check`, and report any unrun PostgreSQL or hosted-CI lane as
residual risk. Local SQLite success does not substitute for PostgreSQL
coverage.

## Rollout, Rollback, and Observable Success

Roll out the core and PG floors together. There is no operator data migration:
relational ID columns remain integers, newly written owned Monitor-table JSON
ID fields use strings, their readers accept either representation, and dump v1
readers accept both token types.
Before release, rollback is a single revert of manifests, lock, specs,
formatters, and tests. After release, writers remain string-only at external
JSON boundaries; the tolerant reader does not promise that old Weft builds can
read newly written dumps.

Success is observable when adjacent unsafe IDs remain distinct after JSON
parsing; public Python and relational storage assertions remain integer-valued;
owned Monitor-table JSON fields are strings at rest and integers after read;
both dump
input types restore exact integer IDs; noncanonical string inputs fail before
writes; wall clocks and unrelated numbers remain JSON numbers; lifetime report
identity is unchanged; and the lock resolves the requested core/PG versions.

## Out of Scope

- changing TID representation or semantics
- changing public Python snapshot/result types
- changing database or Monitor schemas
- rewriting stored queue bodies or internal control/work JSON
- a generic JavaScript-safe-number policy for unrelated application values
- a dump-version bump, compatibility writer, migration, or legacy branch
- publishing, pushing, committing, tagging, or creating a pull request

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [SB-0.2], [SB-0.4a], [MF-5] | All Monitor storage remained integer-valued; only external JSON was projected. | Relational ID columns remain integers, while explicitly owned exact IDs inside Monitor table JSON use canonical strings at rest and normalize to integers on read. | User follow-up correctly identified that JavaScript can consume Monitor JSON text too; leaving IDs numeric there would preserve the same corruption risk. | Promoted into the three governing sections during implementation. |

## Review Log

| ID | Finding | Disposition | Evidence |
|----|---------|-------------|----------|
| R1 [P1] | External inventory omitted task list/status/ping, reconciliation provenance, nested manager evidence, and manager operational JSONL. | Accepted. Added the field-by-field inventory, conditional wall-clock classifications, owners, and test owners. | Corrected Boundary Inventory and exact CLI/MF delta. |
| R2 [P1] | Formatter-only normalization does not enforce canonical ASCII string input. | Accepted. Added one named canonical exact-input normalizer with string equality and all A3 rejection probes. | Corrected invariants, input task, and test plan. |
| R3 [P1] | Strategy B contradicted delayed spec promotion. | Accepted. Changed to strategy A and made reviewed spec promotion Task 2. | Spec Baseline and Tasks. |
| R4 [P1] | Proposed spec delta was partly summary text and missed [SB-0.4a]/formal baseline. | Accepted. Added formal baseline and exact anchored markdown for every touched section; promotion identifier is deferred until promotion. | Spec Baseline and Proposed Spec Delta. |
| R5 [P1] | Focused test commands named nonexistent paths and omitted contract owners. | Accepted. Replaced the provisional list with executable discovered paths including task evidence and manager serve log. | Testing Plan. |
| R6 [P2] | Stable lifetime identity lacked a firing acceptance test. | Accepted. Required non-mutating internal/projected comparison with identical `report_id`. | Testing Plan. |
| R7 [corrective PASS] | Focused re-review of R1-R6 found no remaining blocker. | Closed. Implementation may proceed under strategy A. | Independent reviewer verified the revised inventory, canonical input rule, task order, exact spec text, test paths, and lifetime identity acceptance test. |
| R8 [P1] | The Monitor amendment left stale integer-storage wording, omitted exact JSON-at-rest paths and legacy-read/opacity tests, and returned deferred JSON strings to Python callers. | Accepted. Reconciled the plan/spec text, added the inverse deferred-report normalization, and added raw-SQL legacy integer plus opaque-lookalike probes. | Monitor store/lifetime tests and amended [SB-0.4a]/[MF-5] text. |
| R9 [P1] | The amended plan's proposed spec text still summarized, rather than exactly matching, the promoted Monitor path enumeration. | Accepted. Copied the full [SB-0.4a] and [MF-5] enumerations into the plan and qualified final-review storage wording. | Final focused independent re-review returned explicit `PASS`. |
| R10 [P1] | Task `last_timestamp` provenance was incorrectly tied to the allowlist for `reconciliation.observed_at`, leaving retained task-log IDs numeric for conflict, stale, superseded, and runtime-missing reconciliation paths. | Accepted. Classified the two fields independently, amended [CLI-1.2.1], and added actual command-path plus focused projection tests. | Status/CLI/task-monitor focused suite, mypy, Ruff, and spec gates passed after correction; focused re-review required. |
| R11 [P1] | The `ctrl_out_terminal` control-body wall-clock exception was implemented but absent from the amended spec/plan inventory and projection test. | Accepted. Added the exception to [CLI-1.2.1], the exact plan text, and the task projection acceptance case. | Focused corrective re-review returned explicit `PASS`. |

## Execution Log

| Slice | Status | Evidence |
|-------|--------|----------|
| Preflight | complete | Class 5; upstream v7/PG 3.5.2 inspected; Taut plan/implementation compared; first explicit boundary inventory completed. |
| Plan review round 1 | blocked, corrected | Independent review returned six accepted findings R1-R6; corrected plan awaits focused re-review. |
| Corrective plan review | complete | Independent focused re-review returned explicit `PASS` after verifying every R1-R6 correction. |
| Spec promotion | complete | Applied the reviewed exact requirement text and reciprocal plan backlinks to [SB-0.1]/[SB-0.2]/[SB-0.3]/[SB-0.4a]/Operational Notes, [MF-5], and [CLI-1.1.1]/[CLI-1.1.2]/[CLI-1.2.1]/[CLI-1.2]/[CLI-1.3]/[CLI-4]/[CLI-6]. Promotion baseline: `8fdb54f + reviewed specification worktree delta 2026-08-10`. |
| Dependency floor | complete | Four intended floor failures (core plus pg/all/dev) became green after `pyproject.toml` moved to 7.0.0/3.5.2. `uv lock` and `uv sync --all-extras` resolved and installed exactly SimpleBroker 7.0.0 and SimpleBroker-PG 3.5.2; all eight optional-extra tests passed. README and Unreleased changelog claims are aligned. |
| JSON output boundaries | complete | Queue/status/task/manager/run/serve/foreground-monitor/prune JSON projections are explicit and green. Monitor relational IDs remain integers; named IDs in Monitor JSON columns are canonical strings at rest and normalize to integers on read, including legacy integer JSON rows. SimpleBroker 7's watcher-owned signal field is explicitly reset in TaskMonitor worker snapshots. |
| Input and dump/load boundary | complete | Public exact queue IDs accept `int | str` and normalize before queue calls. Dump/load accepts integer or canonical strings, rejects noncanonical/reserved forms before writes, keeps import objects integer-valued, and rebuilds canonical apply lines; 55 dump/load tests passed. |
| Verification | complete | The migration-focused owner set and full default suite reached 100% with three expected platform/backend skips. The canonical 199-file mypy command, full Ruff check, all spec/plan tests, lock inspection, and `git diff --check` passed. The lock resolves registry releases SimpleBroker 7.0.0 and SimpleBroker-PG 3.5.2. A PostgreSQL lane was not attributed to this task and remains a reported residual. |
| Final review | complete | Independent completed-work review found R10 and R11; both were corrected with firing command/projection tests, synchronized spec text, and focused re-reviews returning explicit `PASS`. No blockers remain. |
