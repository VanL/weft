# CLI Interface Specification

## Overview [CLI-0]

The Weft CLI is the main operator surface for task submission, inspection,
control, queue access, and maintenance.

The design intent is conservative:

- keep the core verbs small
- route real work through the manager and broker
- make current behavior explicit
- avoid CLI sugar that hides which project or broker target is in play

_Implementation mapping_: `weft/cli.py` (command registration),
`weft/commands/run.py`, `weft/commands/status.py`, `weft/commands/result.py`,
`weft/commands/queue.py`, `weft/commands/manager.py`,
`weft/commands/tasks.py`, `weft/commands/specs.py`,
`weft/commands/init.py`, `weft/commands/dump.py`,
`weft/commands/load.py`, `weft/commands/tidy.py`,
`weft/commands/validate_taskspec.py`.

See also:

- planned companion:
  [`10A-CLI_Interface_Planned.md`](10A-CLI_Interface_Planned.md)
- current CLI-to-code ownership map:
  [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)

## Design Principles [CLI-0.1]

1. **Zero-surprise defaults**: current commands should behave predictably from a
   project root or nested directory.
2. **Stable verbs**: new capability should prefer existing verbs over a second
   workflow language.
3. **Pipe-friendly IO**: commands should compose with normal shell tools.
4. **Backend-neutral targeting**: CLI surfaces should speak in terms of project
   context and broker targets, not SQLite-only file assumptions.
5. **Thin command handlers**: commands should stay close to the runtime and
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

_Implementation mapping_: `weft/cli.py` `app` and `main()` callback.

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

_Implementation mapping_: `weft/commands/run.py` `cmd_run()` and helpers;
manager bootstrap in `weft/commands/_manager_bootstrap.py`.

Current behavior:

1. build a TaskSpec template from the CLI input
2. validate locally so obvious user errors fail fast
3. enqueue the request on `weft.spawn.requests`
4. use the spawn-request message ID as the task TID
5. optionally wait for completion using task-local queues and task-log events

Current execution targets:

- inline command
- `--function MODULE:FUNC`
- `--spec FILE`
- `--pipeline NAME|PATH`

Current options:

- `--spec FILE`
- `--pipeline NAME|PATH`
- `--input VALUE`
- `--function MODULE:FUNC`
- `--arg VALUE`
- `--kw KEY=VALUE`
- `--timeout SECONDS`
- `--memory MB`
- `--cpu PERCENT`
- `--env KEY=VALUE`
- `--wait` / `--no-wait`
- `--interactive`
- `--autostart` / `--no-autostart`

Current rules:

- `--spec`, `--pipeline`, and `--function` are mutually exclusive
- `--spec FILE` always means an explicit TaskSpec JSON path
- stored pipeline names are resolved only under `--pipeline`
- inline command and function runs synthesize a TaskSpec using the default
  `host` runner unless the spec itself says otherwise
- agent execution is currently available through `--spec FILE` with
  `spec.type="agent"`

Current stdin behavior:

- non-TTY stdin is read once as initial task input
- inline command targets receive piped stdin as command stdin
- inline function targets receive piped stdin as the initial work item
- pipeline runs use piped stdin as first-stage input when `--input` is absent

Current interactive behavior:

- `--interactive` is queue-mediated line IO, not PTY emulation
- follow-up input goes through `T{tid}.inbox`
- stdout goes through `T{tid}.outbox`
- stderr, status, and terminal control replies go through `T{tid}.ctrl_out`

### `manager serve` - Run the manager in the foreground [CLI-1.1.2]

_Implementation mapping_: `weft/commands/serve.py` `serve_command()`,
registered in `weft/cli.py` as `weft manager serve`.

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
the shared bootstrap helper, reports success only after matching pid-plus-registry
readiness is observed and the detached launcher can still acknowledge success,
and surfaces early detached-start diagnostics on failure.

### `init` - Initialize a project

_Implementation mapping_: `weft/commands/init.py` `cmd_init()`, registered in
`weft/cli.py` as `weft init`.

Current behavior:

- `weft init` initializes the current directory
- `weft init PATH` initializes another directory explicitly
- `init` does not take `--context`
- `init` materializes `.weft/` and broker-facing project state for the selected
  root

This is intentionally git-like. `init` chooses or creates the project root;
other commands operate within an existing root.

## Inspection Commands [CLI-1.2]

### `status` - Show project status [CLI-1.2.1]

_Implementation mapping_: `weft/commands/status.py` `cmd_status()`, registered
in `weft/cli.py` as `weft status`.

Current behavior:

- summarizes the active context
- shows manager registry information and task snapshots
- can emit JSON
- uses task-log replay plus manager/task registry queues rather than a separate
  state database

`weft status` is the project-wide summary command. More specific inspection
surfaces live under `weft task ...` and `weft result`.

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
- `--stream` is single-task only and cannot be combined with `--all` or
  `--json`

### `task list`, `task status`, and `task tid` [CLI-1.2]

_Implementation mapping_: `weft/commands/tasks.py`.

Current behavior:

- `weft task list` lists task snapshots and can filter by status or emit JSON
- `weft task status TID` shows one task, optionally with process information
- `weft task tid` resolves short TIDs or reverse lookups via the TID-mapping
  queue

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
TID/PID is present while the detached launcher is still healthy enough to
acknowledge success. `weft manager serve`
remains the foreground supervisor path and is not interchangeable with
`manager start`.

Pattern-based task stop/kill reuse queue broadcast and control messages rather
than inventing a second control channel.

## Spec Management (`weft spec …`) [CLI-1.4]

Task specs and pipeline specs share one namespace so authoring stays
discoverable without adding more top-level verbs.

_Implementation mapping_: `weft/commands/specs.py` (`create_spec`, `list_specs`,
`load_spec`, `delete_spec`, `validate_spec`, `generate_spec()`), registered in
`weft/cli.py` under the `spec` sub-app.

Current subcommands:

- `weft spec create`
- `weft spec list`
- `weft spec show`
- `weft spec delete`
- `weft spec validate`
- `weft spec generate`

Current rules:

- task specs run through `weft run --spec FILE`
- stored pipeline specs run through `weft run --pipeline NAME|PATH`
- `weft spec list` can show both tasks and pipelines
- `--type` filters or disambiguates task vs pipeline names

### `spec validate` - Validate a task or pipeline spec [CLI-1.4.1]

_Implementation mapping_: `weft/commands/specs.py` `validate_spec()`;
task-spec runner validation reuses `weft/commands/validate_taskspec.py`
`cmd_validate_taskspec()` and `weft/core/runner_validation.py`.

Current validation layers:

- default validation checks the schema only
- `--load-runner` requires that the named runner plugin can be resolved
- `--preflight` runs runner-specific availability checks and implies
  `--load-runner`

## Queue Operations [CLI-4]

_Implementation mapping_: `weft/commands/queue.py`, registered in `weft/cli.py`
under the `queue` sub-app.

Current queue subcommands:

- `read`
- `write`
- `peek`
- `move`
- `list`
- `watch`
- `delete`
- `broadcast`
- `alias add`
- `alias remove`
- `alias list`

These commands intentionally stay close to SimpleBroker behavior. Weft adds
project resolution, aliases, and task/runtime conventions on top.

## Configuration [CLI-5]

_Implementation mapping_: `weft/_constants.py` `load_config()`,
`weft/context.py` `build_context()`.

Current configuration sources:

- environment variables
- `.simplebroker.toml`
- Weft project metadata under `.weft/`

The CLI should not imply that runtime broker configuration lives in a
SQLite-only `.weft/broker.db` flag model. That is why the current contract uses
context discovery plus backend-aware broker resolution.

## System Maintenance (`weft system …`) [CLI-6]

_Implementation mapping_: `weft/commands/tidy.py` `cmd_tidy()`,
`weft/commands/dump.py` `cmd_dump()`, `weft/commands/load.py` `cmd_load()`,
registered in `weft/cli.py` under the `system` sub-app.

Current subcommands:

- `weft system tidy`
- `weft system dump`
- `weft system load`

Current behavior:

- `system tidy` delegates maintenance/compaction to the active backend
- `system dump` exports broker state while excluding runtime-only
  `weft.state.*` queues
- `system load` imports a dump and returns exit code `3` on alias conflicts
  before writes begin
- file-backed sqlite contexts can use snapshot rollback on apply failure
- non-file-backed backends report partial-apply risk if a failure happens after
  writes begin

## Scope Boundary

Planned CLI follow-ups such as richer result streaming, additional convenience
flags, and future queue or control ergonomics live in the companion doc:

- [`10A-CLI_Interface_Planned.md`](10A-CLI_Interface_Planned.md)

## Related Plans

- [`docs/plans/result-stream-implementation-plan.md`](../plans/result-stream-implementation-plan.md)

## Related Documents

- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
