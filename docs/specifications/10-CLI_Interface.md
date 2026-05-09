# CLI Interface Specification

## Overview [CLI-0]

The Weft CLI is the main operator surface for task submission, inspection,
control, queue access, and maintenance.

The design intent is conservative:

- keep the core verbs small
- route real work through the manager and broker
- make current behavior explicit
- avoid CLI sugar that hides which project or broker target is in play

_Implementation mapping_: `weft/cli/app.py` (command registration),
`weft/cli/run.py`, `weft/commands/status.py`, `weft/commands/result.py`,
`weft/commands/queue.py`, `weft/commands/manager.py`, `weft/commands/serve.py`,
`weft/commands/tasks.py`, `weft/commands/specs.py`,
`weft/commands/builtins.py`, `weft/commands/init.py`, `weft/commands/dump.py`,
`weft/commands/load.py`, `weft/commands/tidy.py`,
`weft/commands/validate_taskspec.py`.

See also:

- planned companion:
  [`10A-CLI_Interface_Planned.md`](10A-CLI_Interface_Planned.md)
- current CLI-to-code ownership map:
  [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- implementation plan:
  [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

## Design Principles [CLI-0.1]

1. **Zero-surprise defaults**: current commands should behave predictably from a
   project root or nested directory.
2. **Stable verbs**: new capability should prefer existing verbs over a second
   workflow language.
3. **Pipe-friendly IO**: commands should compose with normal shell tools.
4. **Backend-neutral targeting**: CLI surfaces should speak in terms of project
   context and broker targets, not SQLite-only file assumptions.
5. **Substrate, not orchestration**: the CLI should help operators submit,
   inspect, control, and explicitly validate tasks without turning `weft run`
   into a higher-level agent-management surface.
6. **Thin command handlers**: commands should stay close to the runtime and
   broker semantics they expose.

## Command Structure [CLI-0.2]

```text
weft <command> [options]
weft manager <subcommand> [options]
weft task <subcommand> [options]
weft spec <subcommand> [options]
weft queue <subcommand> [options]
weft system <subcommand> [options]
```

## Global Options [CLI-0.3]

The current root-level global options are intentionally small.

_Implementation mapping_: `weft/cli/app.py` `app` and callback helpers.

| Option | Current behavior |
| --- | --- |
| `--version` | Show version information |
| `--help` | Show root help |
| `--install-completion` | Install shell completion |
| `--show-completion` | Print shell completion script |

Removed or superseded surfaces:

- global `--dir` / `--file` are not part of the current CLI. They were removed
  as the system moved to backend-neutral broker targeting.
- project-aware commands use per-command `--context` instead.
- maintenance actions such as cleanup or compaction live under `weft system`,
  not as root-level one-off flags.

## Submission and Bootstrap [CLI-1.1]

### `run` - Execute a task [CLI-1.1.1]

`weft run` is the center of gravity for execution. It always routes work
through the manager path rather than bypassing the runtime.

_Implementation mapping_: `weft/commands/run.py` owns shared `weft run`
execution helpers and the structured `execute_run()` result surface;
`weft/cli/run.py` is the Typer adapter and owns rendering that structured
result to stdout/stderr and exit codes; shared submission lives in
`weft/commands/submission.py`; manager bootstrap lives in
`weft/core/manager_runtime.py` and is surfaced through
`weft/commands/manager.py`.

The command-layer `cmd_run()` wrapper remains as a compatibility renderer for
tests and non-Typer callers, but new adapter work should prefer
`execute_run()` plus `render_run_execution_result()`. Interactive prompt mode
still has presentation callbacks in the command layer because the prompt loop
is not yet a public `WeftClient.run()` API.

Current behavior:

1. build a TaskSpec template from the CLI input
2. validate the TaskSpec template and CLI-level invariants locally so obvious
   user errors fail fast
3. enqueue the request on `weft.spawn.requests`
4. use the spawn-request message ID as the task TID
5. reconcile post-enqueue startup failures by submitted TID instead of assuming
   the enqueue can always be rolled back
6. optionally wait for completion using task-local queues and task-log events,
   reusing broker-native queue waiting for those queue-backed boundaries when
   available

Current execution targets:

- inline command
- `--function MODULE:FUNC`
- `--spec NAME|PATH`
- `--pipeline NAME|PATH`

Current options:

- `--spec NAME|PATH`
- `--pipeline NAME|PATH`
- `--input VALUE`
- `--function MODULE:FUNC`
- `--name TEXT`
- `--arg VALUE`
- `--kw KEY=VALUE`
- `--timeout SECONDS`
- `--memory MB`
- `--cpu PERCENT`
- `--env KEY=VALUE`
- `--context PATH`
- `--json`
- `--verbose` / `-v`
- `--stream-output` / `--no-stream-output`
- `--wait` / `--no-wait`
- `--interactive`
- `--continuous` / `--once`
- `--autostart` / `--no-autostart`
- hidden `--monitor` exists in the parser, but it is currently unsupported and
  always errors

Current `--name` behavior:

- `--name` always sets the public task name used by task/status/process-title
  surfaces
- inline command, inline function, `--spec`, and `--pipeline` runs all honor
  that explicit override
- if the resolved top-level task is persistent, an explicit `--name` also
  becomes the runtime endpoint claim name for that task
- if the resolved top-level task is not persistent, `--name` is label-only and
  does not claim a runtime endpoint
- endpoint claim on persistent runs is opt-in through explicit `--name`; Weft
  does not derive endpoint claims from stored TaskSpec names by default

Current spec-declared option support:

- when the selected TaskSpec declares `spec.parameterization`, `weft run --spec`
  also accepts that spec's declared long options such as `--provider VALUE`
- these declared parameterization options run locally first and materialize a
  concrete TaskSpec template before queueing
- when the selected TaskSpec declares `spec.run_input`, `weft run --spec` also
  accepts the spec's declared long options such as `--prompt VALUE`
- these declared options are submission-time CLI sugar only; they are resolved
  locally into the ordinary initial work payload after materialization and
  before the spawn request is queued
- specs that only need declared options copied into the initial work payload
  can use built-in adapters such as
  `weft.builtins.run_input:arguments_payload` for a flat JSON object or
  `weft.builtins.run_input:keyword_arguments_payload` for a function kwargs
  envelope; `nonempty_*` variants reject blank string values before queueing
  but do not perform domain-specific validation such as UUID or date parsing
- declared options are long-option only: `--name value` or `--name=value`
- declared option names come from identifier keys in the TaskSpec and
  normalize `_` to `-`
- declared option names cannot collide with built-in `weft run` option names
- `spec.parameterization` and `spec.run_input` cannot reuse the same public
  long option after normalization
- declared `path` arguments are resolved to absolute paths before the adapter
  receives them
- `weft run --spec NAME|PATH --help` loads the selected TaskSpec locally and
  appends spec-aware help for its declared submission-time options
- specs without `spec.parameterization` or `spec.run_input` do not accept
  extra spec-declared options

Current rules:

- `--spec`, `--pipeline`, and `--function` are mutually exclusive
- `--spec NAME|PATH` is the explicit task-spec resolution surface
- stored pipeline names are resolved only under `--pipeline`
- stored task names, stored task bundles, and builtin task helpers are resolved
  only under `--spec`
- `--arg`, `--kw`, `--env`, and `--tag` are not accepted with `--spec` or
  `--pipeline`
- `--interactive` is currently implemented for command targets only; the spec
  and pipeline paths do not route through the interactive client, and command
  interactive mode rejects `--json`
- `--continuous` / `--once` is only supported with `--spec`; it maps to a
  persistent override for that invocation
- `--monitor` is accepted by the parser, but `weft run` currently rejects it on
  every path
- when a selected TaskSpec declares `spec.parameterization`, `weft run --spec`
  materializes a concrete TaskSpec locally before queueing
- parameterization parsing may apply TaskSpec-declared defaults and preserve
  later undeclared tokens for the run-input stage
- when a selected TaskSpec declares `spec.run_input`, `weft run --spec`
  resolves declared long options and optional stdin through that adapter after
  materialization and before queueing work
- when `--help` is requested together with `--spec`, no task is queued; Weft
  loads the TaskSpec locally and renders its declared submission-time option
  surface
- run-input parsing rejects undeclared or repeated spec-owned options
- inline command and function runs synthesize a TaskSpec using the default
  `host` runner unless the spec itself says otherwise
- agent execution is currently available through `--spec NAME|PATH` with
  `spec.type="agent"`
- `weft run` does not do runner/plugin/provider preflight before queueing work
- queue-first submission is the durable contract; once step 3 succeeds, later
  errors must be reconciled against task logs, TID mappings, and exact queue
  location for that submitted TID
- only spawn requests still provably present in `weft.spawn.requests` are
  rollback-safe; requests already claimed into a manager reserved queue require
  continued observation for spawned/rejected child evidence before surfacing
  manual operator recovery
- ahead-of-time runtime checks live under
  `weft spec validate --load-runner` and `--preflight`
- if runtime startup fails, the task fails on the normal execution path with
  the concrete runner or agent error

Current stdin behavior:

- non-TTY stdin is read once as initial task input
- inline command targets receive piped stdin as command stdin
- inline function targets receive piped stdin as the initial work item
- spec runs without `spec.run_input` receive piped stdin as the initial work
  item using the existing target-specific rules
- spec runs with `spec.parameterization` materialize the concrete TaskSpec
  before stdin is routed to any later run-input adapter
- spec runs with `spec.run_input` route declared args and piped stdin through
  the adapter before queueing the initial work payload
- pipeline runs use piped stdin as first-stage input when `--input` is absent
- `--autostart` / `--no-autostart` are per-invocation context overrides:
  explicit flag, then the project-local Weft config file's `autostart`, then
  the env/global default
- if `weft run` adopts an already-live canonical manager, these flags do not
  reconfigure that live manager

Current interactive behavior:

- `--interactive` is queue-mediated line IO, not PTY emulation
- follow-up input goes through `T{tid}.inbox`
- stdout goes through `T{tid}.outbox`
- stderr, status, and terminal control replies go through `T{tid}.ctrl_out`

### `manager serve` - Run the manager in the foreground [CLI-1.1.2]

_Implementation mapping_: `weft/commands/serve.py` `serve_command()`,
registered in `weft/cli/app.py` as `weft manager serve`.

Current behavior:

- runs the canonical manager in the foreground
- forces the served manager to stay alive until explicitly stopped or until it
  yields leadership and drains
- exits with code `1` if another live canonical manager already exists for the
  same context

This exists so operators can supervise Weft under tools like `systemd`,
`launchd`, or `supervisord` without a separate runtime entrypoint.

Detached manager bootstrap for `weft run` and `weft manager start` remains a
separate contract from `manager serve`: it starts the canonical manager through
the shared bootstrap helper, reports success once matching pid-plus-registry
readiness is observed, treats detached-launcher acknowledgement as best-effort
post-proof cleanup, and surfaces early detached-start diagnostics on failure.

### `init` - Initialize a project

_Implementation mapping_: `weft/commands/init.py` `cmd_init()`, registered in
`weft/cli/app.py` as `weft init`.

Current behavior:

- `weft init` initializes the current directory
- `weft init PATH` initializes another directory explicitly
- `init` does not take `--context`
- `init` materializes the Weft metadata directory (default `.weft/`; override
  with `WEFT_DIRECTORY_NAME`) and broker-facing project state for the selected
  root
- `init --autostart/--no-autostart` persists the selected project-local
  autostart default into the project-local Weft config file

This is intentionally git-like. `init` chooses or creates the project root;
other commands operate within an existing root.

## Inspection Commands [CLI-1.2]

### `status` - Show project status [CLI-1.2.1]

_Implementation mapping_: `weft/commands/status.py` `cmd_status()`, registered
in `weft/cli/app.py` as `weft status`.

Current behavior:

- summarizes the active context
- shows manager registry information and task snapshots
- can emit JSON
- uses task-log replay plus manager/task registry queues rather than a separate
  state database
- JSON task snapshots may include an additive `reconciliation` object when
  lifecycle evidence and runtime liveness disagree; the public `status` remains
  one of the normal lifecycle states
- JSON task snapshots may also include additive reconciliation classifications
  from shared task evidence, including `wrapper_lost`, `terminal_ctrl_out`, and
  `result_without_terminal`; claimed result residue may surface as
  `claimed_result_without_terminal`, and superseded manager task rows may
  surface as `superseded_manager_record`; these classifications are
  diagnostics, not public lifecycle states
- project-wide `weft status` does not actively PING every task by default;
  keyed PING/PONG current-state probing belongs to known-task inspection paths
  or explicit command/client options
- JSON task snapshots must not report `status="running"` with `completed_at`
  set

`weft status` is the project-wide summary command. More specific inspection
surfaces live under `weft task ...` and `weft result`.

Known-TID client helpers are allowed to use a different performance path than
`weft status`: for a full TID, the command/client layer should inspect
task-local outbox, typed terminal `ctrl_out`, runtime mapping, and bounded
task-log fallback instead of replaying the global task log from the beginning.
This does not change project-wide `weft status`, which may still replay the
global log once for a global view.

Implementation plan backlinks:

- `docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`
- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`
- `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`
- `docs/plans/2026-05-06-terminal-publication-hardening-plan.md`
- `docs/plans/2026-05-07-extended-ping-pong-state-probe-plan.md`
- `docs/plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`

### `result` - Read task output [CLI-1.2]

_Implementation mapping_: `weft/commands/result.py` `cmd_result()`,
`_await_single_result()`, `_collect_all_results()`.

Current behavior:

- `weft result TID` waits for or reads the next completed result for a task
- `weft result TID --stream` follows unread outbox stream chunks for that one
  task while still using the same task-log completion and grace rules
- `weft result --all` aggregates completed outbox results from non-streaming
  tasks
- `--peek` inspects `--all` results without consuming them
- `--error` selects stderr-oriented output where available
- `--json` includes metadata
- if a known result row exists only as claimed outbox residue and no terminal
  lifecycle proof is visible, `weft result TID --json` returns promptly with
  `status="failed"`, `result=null`, `error`, and additive `reconciliation`
  metadata instead of waiting until timeout
- `--stream` is single-task only and cannot be combined with `--all` or
  `--json`

### `task list`, `task status`, and `task tid` [CLI-1.2]

_Implementation mapping_: `weft/commands/tasks.py`, `weft/commands/status.py`.

Current behavior:

- `weft task list` lists task snapshots, can include terminal tasks with
  `--all`, can filter by status, can summarize counts with `--stats`, and can
  emit JSON
- `weft task status TID` shows one task, optionally with process information,
  JSON output, explicit keyed PING/PONG current-state probing, or live watch
  updates
- `weft task status TID --json` uses the same additive `reconciliation`
  diagnostic object as `weft status --json` when runtime evidence conflicts
  with lifecycle evidence, when a typed terminal `ctrl_out` envelope proves
  terminal state, or when one-shot outbox evidence is visible without terminal
  task-log publication
- `weft task status TID --ping` sends a structured PING with a
  `request_id`, waits for the matching PONG, and may return a `live_pong`
  reconciliation classification plus best-effort runner-specific `runtime`
  details, including Docker details from `RunnerRuntimeDescription` when the
  task runs under Docker
- `weft task tid` resolves short TIDs, PID lookups, or reverse lookups via the
  TID-mapping queue
- `weft task stop` and `weft task kill` can act on one task, all active tasks,
  or a name-pattern subset

These commands exist because project-level status and task-level inspection are
different operator questions.

## Control Commands [CLI-1.3]

_Implementation mapping_: `weft/commands/tasks.py`,
`weft/commands/manager.py`.

Current task-control surfaces:

- `weft task stop TID`
- `weft task kill TID`

Current manager-control surfaces:

- `weft manager start`
- `weft manager stop`
- `weft manager list`
- `weft manager status`

`weft manager start` is the detached operator wrapper over the same canonical
bootstrap helper used by `weft run`. It returns success only after the launched
manager PID is live and the canonical registry record for the same manager
TID/PID is present. Detached-launcher acknowledgement after that proof is a
warning path, not the startup truth boundary. `weft manager serve`
remains the foreground supervisor path and is not interchangeable with
`manager start`.

Pattern-based task stop/kill reuse queue broadcast and control messages rather
than inventing a second control channel.

## Spec Management (`weft spec …`) [CLI-1.4]

Task specs and pipeline specs share one namespace so authoring stays
discoverable without adding more top-level verbs.

_Implementation mapping_: `weft/commands/specs.py` (`create_spec`, `list_specs`,
`load_spec`, `delete_spec`, `validate_spec`, `generate_spec()`), registered in
`weft/cli/app.py` under the `spec` sub-app.

Current subcommands:

- `weft spec create`
- `weft spec list`
- `weft spec show`
- `weft spec delete`
- `weft spec validate`
- `weft spec generate`

Current rules:

- task specs run through `weft run --spec NAME|PATH`
- stored pipeline specs run through `weft run --pipeline NAME|PATH`
- explicit task-spec lookup follows the same `NAME|PATH` model as pipelines:
  existing file path first, then existing spec-bundle directory path, then
  local stored flat spec, then local stored bundle, then builtin task spec
- `--type` accepts `task` / `tasks` and `pipeline` / `pipelines`
- when a task spec is loaded from a bundle directory, Python callable refs in
  that spec keep the normal `module:function` syntax but resolve `module`
  against the bundle root first before falling back to normal Python imports
- local stored task specs shadow builtin task specs of the same name
- builtin task specs are task-only; Weft does not currently ship builtin
  pipelines
- bare `weft run foo` still means "run command `foo`"; builtin lookup only
  happens under explicit spec-management or `--spec` surfaces
- `weft spec list` can show stored pipelines plus stored and builtin task specs
- `weft spec list --json` includes a `source` field for each listed spec;
  builtin entries report `source: "builtin"`
- plain `weft spec list` labels builtin task specs with `(builtin)`
- `weft spec list` is the effective project-visible spec namespace; local
  stored-task shadows under the Weft metadata directory affect this view
- builtin task specs are packaged read-only with Weft; `weft spec delete`
  rejects builtin-only task specs
- `--type` filters or disambiguates task vs pipeline names

### `spec validate` - Validate a task or pipeline spec [CLI-1.4.1]

_Implementation mapping_: `weft/commands/specs.py` `validate_spec()`;
task-spec runner validation reuses `weft/commands/validate_taskspec.py`
`cmd_validate_taskspec()`, `weft/core/runner_validation.py`, and
`weft/core/agents/validation.py`.

Current validation layers:

- default validation checks the schema only
- `--load-runner` and `--preflight` apply only to task specs; pipeline
  validation rejects them
- `--load-runner` requires that the named runner plugin can be resolved; it
  also loads and materializes the configured runner environment profile. For
  agent tasks it resolves the configured agent runtime and, for delegated
  runtimes, the configured tool profile
- `--preflight` runs runner-specific availability checks and, for agent tasks,
  environment-profile, agent-runtime, and delegated tool-profile preflight
  checks that remain static at validation time, such as provider CLI path
  resolution, project-local agent settings from the Weft agent settings file
  including current shipped `provider_cli.providers` executable defaults when
  relevant, and MCP server command resolution; it implies
  `--load-runner`
- for Docker-backed one-shot `provider_cli` agent specs, that preflight stays
  container-oriented: it validates the Docker runner path and static descriptor
  requirements, but it does not require the host provider executable to exist
  because the real provider CLI runs inside the container
- `weft spec validate` is the explicit ahead-of-time validation surface.
  `weft run` does not silently perform the same preflight work before
  submission
- `--preflight` does not prove delegated provider health, login state, or
  runtime capability. Those are checked only when Weft actually opens a
  delegated session or executes a delegated call
- provider-backed persistent agent specs are validated through the same path;
  for example `provider_cli` `conversation_scope="per_task"` requires
  `spec.persistent=true` and a provider runtime that supports its configured
  continuation surface

## Queue Operations [CLI-4]

_Implementation mapping_: `weft/commands/queue.py`, registered in `weft/cli/app.py`
under the `queue` sub-app.

Current queue subcommands:

- `read`
- `write`
- `peek`
- `move`
- `list`
- `resolve`
- `watch`
- `delete`
- `broadcast`
- `alias add`
- `alias remove`
- `alias list`

These commands intentionally stay close to SimpleBroker behavior. Weft adds
project resolution, aliases, and task/runtime conventions on top.

### Named Endpoint Queue Ergonomics [CLI-4.1]

Current endpoint helpers are intentionally thin:

- `weft queue resolve NAME` returns the canonical live endpoint record for one
  stable project-local name
- `weft queue list --endpoints` lists canonical named endpoints instead of raw
  queue inventory
- `weft queue write --endpoint NAME [MESSAGE]` resolves the current inbox for
  that name and then performs an ordinary queue write using the existing queue
  payload rules

Current rules:

- endpoint helpers remain under `weft queue`; Weft does not add a second
  service command family
- endpoint-targeted writes do not auto-register, auto-spawn, or wrap the
  payload in a Weft-wide request envelope
- payload schemas remain task-owned or builtin-owned contracts
- `queue list --endpoints` is incompatible with `--stats`
- failed resolution returns an explicit non-zero exit rather than silently
  redirecting work

_Implementation mapping_: `weft/commands/queue.py` `resolve_command()`,
`list_command()`, `write_command()`; `weft/cli/app.py` `queue_resolve()`,
`queue_list()`, `queue_write()`.

## Configuration [CLI-5]

_Implementation mapping_: `weft/_constants.py` `load_config()`,
`weft/context.py` `build_context()`.

Current configuration domains:

- environment variables for Weft defaults and broker alias translation
- Weft-scoped `broker.toml` under the configured Weft metadata directory
  (default `.weft/broker.toml`) for project-scoped broker target selection
- Weft project metadata and agent settings under the configured Weft metadata
  directory (default `.weft/`), including the optional project-local autostart
  default in its `config.json`

Current broker resolution precedence:

1. determine the project root from explicit `--context` or auto-discovery
2. explicit-root resolution uses `simplebroker.target_for_directory()`:
   the configured Weft-scoped broker config at that root, else env-selected
   non-sqlite backend, else sqlite fallback rooted at that directory
3. auto-discovery uses `simplebroker.resolve_broker_target()`:
   upward Weft-scoped broker config, then upward legacy sqlite discovery using
   the configured default DB name, then env-selected non-sqlite backend
4. if auto-discovery finds nothing, Weft falls back to explicit-root resolution
   at the current working directory

Current exclusions:

- root `.broker.toml` is a standalone SimpleBroker project config and does not
  participate in Weft's default project discovery
- the Weft config file does not participate in broker target resolution, even
  when it carries the project-local autostart default
- the Weft agent settings file does not participate in broker target resolution
- TaskSpec `metadata` does not participate in broker target resolution

The CLI should not imply that runtime broker configuration lives in a
SQLite-only metadata-directory broker-db flag model. That is why the current contract uses
context discovery plus backend-aware broker resolution.

Related plan:
- `docs/plans/2026-04-16-configurable-weft-directory-name-plan.md`

## System Maintenance (`weft system …`) [CLI-6]

_Implementation mapping_: `weft/commands/tidy.py` `cmd_tidy()`,
`weft/commands/dump.py` `cmd_dump()`, `weft/commands/load.py` `cmd_load()`,
`weft/commands/builtins.py` `cmd_system_builtins()`,
`weft/commands/task_monitor.py` `run_task_monitor()`,
`weft/commands/runtime_prune.py` `cmd_prune()`,
`weft/commands/retention_prune.py` `cmd_retention_prune()`, and the canonical
prune implementation under `weft/core/pruning/`, registered in `weft/cli/app.py`
under the `system` sub-app.

Current subcommands:

- `weft system tidy`
- `weft system dump`
- `weft system builtins`
- `weft system load`
- `weft system task-monitor`
- `weft system prune`

Current behavior:

- `system builtins` reports the shipped task-only builtin inventory Weft
  ships, independent of local stored-spec shadows in the metadata directory's
  `tasks/` namespace
- `system builtins --json` emits `type`, `name`, `description`, `category`,
  `function_target`, `supported_platforms`, `path`, and `source`
- plain `system builtins` renders the same builtin metadata as grouped text
- `system tidy` delegates maintenance/compaction to the active backend
- `system dump` exports broker state while excluding runtime-only
  `weft.state.*` queues
- `system load` imports a dump and returns exit code `3` on alias conflicts
  before writes begin
- `system task-monitor` scans `weft.log.tasks` without consuming broker
  messages and emits JSONL log records to stdout or append-only disk files
  under `.weft/logs/task-monitor/YYYY-MM-DD.jsonl`
- `system task-monitor --sink disk --json` emits a final command summary
  on stdout; `--sink stdout --json` is rejected because stdout is reserved for
  task-monitor JSONL records in stdout-sink mode
- task-monitor checkpoints under `.weft/state/task-monitor/` are
  operational cursors only and are not read by `status`, `task status`, or
  `result`
- `system prune` scans selected prune families and reports candidates. It
  defaults to `--family runtime-state` for backward compatibility and defaults
  to `--dry-run`; `--apply` is required for deletion.
- `system prune --family` accepts `runtime-state`, `task-local`, `task-log`,
  `retention`, or `all`. `retention` means task-local plus task-log.
- `system prune --queue` accepts `tid-mappings`, `managers`,
  `streaming`, `endpoints`, `pipelines`, or `all` for runtime-state pruning,
  and rejects unknown values
- `system prune --task TID` filters retention pruning to one or more task IDs
- `system prune --retention-class NAME` filters retention pruning to selected
  candidate classes
- `system prune --min-age` protects recent rows;
  `--keep-recent-per-key` keeps newest rows for grouped runtime keys and must
  be at least `1`
- `system prune --keep-recent-per-task` keeps newest lifecycle-log rows for
  each task in retention pruning and must be at least `1`
- ordinary retention `--apply` requires `--archive PATH`; archive records are
  written before exact-message deletion
- `system prune --apply --force` is an explicit human override for retention
  protections that are report-only in ordinary apply. Force does not override
  selected family/task/class scope, exact-message deletion, dry-run/apply mode,
  or backend delete failures. `--force` without `--apply` is rejected.
- `system prune --json` emits one summary object; `--report PATH`
  writes JSONL candidate records plus a final summary record
- runtime-state pruning deletes exact message IDs only and must not touch
  task-local queues, `weft.log.tasks`, spawn requests, or manager control
  queues
- retention pruning deletes exact message IDs only from selected
  `weft.log.tasks` and `T{tid}.*` task-local queues, according to the
  evidence/candidate classes in `weft/core/pruning/retention.py`
- file-backed sqlite contexts can use snapshot rollback on apply failure
- non-file-backed backends report partial-apply risk if a failure happens after
  writes begin

Implementation plan backlink:
[`2026-05-07-runtime-state-pruning-plan.md`](../plans/2026-05-07-runtime-state-pruning-plan.md).

## Scope Boundary

Planned CLI follow-ups such as richer result streaming, additional convenience
flags, and future queue or control ergonomics live in the companion doc:

- [`10A-CLI_Interface_Planned.md`](10A-CLI_Interface_Planned.md)

## Related Plans

- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](../plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md)
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](../plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](../plans/2026-05-06-task-evidence-reconciliation-model-plan.md)
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](../plans/2026-05-06-terminal-publication-hardening-plan.md)
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](../plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md)
- [`docs/plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](../plans/2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md)
- [`docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](../plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
- [`docs/plans/2026-05-09-prune-path-unification-plan.md`](../plans/2026-05-09-prune-path-unification-plan.md)
- [`docs/plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md`](../plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md)
- [`docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`](../plans/2026-04-14-spawn-request-reconciliation-plan.md)
- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](../plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](../plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
- [`docs/plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](../plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md)
- [`docs/plans/2026-04-14-system-builtins-command-plan.md`](../plans/2026-04-14-system-builtins-command-plan.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](../plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](../plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md)
- [`docs/plans/2026-04-15-spec-aware-run-help-plan.md`](../plans/2026-04-15-spec-aware-run-help-plan.md)
- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
- [`docs/plans/2026-04-16-pipeline-autostart-extension-plan.md`](../plans/2026-04-16-pipeline-autostart-extension-plan.md)

## Related Documents

- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`10B-Builtin_TaskSpecs.md`](10B-Builtin_TaskSpecs.md)
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
