# Weft Client Pythonic Surface And CLI Path Unification Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Redesign the public `weft.client` Python surface so that it (a) shares code
paths with the CLI wherever possible and (b) reads as a pythonic peer to the
CLI — noun-first where the CLI is noun-first, verb-flat on the hot path, with
a `Task` handle that replaces today's TID-juggling. Amend
`docs/specifications/13C-Using_Weft_With_Django.md` §DJ-2.1, §DJ-2.2, §DJ-8.2,
and §DJ-10.1 to match. Migrate `weft_django` to the new surface in the same
landing.

The current `weft.client` is a flat bag of verbs that only covers submit,
observe, and control. It depends on six underscore-prefixed private CLI
modules, duplicates `_ensure_manager_after_submission` and `_delete_spawn_request`
with slightly different semantics from `weft/commands/run.py`, and uses a
different result-wait path from `weft run --wait`. The Django integration has
had to invent a `WeftSubmission` handle to paper over the missing ergonomics,
and its `weft_status` management command reaches into `weft.commands.tasks`
directly because no client equivalent exists. Core CLI capabilities like
queue read/peek/watch, manager start/stop, and spec create/show have no
Python entry point at all.

This plan fixes four things:

1. **Unify the behavior first.** Extract one shared capability path out
   of the mixed CLI command handlers. The current `weft/core/ops/`
   scaffold may be used as a temporary extraction seam if that is the
   safest way to unify behavior, but it is not the final architecture.
2. **Finish the package split.** The final shape is:
   `weft/core/*` for engine internals,
   `weft/commands/*` for shared application capabilities,
   `weft/client/*` for the Python adapter, and
   `weft/cli/*` for the Typer adapter. `weft/cli.py` is replaced by a
   package. Any temporary `weft/core/ops/*` or `weft/core/types.py`
   scaffold is deleted before sign-off.
3. **Redesign the Python surface.** Replace single-file `weft/client.py`
   with a `weft/client/` package. Submission methods return a `Task`
   handle. Grouped operations live under noun namespaces
   (`client.tasks`, `client.queues`, `client.managers`, `client.specs`,
   `client.system`, plus `client.queues.aliases`) that mirror the CLI's
   grammar at the capability level.
4. **Make the specs and agent guidance tell the truth.** Update the
   Django spec and the architecture specs to describe what ships. Update
   agent-facing guidance so the layer split and import rules are durable
   repo policy rather than tribal knowledge.

No intentional core-runtime behavior changes beyond one explicit Django
bug fix: request-id capture moves from commit-time to enqueue-time so the
captured value survives request-context teardown. No async support. No
CLI grammar changes. No new dependencies. This is otherwise a boundary
extraction, package split, surface redesign, and spec amendment.

Completion note:
- The shipped `weft.commands.run` module is still CLI-oriented. It writes
  streaming and interactive output to stdout/stderr directly and remains the
  shared run owner for the CLI, but it is not yet the basis for a public
  `WeftClient.run()` streaming API.

---

## Primer For The Implementing Engineer

Read this if you have not worked in this codebase before. The rest of the
plan assumes you know the concepts in this section.

### What Weft is

Weft is a durable task execution substrate. A user submits a `TaskSpec`
(definition of work) and Weft runs it in an isolated subprocess with
resource limits, streams results into a queue, and logs lifecycle events
into a global log queue. Think "the subprocess library, but durable,
observable, and coordinated via message queues."

Weft is built on top of **SimpleBroker**, a small SQLite-backed message
queue library. SimpleBroker gives Weft: queue creation on first use (no
provisioning), hybrid timestamp ordering (each message gets a unique
monotonic 64-bit timestamp), durability (state lives on disk), and
watchers (long-poll for new messages). Weft does not reimplement any of
that.

The principle is **queues are the only source of truth**. Task state
lives in queue events, not a database. Even the "manager" (the thing
that spawns consumers) is itself a Weft task. Reading the current state
of the system means reading queue events.

### Key abstractions you will encounter

- **TaskSpec** — a Pydantic model describing one unit of work: target
  type (`function`, `command`, or `agent`), target (import path or shell
  command), args, environment, resource limits, and runner choice. Lives
  in `weft/core/taskspec.py`. Immutable after creation (has frozen list
  and dict helpers to enforce this).

- **TID (Task ID)** — a 19-digit string that is also a SimpleBroker
  hybrid timestamp in nanoseconds since epoch, with a logical counter
  mixed into the low bits. It is the unique identity of one task
  submission. When you see `int(tid)` being compared against
  `time.time_ns()` ranges, that is not a coincidence — they live in the
  same 64-bit numeric space. See `docs/specifications/02-TaskSpec.md` for
  the authoritative description. The fact that `submitted_at_ns ==
  int(tid)` is an invariant this plan will test.

- **WeftContext** — the resolved project context: where the broker
  database lives, what the project root is, what config is loaded.
  Produced by `build_context()` in `weft/context.py`. Every operation on
  a real Weft project starts by building (or receiving) one of these.

- **Queue names** — per-task queues follow the pattern
  `T{tid}.{suffix}` where suffix is one of `inbox`, `outbox`, `reserved`,
  `ctrl_in`, `ctrl_out`. Global queues are named
  `weft.log.tasks` (lifecycle events), `weft.spawn.requests` (manager
  input), etc. See `docs/specifications/00-Quick_Reference.md` for the
  complete list.

- **Manager** — a long-lived Weft task whose job is to drain
  `weft.spawn.requests` and spawn consumer subprocesses for each request.
  "Is the manager running?" is a frequent precondition. Starting one if
  needed is called "manager bootstrap."

- **Consumer** — the subprocess that actually runs one task's work item.
  Writes lifecycle events to `weft.log.tasks`, reads inbox messages, and
  writes results to `T{tid}.outbox`.

- **Spawn request** — a message on `weft.spawn.requests` that carries a
  TaskSpec payload and an inbox-seed message. The manager picks it up
  and spawns a consumer. See `weft/core/spawn_requests.py`.

- **Result materialization race** — when a caller says `weft result TID`
  for a task someone else submitted, the queue names may not yet be
  visible (the manager has not started the consumer). The "materialization
  wait" is the logic that handles this race by watching `weft.log.tasks`
  for first-activity signals. Only the `weft result TID` path needs this;
  `weft run --wait` already knows the queue names from the TaskSpec it
  just submitted.

### What the CLI and client do

- **CLI** (`weft/cli/*` in the final state): parses arguments,
  orchestrates, formats output, and produces exit codes. It is a Typer
  adapter over the shared capability layer.

- **Python client** (`weft/client.py` today, `weft/client/` after this
  plan): a programmatic API for embedding Weft in Python code. Same
  runtime operations, different surface (returns dataclasses, raises
  exceptions, does not write to stdout).

Both surfaces should call into the same underlying runtime operations.
Today they only partially do. This plan first extracts one shared
behavior path, then lands the final architecture where
`weft.commands.*` is that shared capability layer and both adapters sit
above it.

### What "noun-first with flat hot path" means

The CLI grammar is:

```
weft run CMD|--spec|--pipeline     # hot path — verb-first
weft task list
weft task status TID
weft task stop TID
weft task kill TID
weft queue peek NAME
weft queue read NAME
weft manager start
weft spec show NAME
weft status
weft system tidy
```

The Python client surface mirrors that with one dot per space:

```python
client.submit(...)              # hot path — no noun prefix
client.submit_spec(ref)
client.submit_pipeline(ref)
client.submit_command(cmd)
task = client.submit(...)
task.stop()
task.kill()
task.result()

client.tasks.list(...)          # noun-first for everything else
client.queues.peek(name)
client.queues.read(name)
client.managers.start()
client.specs.show(name)
client.system.status()
client.system.tidy()
```

This is deliberate. The submit-plus-handle path is the 90% case and
stays one dot. Grouped operations that have verb ambiguity at the top
level (`list` — of what? `status` — of what?) live under nouns. The
result is that a reader who knows the CLI can write Python without
translation, and vice versa.

### Code style you must match

Read `CLAUDE.md` §4 "House Style" end to end before editing. Summary of
the load-bearing rules:

- `from __future__ import annotations` at the top of every module.
- Modern type syntax: `list[int]`, `dict[str, Any]`, `X | None`. Never
  `List`, `Optional`, `Dict`.
- `Callable`, `Iterator`, `Mapping`, `Sequence` from `collections.abc`.
- `Final[T]` for constants; `Literal` for constrained strings.
- Imports at the top of the module — no lazy imports "to avoid cycles."
  If you hit a cycle, you have a layering problem; fix the layering.
- Pydantic `BaseModel` for anything that needs validation; dataclasses
  (`frozen=True, slots=True`) for simple immutable values.
- Custom exceptions dual-inherit from the stdlib exception plus
  `WeftError`, e.g. `class TaskNotFound(WeftError, LookupError)`.
- Module docstrings cite their governing spec sections, e.g.
  `Spec references: [CC-1], [TS-2]`.
- Function docstrings include `Spec: [XX-N]` when the function owns a
  spec boundary.

### Test design you must follow

You will be tempted to mock SimpleBroker, `submit_spawn_request`, or
`WeftContext`. Do not. The rules:

- **Broker-backed tests for anything that touches queues.** Use
  `tests.helpers.weft_harness.WeftTestHarness`. It gives you a real
  isolated broker, a real working context, and automatic cleanup. See
  existing tests under `tests/` for examples.
- **Only mock at true system boundaries** — external HTTP APIs, subprocess
  invocations you do not want to execute for real, clock in timing-
  sensitive tests. Everything inside weft is test-with-real.
- **Never mock `submit_spawn_request`, `Queue`, `WeftContext`, or
  `build_context`.** If a test wants to assert "this function submits
  a spawn request," it should run the function and then read the spawn
  request off the real queue.
- **Red-green TDD per slice.** For each task below, write the failing
  test first, watch it fail with the expected error, then implement the
  minimum to make it pass, then refactor. If a test passes on the first
  run before you wrote the code, that test is wrong — it is not
  exercising what you think.

Anti-patterns the reviewer will reject:

- `MagicMock(spec=WeftClient)` and asserting it was called. That tests
  your test, not your code.
- Patching a private helper to "simulate" a condition. If the condition
  is reachable through the public API, reach it through the public API.
- "Unit tests" that import `submit_taskspec` and assert it returned a
  `SubmittedTask`-shaped thing without actually submitting. That is a
  type-check, not a test. Either submit for real and inspect the queue,
  or do not write the test.
- Monkeypatching `time.time_ns()` to control TIDs. Use the harness and
  let real timestamps happen. If ordering matters, use the hybrid-
  timestamp comparison not real wall time.

Read `docs/specifications/08-Testing_Strategy.md` and
`CLAUDE.md` §5.2 "Test Design" before writing the first test.

### YAGNI and DRY for this plan specifically

- **YAGNI.** Do not add `async` variants "for later." Do not add a
  `retry=` parameter because Celery has one. Do not add config hooks
  "so users can plug in alternate implementations." If the spec does
  not call for it and no task below lists it, do not write it.
- **DRY.** If you find yourself implementing the same logic in two
  files, stop and extract. The point of this plan is to eliminate
  duplication between the CLI and the client. Do not recreate it
  elsewhere.
- **Do not touch code unrelated to this plan.** If you notice a bug in
  an adjacent module, file it separately. This plan is not a
  refactor-everything pass.

### How to ask for help

- If a CLI verb maps awkwardly to Python, stop and raise it. Do not
  invent a third grammar.
- If a test seems to require mocking an internal, raise it. Usually the
  right answer is a harness change or a different test shape.
- If §DJ-2.1 after amendment still does not match what you shipped,
  raise it before merging. Spec and code land together.

---

## Source Documents

Source specs:
- `docs/specifications/00-Overview_and_Architecture.md` — update the
  package split and implementation mapping notes
- `docs/specifications/13C-Using_Weft_With_Django.md` — the package of
  sections this plan amends: [DJ-2.1] (public client surface), [DJ-2.2]
  (public data shapes), [DJ-8.1]–[DJ-8.4] (submission API), [DJ-10.1]
  (`WeftSubmission` / `WeftResult`)
- `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.3]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-5], [MF-6]
- `docs/specifications/07-System_Invariants.md` — read in full before
  touching runtime ops
- `docs/specifications/08-Testing_Strategy.md` — read in full before
  writing tests
- `docs/specifications/10-CLI_Interface.md` [CLI-1] and subsections for
  each noun
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` — update the
  final adapter and capability mapping
- `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1], [PL-4.1]
- `docs/specifications/00-Quick_Reference.md` — quick queue-name lookup

Source guidance:
- `AGENTS.md`
- `CLAUDE.md` — §4 House Style, §5.1 How To Work, §5.2 Test Design
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Relevant existing plans:

- `docs/plans/2026-04-09-manager-bootstrap-unification-plan.md`
- `docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`

---

## Resolved Design Decisions

These are now fixed inputs to implementation. If new evidence forces a
change, update this plan first. Do not "decide in code."

1. **Product bar: near-complete capability parity.** `weft.client` is a
   real client, not a narrow embedding shim. The CLI and the client are
   peer adapter layers over one shared capability layer. Anything that
   is a meaningful runtime capability is in scope unless it appears in
   Documented Non-Parity Exceptions below.
2. **Phase 0 is mandatory.** Before expanding the client surface, extract
   the shared capability behavior out of the mixed CLI command handlers.
   The problem is not `cli.py` by itself; it is that `weft.commands.*`
   still mixes adapter logic and runtime logic. Task 1 is the hard gate
   for the rest of the plan.
3. **Final package split is fixed.**
   - `weft/core/*` is engine and runtime internals only.
   - `weft/commands/*` is the shared capability layer used by both the
     CLI adapter and the Python client.
   - `weft/client/*` is the Python adapter only.
   - `weft/cli/*` is the Typer adapter only, with `__main__.py` as the
     executable entrypoint.
4. **Temporary scaffolding is allowed but must be deleted.** If the
   implementation needs a temporary `weft/core/ops/*` and
   `weft/core/types.py` extraction layer to unify behavior safely, that
   is acceptable only during the migration. The final state removes that
   scaffold by moving shared capability modules into `weft/commands/*`
   and shared public dataclasses into `weft/commands/types.py` (or an
   equivalent command-layer home), then deleting the transitional paths.
5. **Adapter and import rules are strict.**
   - Allowed everywhere:
     `weft._constants`, `weft._exceptions`, `weft.context`,
     `weft.helpers`.
   - `weft.core.*` must not import `weft.commands.*`, `weft.cli.*`, or
     `weft.client.*`.
   - `weft.commands.*` may import `weft.core.*` and the allowed base
     modules, but must not import `weft.cli.*` or `weft.client.*`.
   - `weft.cli.*` and `weft.client.*` may import `weft.commands.*` and
     the allowed base modules.
   - No `typer` import is allowed outside `weft.cli.*`.
6. **Surface shape: hot path plus nouns.** The client keeps a flat hot
   path for submission and rehydration:
   `submit`, `submit_spec`, `submit_pipeline`, `submit_command`,
   `task(tid)`.
   Everything else is noun-first:
   `tasks`, `queues`, `managers`, `specs`, `system`.
   The one nested namespace is `client.queues.aliases` because the CLI
   already has two noun levels there: `weft queue alias <verb>`.
7. **Constructor and compatibility policy.**
   - Preferred constructor: `WeftClient(path=...)`.
   - Preserve current path-like named constructor semantics during the
     migration window:
     `WeftClient.from_context(spec_context: str | Path | None = None, *, autostart: bool | None = None)`.
   - If direct `WeftContext` injection is needed, add
     `WeftClient.from_weft_context(context: WeftContext)`.
   - Do not change `from_context(...)` to mean "must pass a WeftContext"
     while legacy callers still exist.
8. **Submission returns `Task`, not `SubmittedTask`.** A small internal
   receipt type may still exist in `weft.commands.types` for shared ops
   and tests, but `weft.client` does not re-export it. The public client
   submission return type is `Task`.
9. **Public type rule.** Runtime observations and receipts use typed
   dataclasses or concrete classes. Open-ended serialized payloads that
   are already "schema as data" stay as `dict[str, Any]` or
   `list[dict[str, Any]]`. Examples:
   `TaskSnapshot`, `TaskResult`, `QueueEntry`, `ManagerSnapshot` are
   typed; `specs.show(...)` and `specs.generate(...)` return serialized
   spec payloads.
10. **Task contracts are fixed.**
   - `client.task(tid)` validates TID shape and returns a lazy handle.
     It does not prove existence.
   - `Task.snapshot()` returns `TaskSnapshot | None`.
   - `client.tasks.status(...)` returns `TaskSnapshot | None`.
   - `Task.result(timeout)` always returns `TaskResult`.
   - `Task.stop()` and `Task.kill()` return `None` on success and raise
     typed exceptions on failure.
   - `Task.follow()` yields the normal event stream and ends with a
     synthetic `TaskEvent(event_type="result", ...)` whose payload is:
     `{"status": ..., "value": ..., "stdout": ..., "stderr": ..., "error": ...}`.
11. **`submit_command` semantics are explicit.**
    - Signature:
      `submit_command(command: Sequence[str] | str, *, payload: Any = None, shell: bool = False, **overrides: Any) -> Task`
    - `Sequence[str]` means exact argv.
    - `str` with `shell=False` means parse with `shlex.split`.
    - `shell=True` is out of scope for this plan.
12. **Public submit overrides are explicit.** The only accepted
    overrides on `submit`, `submit_spec`, `submit_pipeline`, and
    `submit_command` are:
    `name`, `description`, `tags`, `env`, `working_dir`, `timeout`,
    `memory_mb`, `cpu_percent`, `runner`, `runner_options`, `metadata`.
    Unknown keys raise `TypeError`. Internal pipeline flags such as
    `seed_start_envelope` and `allow_internal_runtime` are never public.
13. **Legacy compatibility exists only as a migration tool.** During the
    Task 2-6 window, the new `WeftClient` class may carry thin legacy
    forwarders for the old surface. They call the same shared ops as the
    new surface. They are removed in Task 7.
14. **No async, no CLI grammar changes, no new dependencies.**

---

## Capability Parity Matrix

This is the contract. Treat it as normative for this plan. "Same code
path" here means the CLI adapter and the client adapter call the same
shared op for the same capability. It does not mean the CLI inherits
client error behavior or the client inherits Typer exit codes. The
module names in the "Shared op" column are the final `weft.commands.*`
homes. Task 1 may use a temporary `weft/core/ops/*` extraction seam,
but Task 4 must land the final paths named here.

### Hot path and task surface

| CLI capability | Shared op | Client surface | Return type | Notes |
| --- | --- | --- | --- | --- |
| `weft run CMD...` | `commands.submission.submit_command` | `client.submit_command(command, *, payload=None, shell=False, **overrides)` | `Task` | `Sequence[str]` exact argv. `str` means `shlex.split`. |
| `weft run --spec REF` | `commands.submission.submit_spec_reference` | `client.submit_spec(reference, *, payload=None, **overrides)` | `Task` | Shared spec resolution path. |
| `weft run --pipeline REF` | `commands.submission.submit_pipeline_reference` | `client.submit_pipeline(reference, *, payload=None, **overrides)` | `Task` | Shared pipeline compile and internal runtime path. |
| `weft result TID` | `commands.results.await_task_result` | `client.task(tid).result(timeout=None)` | `TaskResult` | Same materialization and wait logic. |
| `weft status` | `commands.system.system_status` | `client.system.status()` | `SystemStatusSnapshot` | Top-level status remains under `system`. |
| `weft task list` | `commands.tasks.list_task_snapshots` | `client.tasks.list(*, status=None, include_terminal=False)` | `list[TaskSnapshot]` | |
| `weft task list --stats` | `commands.tasks.task_stats` | `client.tasks.stats(*, status=None, include_terminal=False)` | `dict[str, int]` | Separate method to avoid union return types. |
| `weft task status TID` | `commands.tasks.task_snapshot` | `client.tasks.status(tid, *, include_process=False)` | `TaskSnapshot \| None` | |
| `weft task status TID --process` | `commands.tasks.task_snapshot` | `client.tasks.status(tid, *, include_process=True)` | `TaskSnapshot \| None` | `TaskSnapshot` grows optional process fields or nested process info. |
| `weft task status TID --watch` | `commands.tasks.watch_task_status` | `client.tasks.watch(tid, *, include_process=False)` | `Iterator[TaskSnapshot]` | Client gets iterator, not terminal rendering. |
| `weft task stop TID` | `commands.tasks.stop_task` | `client.task(tid).stop()` or `client.tasks.stop(tid)` | `None` | Raises typed exceptions on failure. |
| `weft task stop --all / --pattern` | `commands.tasks.stop_tasks` | `client.tasks.stop_many(*, tids=None, all_tasks=False, pattern=None)` | `int` | Count of tasks asked to stop. |
| `weft task kill TID` | `commands.tasks.kill_task` | `client.task(tid).kill()` or `client.tasks.kill(tid)` | `None` | Raises typed exceptions on failure. |
| `weft task kill --all / --pattern` | `commands.tasks.kill_tasks` | `client.tasks.kill_many(*, tids=None, all_tasks=False, pattern=None)` | `int` | Count of processes asked to kill. |
| `weft task tid ...` | `commands.tasks.resolve_tid` | `client.tasks.resolve_tid(*, tid=None, pid=None, reverse=None)` | `str \| None` | Same resolution rules. |

### Queue surface

| CLI capability | Shared op | Client surface | Return type | Notes |
| --- | --- | --- | --- | --- |
| `weft queue read NAME` | `commands.queues.read_queue` | `client.queues.read(name, *, all_messages=False, message_id=None, since=None)` | `list[QueueEntry]` | Timestamps are always present in `QueueEntry`. No `--json` flag mirror. |
| `weft queue write NAME MESSAGE` | `commands.queues.write_queue` | `client.queues.write(name, message)` | `QueueWriteReceipt` | |
| `weft queue write --endpoint NAME MESSAGE` | `commands.queues.write_endpoint` | `client.queues.write_endpoint(name, message)` | `QueueWriteReceipt` | Separate method instead of nullable queue name. |
| `weft queue peek NAME` | `commands.queues.peek_queue` | `client.queues.peek(name, *, all_messages=False, message_id=None, since=None)` | `list[QueueEntry]` | |
| `weft queue move SRC DST` | `commands.queues.move_queue_messages` | `client.queues.move(source, destination, *, limit=None, all_messages=False, message_id=None, since=None)` | `QueueMoveReceipt` | |
| `weft queue list` | `commands.queues.list_queues` | `client.queues.list(*, pattern=None, include_stats=False, include_endpoints=False)` | `list[QueueInfo]` | |
| `weft queue resolve ENDPOINT` | `commands.queues.resolve_endpoint` | `client.queues.resolve(endpoint_name)` | `EndpointResolution` | |
| `weft queue watch NAME` | `commands.queues.watch_queue` | `client.queues.watch(name, *, limit=None, interval=0.5, peek=False, since=None, move_to=None)` | `Iterator[QueueEntry]` | Same queue-watch behavior, Python iterator surface. |
| `weft queue delete NAME / --all` | `commands.queues.delete_queue_messages` | `client.queues.delete(name=None, *, all_queues=False, message_id=None)` | `QueueDeleteReceipt` | |
| `weft queue broadcast MESSAGE --pattern PATTERN` | `commands.queues.broadcast` | `client.queues.broadcast(message, *, pattern=None)` | `QueueBroadcastReceipt` | Exact capability; no fake `name=` argument. |
| `weft queue alias add` | `commands.queues.add_alias` | `client.queues.aliases.add(alias, target)` | `QueueAliasRecord` | In scope. Not deferred. |
| `weft queue alias list` | `commands.queues.list_aliases` | `client.queues.aliases.list(*, target=None)` | `list[QueueAliasRecord]` | |
| `weft queue alias remove` | `commands.queues.remove_alias` | `client.queues.aliases.remove(alias)` | `None` | |

### Manager surface

| CLI capability | Shared op | Client surface | Return type | Notes |
| --- | --- | --- | --- | --- |
| `weft manager start` | `commands.managers.start_manager` | `client.managers.start()` | `ManagerSnapshot` | |
| `weft manager serve` | `commands.managers.serve_manager` | `client.managers.serve()` | `None` | Blocking foreground loop. This is in scope. |
| `weft manager stop TID` | `commands.managers.stop_manager` | `client.managers.stop(tid, *, force=False, timeout=5.0)` | `None` | |
| `weft manager list` | `commands.managers.list_managers` | `client.managers.list(*, include_stopped=False)` | `list[ManagerSnapshot]` | |
| `weft manager status TID` | `commands.managers.manager_status` | `client.managers.status(tid)` | `ManagerSnapshot \| None` | |

### Spec surface

| CLI capability | Shared op | Client surface | Return type | Notes |
| --- | --- | --- | --- | --- |
| `weft spec create NAME --file FILE --type TYPE` | `commands.specs.create_spec` | `client.specs.create(name, source, *, spec_type="task", force=False)` | `SpecRecord` | `source` is `Path` or serialized payload. |
| `weft spec list` | `commands.specs.list_specs` | `client.specs.list(*, spec_type=None)` | `list[SpecRecord]` | |
| `weft spec show NAME` | `commands.specs.show_spec` | `client.specs.show(name, *, spec_type=None)` | `dict[str, Any]` | Serialized spec payload. |
| `weft spec delete NAME` | `commands.specs.delete_spec` | `client.specs.delete(name, *, spec_type=None)` | `Path` | Deleted path is the most concrete useful return. |
| `weft spec validate FILE` | `commands.specs.validate_spec` | `client.specs.validate(source, *, spec_type=None, load_runner=False, preflight=False)` | `SpecValidationResult` | Covers `validate_taskspec` path too. |
| `weft spec generate --type TYPE` | `commands.specs.generate_spec` | `client.specs.generate(*, spec_type="task")` | `dict[str, Any]` | Serialized generated payload. |

### System surface

| CLI capability | Shared op | Client surface | Return type | Notes |
| --- | --- | --- | --- | --- |
| `weft system tidy` | `commands.system.tidy_system` | `client.system.tidy()` | `SystemTidyResult` | |
| `weft system dump` | `commands.system.dump_system` | `client.system.dump(*, output=None)` | `Path` | Returns output path. |
| `weft system builtins` | `commands.system.list_builtins` | `client.system.builtins()` | `list[dict[str, Any]]` | Builtin inventory is open-ended serialized data. |
| `weft system load` | `commands.system.load_system` | `client.system.load(*, input_file=None, dry_run=False)` | `SystemLoadResult` | |

### Client-only ergonomic affordances

These are not parity exceptions. They are Python-native wrappers over the
same shared code:

- `client.submit(...)` wraps normalized `TaskSpec` submission directly.
- `client.task(tid)` rehydrates a handle from a bare TID.
- `Task.events(follow=False)` exposes raw lifecycle events.
- `Task.follow()` exposes lifecycle events plus the synthetic final
  `result` event.

---

## Documented Non-Parity Exceptions

These are the only intentional client omissions in this plan.

- `weft init`: project bootstrapping is a one-time filesystem command,
  not a runtime capability. Leave it CLI-only.
- `weft interactive`: the CLI interactive session owns a TTY, prompt
  flow, and readline state. There is no stable client equivalent here.
- CLI rendering and UX flags: `--json`, `--quiet`, `--verbose`,
  `--timestamps`, `--version`, help text, shell completion, and similar
  presentation-only flags do not get 1:1 client methods. The client
  returns structured data instead.

Everything else in the matrix above is in scope.

---

## Context And Key Files

### Files to create

- `weft/commands/types.py` — shared public dataclasses and receipts used
  by the capability layer, CLI adapter, and client adapter.
- `weft/client/__init__.py`
- `weft/client/_client.py`
- `weft/client/_task.py`
- `weft/client/_namespaces.py`
- `weft/client/_types.py`
- `weft/cli/__init__.py`
- `weft/cli/__main__.py`
- `weft/cli/app.py` and per-domain adapter modules as needed
- `tests/core/test_ops_shared.py`
- `tests/core/test_exceptions.py`
- `tests/core/test_client.py`
- `tests/architecture/test_import_boundaries.py`

Temporary files may exist during the extraction phase only:

- `weft/core/ops/*`
- `weft/core/types.py`

If they are introduced or already present in the working tree, Task 4
must remove them before final sign-off.

### Files to modify

- `weft/__init__.py`
- `weft/cli.py` — replaced atomically with `weft/cli/` in Task 4
- `weft/commands/run.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/status.py`
- `weft/commands/queue.py`
- `weft/commands/manager.py`
- `weft/commands/specs.py`
- `weft/commands/serve.py`
- `weft/commands/tidy.py`
- `weft/commands/dump.py`
- `weft/commands/load.py`
- `weft/commands/builtins.py`
- `weft/commands/_manager_bootstrap.py`
- `weft/commands/_spawn_submission.py`
- `weft/commands/_result_wait.py`
- `weft/commands/_queue_wait.py`
- `weft/commands/_task_history.py`
- `weft/client.py` — deleted atomically when the package lands in Task 2
- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `integrations/weft_django/weft_django/__init__.py`
- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/decorators.py`
- `integrations/weft_django/weft_django/views.py`
- `integrations/weft_django/weft_django/sse.py`
- `integrations/weft_django/weft_django/management/commands/weft_status.py`
- `integrations/weft_django/tests/test_weft_django.py`
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/plans/README.md`

### Files to read before editing

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `weft/client.py`
- `weft/cli.py`
- `weft/core/ops/*` if the temporary scaffold already exists
- `weft/commands/run.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/queue.py`
- `weft/commands/manager.py`
- `weft/commands/specs.py`
- `weft/commands/status.py`
- `weft/core/spawn_requests.py`
- `weft/context.py`
- `weft/core/taskspec.py`
- `weft/core/pipelines.py`
- `tests/helpers/weft_harness.py`

### Shared helpers that stay shared

These already sit at the right boundary. Reuse them. Do not rewrite them
unless a task below explicitly requires it.

- `build_context()` in `weft/context.py`
- `submit_spawn_request()` in `weft/core/spawn_requests.py`
- `compile_linear_pipeline()` and `load_pipeline_spec_payload()` in
  `weft/core/pipelines.py`
- `TaskSpec.model_validate(...)` and related TaskSpec machinery
- SimpleBroker `Queue`

---

## Invariants And Constraints

These are review blockers. If implementation violates one, stop.

**Layering**

- Allowed everywhere:
  `weft._constants`, `weft._exceptions`, `weft.context`, `weft.helpers`.
- `weft.core.*` must not import `weft.commands.*`, `weft.cli.*`, or
  `weft.client.*`.
- `weft.commands.*` may import `weft.core.*` and the allowed base
  modules. It must not import `weft.cli.*` or `weft.client.*`.
- `weft.client.*` and `weft.cli.*` may import `weft.commands.*` and the
  allowed base modules.
- No `typer` import is allowed outside `weft.cli.*`.
- `integrations/weft_django/weft_django/` imports only from the public
  `weft.client` surface plus the small set of already-public core helper
  modules it already relies on. No `weft.commands.*` imports remain.

**Runtime behavior**

- Poll intervals, wait deadlines, manager bootstrap semantics, spawn
  recovery outcomes, task-status derivation, queue alias behavior, and
  spec resolution behavior must remain unchanged unless this plan says
  otherwise.
- CLI output and exit codes do not change.
- Queue formats and message envelopes do not change, except for the
  request-id capture timing bug fix in Task 5. That fix must preserve
  the existing envelope shape.

**Public contracts**

- `Task` does not own broker resources and does not gain `close()`.
- `Task.snapshot()` and `client.tasks.status(...)` both return
  `TaskSnapshot | None`. Pick one rule and keep it everywhere.
- `Task.result(timeout)` returns `TaskResult(status="timeout", ...)`
  for a wait timeout and `TaskResult(status="missing", ...)` when there
  is no record and no timeout was supplied. Preserve that rule.
- `Task.stop()` and `Task.kill()` return `None` on success and raise
  `TaskNotFound` or `ControlRejected` on failure.
- `submitted_at_ns == int(tid)` must hold for freshly submitted tasks.
- `Task.snapshot()` must never fall back to `name=tid` when the real
  task name is available. That bug was already identified; do not carry
  it forward.

**Phase 0 stop gate**

No work on the new client surface beyond Task 2 scaffolding may proceed
until all of these are true:

- There is one shared behavior path for the hot path and every noun in
  the parity matrix. If a temporary `weft/core/ops/*` scaffold exists,
  both the CLI side and the client side delegate through it.
- Current `weft/client.py` no longer depends on private underscore
  command helpers or duplicate runtime logic.
- There is no duplicate manager-bootstrap, spawn-recovery, result-wait,
  or task-control logic between CLI and client routes.
- `tests/core/test_ops_shared.py` passes.

**Package replacement**

- `weft/client.py` and `weft/client/__init__.py` cannot coexist. Task 2
  replaces the module with the package atomically.
- `weft/cli.py` and `weft/cli/` cannot coexist. Task 4 replaces the
  module with the package atomically.
- Compatibility is provided by legacy forwarders on the new
  `WeftClient` class. There is no `_legacy.py` sidecar and no period
  where both module and package are importable.

**Review gates**

- Independent review after Task 1, Task 4, and Task 5.
- Reviewer should be a different agent family when available.
- Use the review-loop runbook already listed in Source Documents.

---

## Phases And Tasks

Work order:

1. Task 1. Phase 0 boundary extraction and parity gate
2. Task 2. Types, exceptions, and client package scaffold
3. Task 3. Full client implementation
4. Task 4. Final package reshape and import-boundary enforcement
5. Task 5. Django migration and request-id fix
6. Task 6. Spec and agent-guidance updates
7. Task 7. Legacy removal
8. Task 8. Final verification and sign-off

Do not overlap them casually. Each one has a hard done signal.
Task 4 is not complete until Task 4A, Task 4B, and Task 4C all pass.

### Task 1 — Phase 0: Extract the shared capability layer and lock the boundary

**Outcome**

There is one shared behavior path below the mixed adapters. If the
safest extraction path uses a temporary `weft/core/ops/*` scaffold, that
scaffold exists at the end of this task and both the command side and
the current single-file `weft.client.py` call it. This task is not "hot
path only." It moves the runtime behavior for the command groups in the
parity matrix below the adapters. If the PR must be split, split by
domain module, but do not start Task 3 until the whole phase is done.

**Files to touch**

- Create the temporary extraction scaffold only if needed:
  `weft/core/ops/` package and its domain modules.
- Modify every command module named in Context And Key Files so it calls
  the shared ops package.
- Modify current `weft/client.py` so it uses the same shared ops and no
  longer imports private CLI helper modules or carries duplicate logic.
- Create `tests/core/test_ops_shared.py`.
- Create `tests/architecture/test_import_boundaries.py` if the AST
  boundary harness is straightforward to land early; otherwise land it
  in Task 4.

**TDD prompt**

Write broker-backed and identity-style tests that prove the adapters are
thin without overfitting to file names:

```python
def test_run_and_result_adapters_use_shared_ops() -> None:
    import weft.commands.run as run_mod
    import weft.commands.result as result_mod
    from weft.core.ops import submission, results

    assert run_mod.ensure_manager_after_submission is (
        submission.ensure_manager_after_submission
    )
    assert result_mod.await_task_result is results.await_task_result


def test_current_client_submission_and_cli_wait_match(harness) -> None:
    from weft.client import WeftClient
    from weft.core.taskspec import TaskSpec

    client = WeftClient.from_context(harness.root)
    taskspec = TaskSpec.model_validate(
        {
            "name": "hello-task",
            "spec": {
                "type": "command",
                "process_target": "echo",
                "args": ["hello"],
            },
        },
        context={"template": True, "auto_expand": False},
    )
    submitted = client.submit_taskspec(taskspec)
    result = client.result(submitted.tid, timeout=30.0)

    assert result.status == "completed"
    assert "hello" in (result.stdout or str(result.value))
```

Add one representative parity test per domain group:
tasks/results, queues, managers, specs, system.

**Implementation**

1. Create the ops package with domain ownership split as follows:
   - `submission.py`: TID generation, TaskSpec normalization, submission,
     manager ensure, spawn recovery.
   - `results.py`: result materialization, queue-name resolution, result
     waiting.
   - `tasks.py`: task listing, status, watch, stop, kill, TID resolve.
   - `events.py`: task event iteration and synthetic final result event.
   - `queues.py`: queue read/write/peek/move/list/watch/delete/broadcast
     and alias operations.
   - `managers.py`: manager start/serve/stop/list/status.
   - `specs.py`: spec create/list/show/delete/validate/generate and any
     pure resolver helpers that must move for layering.
   - `system.py`: top-level system status, tidy, dump, load, builtins.
2. Move duplicated and private logic out of `weft.commands._*` and the
   command modules into the appropriate ops module. Leave a thin
   re-export shim only if another caller still imports the old symbol
   during this task.
3. Rewire the command modules so that their business logic disappears.
   After the rewrite, the command module should mostly look like:
   parse inputs -> call one shared op -> render -> translate exceptions.
4. Rewire current `weft/client.py` to the same ops package. This keeps
   current behavior stable while Task 2 prepares the package split.
5. Keep `resolve_spec_reference(...)` where it is only if doing so does
   not force `weft.core.ops.*` to import from `weft.commands.*`. If it
   does, move the resolver now. The boundary matters more than path
   purity.

**Stop and re-evaluate if**

- Any `weft.core.ops.*` module wants to import from `weft.commands.*`.
- A command module still owns non-trivial runtime branching after the move.
- The queue, manager, or spec nouns cannot be extracted cleanly without
  exposing a real contract disagreement. If so, update the parity matrix
  before coding further.

**Done signal**

- `tests/core/test_ops_shared.py` passes.
- `rg -n "from weft.commands\\._|import weft.commands\\._" weft/client.py`
  returns nothing.
- `rg -n "def _ensure_manager_after_submission|def _await_result_materialization|def _await_single_result" weft`
  returns only shims or deleted-path references that this task removes.
- `pytest tests/core tests/cli integrations/weft_django/tests -q` passes.

### Task 2 — Add typed exceptions and replace `weft/client.py` with the package scaffold

**Outcome**

The new `weft/client/` package exists. The public shape is frozen, but
most new methods may still raise `NotImplementedError` at the start of
this task. Legacy compatibility is carried by forwarders on the new
`WeftClient` class. `SubmittedTask` is no longer a public client type.

**Files to touch**

- Create or extend `weft/_exceptions.py`.
- Create `weft/commands/types.py`.
- Delete `weft/client.py` and atomically replace it with
  `weft/client/__init__.py`, `_client.py`, `_task.py`, `_namespaces.py`,
  `_types.py`.
- Update `weft/__init__.py`.

**Key signatures to freeze in this task**

```python
class WeftClient:
    def __init__(
        self,
        context: WeftContext | None = None,
        *,
        path: str | Path | None = None,
    ) -> None: ...

    @classmethod
    def from_context(
        cls,
        spec_context: str | Path | None = None,
        *,
        autostart: bool | None = None,
    ) -> WeftClient: ...

    @classmethod
    def from_weft_context(cls, context: WeftContext) -> WeftClient: ...

    def submit(... ) -> Task: ...
    def submit_spec(... ) -> Task: ...
    def submit_pipeline(... ) -> Task: ...
    def submit_command(... ) -> Task: ...
    def task(self, tid: str) -> Task: ...
```

`TasksNamespace`, `QueuesNamespace`, `QueueAliasesNamespace`,
`ManagersNamespace`, `SpecsNamespace`, and `SystemNamespace` all exist at
the end of this task, even if some new methods still stub.

**TDD prompt**

```python
def test_public_names_are_importable() -> None:
    from weft.client import Task, TaskEvent, TaskResult, TaskSnapshot, WeftClient
    assert Task is not None
    assert WeftClient is not None


def test_submitted_task_is_not_public() -> None:
    import weft.client as client_mod
    assert not hasattr(client_mod, "SubmittedTask")


def test_legacy_forwarders_exist_during_migration(harness) -> None:
    from weft.client import WeftClient
    client = WeftClient.from_context(harness.root)
    assert callable(client.submit_taskspec)
    assert callable(client.result)
```

**Implementation**

1. Add typed exceptions only in `weft/_exceptions.py`. Do not create a
   second exceptions module. Minimum required set:
   `WeftError`, `InvalidTID`, `TaskNotFound`, `ControlRejected`,
   `SpecNotFound`, `ManagerNotRunning`.
2. Add shared types in `weft/commands/types.py`:
   `TaskSnapshot`, `TaskResult`, `TaskEvent`, `QueueEntry`,
   `QueueInfo`, `QueueWriteReceipt`, `QueueMoveReceipt`,
   `QueueDeleteReceipt`, `QueueBroadcastReceipt`, `QueueAliasRecord`,
   `EndpointResolution`, `ManagerSnapshot`, `SpecRecord`,
   `SpecValidationResult`, `SystemStatusSnapshot`, `SystemTidyResult`,
   `SystemLoadResult`, plus any nested structs needed for process info.
   Keep the internal submission receipt there too if shared ops need it,
   but do not re-export it from `weft.client`.
3. Replace `weft/client.py` atomically with the package.
4. Put legacy forwarders directly on the new `WeftClient` class. Do not
   create `_legacy.py`.
5. Re-export only public client types from `weft/client/__init__.py`.

**Stop and re-evaluate if**

- The scaffold cannot express some CLI capability cleanly. Fix the
  parity matrix now, not later.
- A public method still needs `Any` when a concrete type or
  `dict[str, Any]` would do.

**Done signal**

- `pytest tests/core/test_exceptions.py tests/core/test_client.py -q -k "importable or public or legacy"`
  passes.
- `weft/client.py` no longer exists.
- `weft.client.SubmittedTask` is not public.

### Task 3 — Implement the full client surface on top of shared ops

**Outcome**

The client becomes the second view over the shared capability layer.
Every in-scope row in the parity matrix has a working client method.
Before Task 4, `weft.client` may still point at the temporary extraction
layer rather than its final `weft.commands.*` home.

**Files to touch**

- `weft/client/_client.py`
- `weft/client/_task.py`
- `weft/client/_namespaces.py`
- `weft/client/_types.py`
- `weft/core/ops/*` only where a missing shared op is still required
- `tests/core/test_client.py`

**Implementation**

1. Make every public client method a thin adapter over one shared op.
   If a client method needs more than a few lines, the missing logic
   belongs in `weft.core.ops.*`.
2. Implement the hot path:
   - `submit`
   - `submit_spec`
   - `submit_pipeline`
   - `submit_command`
   - `task`
3. Implement the `Task` handle:
   - `snapshot`
   - `result`
   - `events`
   - `follow`
   - `stop`
   - `kill`
4. Implement all noun namespaces from the parity matrix, including:
   - `client.tasks.list`, `stats`, `status`, `watch`, `resolve_tid`,
     `stop`, `stop_many`, `kill`, `kill_many`
   - `client.queues.read`, `write`, `write_endpoint`, `peek`, `move`,
     `list`, `resolve`, `watch`, `delete`, `broadcast`,
     `aliases.add`, `aliases.list`, `aliases.remove`
   - `client.managers.start`, `serve`, `stop`, `list`, `status`
   - `client.specs.create`, `list`, `show`, `delete`, `validate`, `generate`
   - `client.system.status`, `tidy`, `dump`, `builtins`, `load`
5. Preserve the legacy forwarders until Task 7, but route them through
   the same shared ops and new handle semantics.

**TDD prompt**

Use `WeftTestHarness`. Minimum required coverage:

```python
def test_submit_command_returns_task_with_completed_result(harness) -> None:
    from weft.client import WeftClient

    client = WeftClient(path=harness.root)
    task = client.submit_command(["echo", "hello"])
    result = task.result(timeout=30.0)

    assert result.status == "completed"
    assert "hello" in (result.stdout or str(result.value))


def test_queue_alias_roundtrip(harness) -> None:
    from weft.client import WeftClient

    client = WeftClient(path=harness.root)
    client.queues.aliases.add("my-alias", "test.queue")
    aliases = client.queues.aliases.list()
    assert any(item.alias == "my-alias" for item in aliases)


def test_task_follow_ends_with_result_event(harness) -> None:
    from weft.client import WeftClient

    client = WeftClient(path=harness.root)
    task = client.submit_command(["echo", "done"])
    events = list(task.follow())
    assert events[-1].event_type == "result"
    assert events[-1].payload["status"] == "completed"
```

Add one happy-path test per public method family and at least one
parity subprocess test per command group. Do not rely only on unit-level
adapter tests.

**Stop and re-evaluate if**

- A namespace method wants to swallow exceptions and return `bool`.
- A client signature drifts from the parity matrix.
- `submit_command` semantics become ambiguous again. Keep the
  `Sequence[str]` versus `str` rule explicit.

**Done signal**

- `pytest tests/core/test_client.py -q` passes in full.
- `rg -n "from weft.commands\\._|import weft.commands\\._" weft/client`
  returns nothing.
- All in-scope parity-matrix rows have live client methods.

### Task 4 — Finish the package reshape and enforce import boundaries

**Outcome**

The final package split is real, not aspirational:

- `weft/core/*` contains engine internals only
- `weft/commands/*` contains the shared capability layer
- `weft/client/*` is the Python adapter
- `weft/cli/*` is the Typer adapter

Any temporary `weft/core/ops/*`, `weft/core/types.py`, module-alias
wrappers, or compatibility glue that buries real implementations under
`core` are gone. The import-boundary tests enforce the one-way graph.

**Files to touch**

- `weft/cli.py` — replaced atomically with `weft/cli/`
- `weft/cli/__init__.py`
- `weft/cli/__main__.py`
- `weft/cli/app.py` and per-domain adapter modules as needed
- `weft/commands/*.py`
- `weft/commands/types.py`
- `weft/client/*`
- `weft/core/ops/*` — deleted by the end of the task if present
- `weft/core/types.py` — deleted by the end of the task if present
- `tests/core/test_ops_shared.py`
- `tests/architecture/test_import_boundaries.py`

**Implementation**

1. Move the shared capability implementations from any temporary
   `weft/core/ops/*` scaffold into their final `weft/commands/*`
   modules.
2. Move shared public dataclasses and receipts out of
   `weft/core/types.py` into `weft/commands/types.py`.
3. Replace `weft/cli.py` atomically with a `weft/cli/` package and move
   Typer-only concerns there: command registration, parameter mapping,
   rendering, and exit-code translation.
4. Delete the module-alias wrappers. Old `weft.commands.*` files become
   the real shared implementation, not compatibility shells.
5. Repoint `weft.client.*` and `weft.cli.*` imports at `weft.commands.*`
   and the allowed base modules.
6. Add AST-based import-boundary tests that fail on:
   - `weft.core.* -> weft.commands.* | weft.cli.* | weft.client.*`
   - `weft.commands.* -> weft.cli.* | weft.client.*`
   - any `typer` import outside `weft.cli.*`
7. Keep the allowed-base-module exception list explicit in the test:
   `weft._constants`, `weft._exceptions`, `weft.context`,
   `weft.helpers`.

**TDD prompt**

```python
def test_internal_import_boundaries() -> None:
    violations = find_import_boundary_violations(...)
    assert violations == []


def test_typer_only_lives_under_cli() -> None:
    offenders = find_typer_imports_outside_cli(...)
    assert offenders == []
```

Use `ast`, not regexes, so relative imports are handled correctly.

**Stop and re-evaluate if**

- `weft.commands.*` still looks like adapter code after the move.
- `weft.client.*` or `weft.cli.*` still needs broad direct imports from
  `weft.core.*` after `weft.commands.types.py` exists.
- The import-boundary test needs a large exception list beyond the four
  allowed base modules.

**Done signal**

- `tests/architecture/test_import_boundaries.py` passes.
- `rg -n "sys.modules\\[__name__\\]" weft/commands weft/core` returns
  zero matches.
- `rg -n "import typer|from typer" weft | rg -v "^weft/cli/"` returns
  zero matches.
- `rg -n "weft/core/ops|weft.core.ops|weft/core/types.py|weft.core.types" weft tests integrations`
  returns only deleted-path references or plan/doc text that this task
  then updates.
- `test ! -d weft/core/ops` succeeds.
- `test ! -f weft/core/types.py` succeeds.

### Task 4A — Completion sub-plan: delete `weft/core/ops` and finish the one-way graph

**Outcome**

This is the hardening slice for the final architecture, not an optional
cleanup pass. At the end of this sub-task:

- `weft/core/ops/` does not exist
- `weft/core/types.py` does not exist unless a reviewer explicitly
  proves it contains only true core-internal types and no app-surface
  contracts
- `weft.commands.*` contains the real shared capability code
- `weft.client.*` imports only `weft.commands.*` plus the four allowed
  base modules
- `weft.cli.*` imports only `weft.commands.*`, `typer`, and the four
  allowed base modules
- no `sys.modules[__name__]` alias shims remain
- no `typer` import remains outside `weft.cli.*`

This is the point where the temporary extraction seam stops existing.
Do not treat "unused but still present" as good enough. The directory and
its compatibility story must be gone.

**Read first**

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/client/_client.py`
- `weft/client/_task.py`
- `weft/client/_namespaces.py`
- `weft/cli/app.py`
- `weft/cli/run.py`
- every remaining module under `weft/core/ops/`

**Comprehension check before editing**

Before touching code, the implementer should be able to answer:

1. Which remaining client methods still call `weft.core.ops.*` directly,
   and what permanent `weft.commands.*` home should each of those
   behaviors have?
2. Which functions in `weft/core/ops/managers.py` are pure runtime
   mechanics, which are shared manager capabilities, and which are
   CLI-only output or exit-code behavior?

If those answers are fuzzy, stop and do the audit first.

**Files to touch**

- Delete: `weft/core/ops/`
- Delete or fully rehome: `weft/core/types.py`
- Promote to real shared implementations:
  - `weft/commands/result.py`
  - `weft/commands/tasks.py`
  - `weft/commands/queue.py`
  - `weft/commands/specs.py`
  - `weft/commands/manager.py`
  - `weft/commands/submission.py`
  - `weft/commands/types.py`
- Create if needed for clean ownership:
  - `weft/commands/events.py`
  - `weft/commands/system.py`
  - one or more permanent non-`ops` core modules such as
    `weft/core/manager_bootstrap.py` if runtime mechanics still need a
    core home after the move
- Update:
  - `weft/client/_client.py`
  - `weft/client/_task.py`
  - `weft/client/_namespaces.py`
  - `weft/cli/app.py`
  - `weft/cli/run.py`
  - any other `weft/cli/*` adapters created in Task 4
  - `tests/core/test_ops_shared.py`
  - `tests/architecture/test_import_boundaries.py`

**Required classification rule**

For every remaining symbol under `weft/core/ops/*`, classify it before
moving it:

- shared capability used by CLI and/or client: move to `weft.commands.*`
- true engine/runtime mechanic with no CLI/client contract of its own:
  move to a permanent `weft/core/*` module
- output formatting, `typer` exceptions, table/JSON rendering, exit-code
  shaping, or CLI verbosity behavior: move to `weft/cli/*`

Do not move files wholesale without this audit. The purpose of the task
is not "rename `core/ops` to `commands`." The purpose is to leave only
real core in `core`, only shared capabilities in `commands`, and only
adapter behavior in `cli` and `client`.

**Implementation**

1. Audit the remaining `weft/core/ops/*` package and write down the
   target home for each module before editing. The expected end-state
   map is:
   - `core.ops.submission` -> `weft/commands/submission.py`
   - `core.ops.results` and `core.ops._result_wait` -> `weft/commands/result.py`
     plus `weft/commands/events.py` or command-private helpers
   - `core.ops.tasks` -> `weft/commands/tasks.py`
   - `core.ops.queues` -> `weft/commands/queue.py`
   - `core.ops.specs` -> `weft/commands/specs.py`
   - `core.ops.system` -> `weft/commands/system.py` or a clearly
     documented permanent `weft.commands.*` split by capability
   - `core.ops.managers` -> split between permanent core runtime
     mechanics and `weft/commands/manager.py`
2. Promote the alias modules in `weft/commands/*` into real
   implementations in place. The permanent rule is:
   `weft.commands.*` is the shared capability layer.
   Any file that only says "backward-compatible module alias" is not
   done.
3. Eliminate client imports from `weft.core.*`. Specifically:
   - `weft/client/_client.py` must stop importing `weft.core.ops`
   - `weft/client/_task.py` must stop importing `weft.core.ops`
   - `weft/client/_namespaces.py` must stop importing `weft.core.ops`
   - replace them with imports from `weft.commands.*` and
     `weft.commands.types`
4. Eliminate CLI imports from `weft.core.*` except for the four allowed
   base modules and any true core module that a reviewer explicitly
   accepts as unavoidable. The default expectation is no direct
   `weft.core.*` imports from `weft.cli.*`.
5. Split `weft/core/ops/managers.py` carefully:
   - move pure runtime mechanics such as detached-launch proof,
     registry polling, and low-level process/bootstrap helpers to a
     permanent `weft/core/*` module with a truthful name
   - move shared manager capabilities (`start`, `stop`, `list`,
     `status`, `serve`) to `weft/commands/manager.py`
   - move `typer.echo`, `typer.Exit`, verbose output emission, and
     warning rendering to `weft/cli/*`
   - after this step, no `typer` import remains in core
6. Rehome `weft/core/types.py`:
   - public/shared app-level dataclasses live in `weft/commands/types.py`
   - any true internal runtime-only types move next to the core code
     that owns them
   - then delete `weft/core/types.py`
7. Delete every remaining module-alias shim. The repo should contain no
   `sys.modules[__name__] = ...` pattern under `weft/commands` or
   `weft/core`.
8. Delete the `weft/core/ops/` directory only after steps 2 through 7
   are complete and imports have been repointed. Do not leave tombstone
   modules behind.
9. Strengthen the import-boundary tests to encode the real final graph:
   - `weft.core.* -> weft.commands.* | weft.cli.* | weft.client.*` forbidden
   - `weft.commands.* -> weft.cli.* | weft.client.*` forbidden
   - `weft.cli.* -> weft.core.*` forbidden
   - `weft.client.* -> weft.core.*` forbidden
   - allowed everywhere only:
     `weft._constants`, `weft._exceptions`, `weft.context`,
     `weft.helpers`
   - any `typer` import outside `weft.cli.*` forbidden

**Stop and re-evaluate if**

- Any `weft.commands.*` module still reads like adapter code after the
  move.
- A proposed permanent `weft/core/*` module exposes application-level
  dataclasses or client-facing contracts.
- `weft.cli.*` or `weft.client.*` still "needs" a direct `weft.core.*`
  import after the audit. That is a missing command-layer helper, not an
  acceptable permanent exception.
- Deleting `weft/core/types.py` seems impossible without reintroducing a
  fifth shared base module. If that happens, stop and justify the new
  module explicitly in the plan before coding further.

**Hard gates**

- `test ! -d weft/core/ops` succeeds.
- `test ! -f weft/core/types.py` succeeds.
- `rg -n "weft/core/ops|weft\\.core\\.ops|weft/core/types.py|weft\\.core\\.types" weft tests integrations`
  returns zero relevant matches.
- `rg -n "sys.modules\\[__name__\\]" weft/commands weft/core` returns
  zero matches.
- `rg -n "import typer|from typer" weft | rg -v "^weft/cli/"` returns
  zero matches.
- `rg -n "from weft\\.core|import weft\\.core" weft/client weft/cli`
  returns zero relevant matches.
- `tests/architecture/test_import_boundaries.py` passes with the final,
  stricter graph.

### Task 4B — Completion sub-plan: clean module ownership and repo organization

**Outcome**

The repo shape has clear ownership, not just a passing import graph. At
the end of this sub-task:

- there is no same-stem split like
  `weft/commands/_manager_bootstrap.py` and
  `weft/core/manager_bootstrap.py`
- there is no duplicated `QueueChangeMonitor` implementation
- `weft.core.taskspec` is a package, not a single catch-all module file,
  and the TaskSpec schema cluster lives under it
- `weft.core.spec_store.py` is either the one canonical named-spec
  resolver or it is rehomed; `weft.commands.specs.py` does not carry a
  second copy of the same lookup rules
- `integrations/weft_django/*` depends on public Weft surfaces only,
  not `weft.commands.*` or `weft.core.*`
- root-level leftovers from the old shape are gone:
  `weft/cli_utils.py` is rehomed under `weft/cli/`,
  `weft/config.py` is deleted, and duplicate `version_callback`
  implementations are removed

Passing the one-way import test is not enough if the same behavior still
lives under multiple names or multiple layers.

**Read first**

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `weft/commands/_manager_bootstrap.py`
- `weft/core/manager_bootstrap.py`
- `weft/commands/_queue_wait.py`
- `weft/core/queue_wait.py`
- `weft/commands/specs.py`
- `weft/core/spec_store.py`
- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/decorators.py`
- `integrations/weft_django/weft_django/conf.py`

**Comprehension check before editing**

Before touching code, the implementer should be able to answer:

1. Which functions in `weft/commands/_manager_bootstrap.py` are just
   wrappers over core helpers, and which ones still own shared
   capability behavior that belongs in `weft.commands.*`?
2. Which helpers in `weft/commands/specs.py` are CLI-only, which are
   shared capability helpers, and which duplicate the canonical
   resolver rules already present in `weft/core/spec_store.py`?
3. Which parts of the current `weft.core.taskspec.py` file are schema,
   which are submission-time materialization helpers, and which should
   stay outside the TaskSpec package entirely?

If those answers are fuzzy, stop and do the ownership audit first.

**Files to touch**

- Delete or rename:
  - `weft/commands/_manager_bootstrap.py`
  - `weft/commands/_queue_wait.py`
  - `weft/core/taskspec.py`
  - `weft/cli_utils.py`
  - `weft/config.py`
- Create or replace:
  - `weft/core/taskspec/`
  - a permanent truthfully named core module for low-level manager
    lifecycle mechanics, such as `weft/core/manager_lifecycle.py`
  - a CLI-local argv helper such as `weft/cli/_argv.py`
- Update:
  - `weft/commands/manager.py`
  - `weft/commands/specs.py`
  - `weft/commands/submission.py`
  - `weft/core/spec_store.py`
  - `weft/core/queue_wait.py`
  - `weft/client/*`
  - `weft/cli/*`
  - `integrations/weft_django/weft_django/*`
  - `tests/architecture/test_import_boundaries.py`
  - `tests/cli/test_rearrange_args.py`
  - TaskSpec and spec-resolution tests affected by the package move

**Required ownership rules**

- Shared manager capabilities live in `weft.commands.manager` and any
  command-private support module with a unique, truthful name. They do
  not live under both `commands` and `core` with the same stem.
- Low-level manager launch and registry mechanics live in a permanent
  `weft.core.*` home with a name that describes the runtime job.
- `QueueChangeMonitor` has one implementation only. `commands` imports
  it from `core`.
- `weft.core.taskspec` is the schema-and-materialization package:
  `__init__.py` re-exports the public surface, while internal modules
  hold the actual code. Keep the import path `weft.core.taskspec`
  stable for callers.
- `weft.core.spec_store.py` is the canonical named-spec resolver unless
  the implementation is explicitly rehomed. `weft.commands.specs.py`
  should layer capability and CLI concerns over that one owner rather
  than reimplement its lookup rules.
- `integrations/weft_django/*` is a public-consumer layer. It may
  import `weft.client`, `weft.ext`, and the four allowed base modules.
  It must not import `weft.commands.*` or `weft.core.*`.

**Implementation**

1. Eliminate the split manager-bootstrap seam.
   - Move shared manager capability helpers into `weft/commands/manager.py`
     or one clearly named command-private support module.
   - Rename the current core bootstrap module to a truthful runtime name
     such as `weft/core/manager_lifecycle.py`, or shrink it until the
     remaining name is clearly core-only.
   - Hard rule: the permanent `commands` and `core` modules must not
     share the same stem for the same concern.
2. Delete `weft/commands/_queue_wait.py` and import
   `QueueChangeMonitor` from `weft.core.queue_wait` everywhere.
   There should be one implementation, not two synced copies.
3. Convert `weft.core.taskspec` into a package.
   - Move the current schema model and re-export surface into
     `weft/core/taskspec/__init__.py`.
   - Move schema-heavy internals into a module such as
     `weft/core/taskspec/model.py`.
   - Move `spec_parameterization.py` and `spec_run_input.py` under the
     `weft/core/taskspec/` package with names that reflect their role.
   - Keep `weft/core/spec_store.py` outside this package unless a later
     review proves it is part of the same reason-to-change cluster.
4. Deduplicate spec resolution.
   - Keep one canonical implementation of stored-spec and builtin-spec
     lookup rules.
   - Delete the duplicated path-resolution helpers from
     `weft/commands/specs.py` when the core owner is in place.
   - Keep CLI-only explicit-path handling, rendering, and mutation in
     `weft.commands.specs.py`.
5. Tighten the integration boundary.
   - Rework `integrations/weft_django/*` so it uses `weft.client`
     rather than `weft.commands.*` or `weft.core.*`.
   - If the integration still needs a helper that currently lives in
     `core`, promote that helper to an intentional public module first
     instead of poking another hole through the boundary.
6. Clean up root-level leftovers.
   - Move `weft/cli_utils.py` into `weft/cli/`.
   - Delete empty `weft/config.py`.
   - Remove the duplicate `version_callback` so there is one CLI
     implementation only.
   - Update `weft/commands/__init__.py` so it describes the current
     shared capability layer truthfully.
7. Extend the architecture guardrails beyond `weft/`.
   - Keep the current one-way graph enforcement inside `weft/`.
   - Add a second rule set for `integrations/weft_django/*`:
     no imports from `weft.commands.*` or `weft.core.*`.
   - Keep the four allowed base modules explicit.

**Stop and re-evaluate if**

- The TaskSpec package split requires a web of late imports or circular
  imports to keep working. That is a sign the split is wrong or too
  coarse.
- `weft.core.spec_store.py` can no longer stay below `commands` without
  importing `weft.commands.*`. If that happens, re-decide the owner
  explicitly before continuing.
- The Django integration needs helper functionality that does not have a
  clean public home yet. Promote it intentionally; do not keep reaching
  into `core`.
- Renaming the core manager-lifecycle module would create churn without
  reducing ambiguity. If that happens, delete the command-side same-stem
  helper instead and keep the surviving name truthful.

**Hard gates**

- `test ! -f weft/commands/_manager_bootstrap.py` succeeds.
- `test ! -f weft/commands/_queue_wait.py` succeeds.
- `test ! -f weft/core/taskspec.py` succeeds.
- `test -d weft/core/taskspec` succeeds.
- `test ! -f weft/cli_utils.py` succeeds.
- `test ! -f weft/config.py` succeeds.
- `rg -n "def version_callback" weft/cli` returns one relevant match.
- `rg -n "from weft\\.commands|import weft\\.commands|from weft\\.core|import weft\\.core" integrations/weft_django/weft_django`
  returns zero relevant matches.
- `rg -n "from weft\\.commands\\._manager_bootstrap|import weft\\.commands\\._manager_bootstrap" weft tests integrations`
  returns zero relevant matches.
- `rg -n "from weft\\.cli_utils|import weft\\.cli_utils|from weft\\.config|import weft\\.config" weft tests integrations`
  returns zero relevant matches.
- `tests/architecture/test_import_boundaries.py` passes with the
  expanded integration guardrails.

### Task 4C — Completion sub-plan: collapse the manager lifecycle seam to the proper shape

**Outcome**

Manager lifecycle ownership is clear and one-way:

- `weft.cli.*` imports manager capability only from `weft.commands.*`
- `weft.client.*` imports manager capability only from `weft.commands.*`
- `weft.commands.*` imports low-level manager mechanism from `weft.core.*`
- `weft.core.*` owns the runtime launcher, registry polling, pid liveness,
  and stop/wait mechanics

At the end of this sub-task:

- `weft/commands/_manager_lifecycle.py` does not exist
- `weft/commands/manager.py` is the one command-layer owner of manager
  capability
- there is no broad command-side re-export of underscore-prefixed core
  helpers, stdlib modules, `logger`, or queue-wait internals
- tests patch either `weft.commands.manager` for capability behavior or
  `weft.core.manager_runtime` for mechanism behavior; they do not patch a
  mirror module that republishes private internals
- stale dead code such as `weft/commands/handlers.py` is removed rather
  than kept as another compatibility shell

This task is about ownership, not just import legality. A thin mirror of
`weft.core.manager_runtime` under `weft.commands` is not an acceptable
end state.

**Read first**

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `weft/core/manager_runtime.py`
- `weft/commands/manager.py`
- `weft/commands/_manager_lifecycle.py`
- `weft/commands/serve.py`
- `weft/commands/submission.py`
- `weft/commands/system.py`
- `weft/cli/run.py`
- `tests/commands/test_manager_commands.py`
- `tests/commands/test_run.py`
- `tests/commands/test_serve.py`

**Comprehension check before editing**

Before touching code, the implementer should be able to answer:

1. Which manager operations are true command-layer capabilities
   (`start`, `serve`, `stop`, `status`, `list`, durable manager ensure
   after submission), and which are low-level launch or registry
   mechanics?
2. Which current imports of `weft.commands._manager_lifecycle` are really
   capability imports and which are only using it as a convenient path to
   a private core helper?
3. Which tests should patch `weft.commands.manager` versus
   `weft.core.manager_runtime` to keep the capability/mechanism split
   honest?

If those answers are fuzzy, stop and do the owner map first.

**Files to touch**

- Delete:
  - `weft/commands/_manager_lifecycle.py`
  - `weft/commands/handlers.py`
- Update:
  - `weft/commands/manager.py`
  - `weft/commands/serve.py`
  - `weft/commands/submission.py`
  - `weft/commands/system.py`
  - `weft/cli/run.py`
  - `weft/core/manager_runtime.py`
  - `tests/commands/test_manager_commands.py`
  - `tests/commands/test_run.py`
  - `tests/commands/test_serve.py`
  - `tests/architecture/test_import_boundaries.py`
  - docs in Task 6 that still map manager ownership or package shape
    incorrectly

**Required ownership rules**

- `weft.commands.manager` is the only command-layer owner of manager
  capability. If another command module needs manager behavior, it
  imports a narrow function from `weft.commands.manager`, not from a
  second lifecycle shell.
- `weft.commands.serve` may stay as a tiny command module if it adds
  distinct CLI-command behavior. If it is only a one-line forwarder, fold
  it into `weft.commands.manager`.
- `weft.core.manager_runtime` owns mechanism only. It may expose a small
  intentional command-consumable API, but it must not require the
  command layer to mirror dozens of underscore-prefixed helpers just to
  use it.
- `weft.commands.*` must not re-export `logger`, `time`,
  `QueueChangeMonitor`, `iter_queue_json_entries`, or other raw runtime
  implementation helpers as part of the manager capability seam.
- `weft/commands/handlers.py` is not preserved as compatibility glue.
  Either its functionality already lives elsewhere or it should be moved
  into the real owner before the file is deleted.

**Implementation**

1. Delete `weft/commands/_manager_lifecycle.py`.
   - Do not replace it with another same-purpose mirror file.
   - Repoint all current import sites to the real command-layer owner.
2. Make `weft.commands.manager` the shared manager capability surface.
   - Move or expose the narrow command-layer functions that other command
     modules actually need:
     `start_manager`, `serve_manager`, `stop_manager`,
     `list_managers`, `manager_status`, and any command-private helpers
     that support submission-time manager ensure.
   - Keep the surface small and truthful. Do not simply rename the old
     re-export list.
3. Separate capability from mechanism in the remaining API.
   - If `weft.commands.manager` still needs low-level helpers such as TID
     generation, detached launch settlement, or registry lookup, either
     keep them fully internal to `weft.commands.manager` or promote a
     small intentional API in `weft.core.manager_runtime`.
   - Do not keep cross-layer imports of underscore-prefixed core helpers
     as the long-term contract.
4. Repoint dependent modules.
   - `weft/commands/submission.py` should depend on a narrow manager
     capability or helper owned by `weft.commands.manager`.
   - `weft/commands/system.py` should depend on `weft.commands.manager`
     for manager-record access.
   - `weft/cli/run.py` should depend on `weft.commands.manager` and
     `weft.commands.submission`, not on a command-private lifecycle seam.
   - `weft/commands/serve.py` should either import from
     `weft.commands.manager` or be removed if it has no remaining value.
5. Clean up tests to patch the right owner.
   - Capability-level tests patch `weft.commands.manager`.
   - Mechanism-level tests patch `weft.core.manager_runtime`.
   - No tests should import or patch `weft.commands._manager_lifecycle`
     after this task.
6. Delete `weft/commands/handlers.py`.
   - If any display helper still matters, move it into
     `weft.commands.validate_taskspec` first.
   - Do not keep a dead shell module around because it once matched the
     old package shape.
7. Update the architecture docs and agent guidance in Task 6 so they no
   longer mention `weft/cli.py`, `weft/commands/run.py`,
   `weft/core/taskspec.py`, or a command-side manager lifecycle seam that
   mirrors core internals.

**Stop and re-evaluate if**

- `weft.commands.manager` starts to accumulate raw process-launch,
  queue-poll, or startup-log parsing details that clearly belong in
  `core`.
- The only way to keep tests passing is to reintroduce a broad re-export
  module under a different name.
- `weft.core.manager_runtime` cannot support the needed command-layer
  calls without exposing a huge pseudo-public surface. If that happens,
  split the core runtime by responsibility first instead of forcing one
  oversized module to do both jobs.
- Deleting `weft/commands/handlers.py` reveals a real external contract
  that was not documented anywhere. If that happens, stop and decide
  explicitly whether to keep or replace that contract.

**Hard gates**

- `test ! -f weft/commands/_manager_lifecycle.py` succeeds.
- `test ! -f weft/commands/handlers.py` succeeds.
- `rg -n "from weft\\.commands\\._manager_lifecycle|import weft\\.commands\\._manager_lifecycle|_manager_lifecycle" weft tests integrations`
  returns zero relevant matches.
- `rg -n "logger|time|QueueChangeMonitor|iter_queue_json_entries" weft/commands/manager.py`
  returns zero relevant manager-seam re-export matches.
- `rg -n "from weft\\.core\\.manager_runtime import _|import weft\\.core\\.manager_runtime as" weft/commands weft/cli`
  returns zero relevant long-term contract matches.
- `tests/commands/test_manager_commands.py`,
  `tests/commands/test_run.py`, and `tests/commands/test_serve.py` pass.
- `tests/architecture/test_import_boundaries.py` still passes.

### Task 5 — Migrate `weft_django` and fix request-id capture at enqueue time

**Outcome**

`weft_django` becomes a client consumer, not a command-module consumer.
It also stops depending on `weft.core.*`. The request-id capture bug is
fixed in a way the tests can actually prove. `weft_status` uses the new
task namespace. SSE uses `Task.follow()`.

**Files to touch**

- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/decorators.py`
- `integrations/weft_django/weft_django/views.py`
- `integrations/weft_django/weft_django/sse.py`
- `integrations/weft_django/weft_django/management/commands/weft_status.py`
- `integrations/weft_django/tests/test_weft_django.py`
- Fixture-project helpers needed by the tests

**Implementation**

1. Replace imports from `weft.commands.*` and `weft.core.*` with public
   `weft.client` and other intentional public surfaces.
2. Migrate registered-task submission, spec-reference submission, and
   pipeline-reference submission to the new client surface.
3. Replace or shrink `WeftSubmission`. Default target: delete it and
   return `Task`. Keep only a thin adapter if a Django-side contract
   actually requires it.
4. Change `WeftDeferredSubmission.submission` to `.task: Task | None`.
5. In `enqueue_on_commit`, build the envelope before registering the
   `on_commit` callback. The callback submits the already-built envelope.
6. Update SSE and status management to use the new client surface.
7. Rename any remaining Django-side `input=` call sites to `payload=`.

**Request-id test requirement**

The test must assert the actual captured request id, not just that the
task submitted. The current weak assertion is not enough. Add a fixture
task or fixture helper that surfaces the request id from the built
envelope in the task result, then assert the exact value:

```python
def test_request_id_captured_at_enqueue_time(...) -> None:
    ...
    provider_mod.set_current("req-abc-123")
    with transaction.atomic():
        deferred = echo_request_id.enqueue_on_commit("x")
        provider_mod.set_current(None)

    result = deferred.task.result(timeout=30.0)
    assert result.status == "completed"
    assert result.value["request_id"] == "req-abc-123"
```

If the existing fixture task cannot expose that cleanly, add a new
fixture task that does. Do not settle for `assert deferred.task is not None`.

**Done signal**

- `rg -n "from weft.commands|import weft.commands|from weft.core|import weft.core" integrations/weft_django`
  returns zero relevant matches.
- `pytest integrations/weft_django/tests -q` passes.
- The request-id test asserts the actual captured value.

### Task 6 — Update the specs, agent guidance, and plan index to match what shipped

**Outcome**

The specs and agent guidance describe the real package split, the real
client surface, the real parity model, and the real import rules. The
docs stop describing the old Django-only client or the temporary
`weft/core/ops` scaffold.

**Files to touch**

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/plans/README.md`

**Required doc updates**

1. `AGENTS.md`
   - Update the package map so `weft/commands/*` is the shared
     capability layer and `weft/cli/*` is the CLI adapter package.
   - Replace stale file pointers such as `weft/cli.py`,
     `weft/commands/run.py`, and `weft/core/taskspec.py` with the real
     package-based implementation homes.
   - Add the one-way import rules and the four allowed base modules.
   - Explicitly ban `typer` imports outside `weft.cli.*`.
2. `docs/agent-context/engineering-principles.md`
   - Add the same layer contract in durable agent guidance.
   - State that temporary migration scaffolds such as `weft/core/ops/*`
     must be deleted once the final package split lands.
3. `00-Overview_and_Architecture.md`
   - Update the architecture overview and implementation mapping notes
     to show `core <- commands <- cli/client`.
   - Remove stale references to transitional same-stem helper pairs or
     root-level CLI utilities that no longer exist.
4. `02-TaskSpec.md`
   - Update implementation mapping notes so `weft.core.taskspec` is a
     package and name the stable public import surface.
   - Keep the schema versus named-spec-store boundary explicit.
5. `03-Manager_Architecture.md`
   - Update implementation mapping notes so the shared manager
     capability surface and the low-level runtime lifecycle module have
     distinct permanent homes and distinct names.
   - State plainly that there is no command-side mirror module that
     republishes private core lifecycle helpers.
6. `13C-Using_Weft_With_Django.md`
   - Update §DJ-2.1 to the new surface:
     hot path, noun namespaces, nested `queues.aliases`, and documented
     non-parity exceptions.
   - Update §DJ-2.2 to the public types and the `Task` handle.
   - Update §DJ-8.2 to `payload=`.
   - Update §DJ-10.1 to describe `Task` as the canonical handle and to
     describe any surviving Django adapter truthfully.
   - Update §DJ-14.1 to explicitly forbid imports from `weft.commands.*`
     and `weft.core.*`.
   - Remove stale references to `weft.commands.run`.
7. `10-CLI_Interface.md`
   - Add a short "Python parity" section that points at the client
     surface and names the documented non-parity exceptions.
8. `11-CLI_Architecture_Crosswalk.md`
   - Update the implementation mapping so CLI adapters live in
     `weft.cli.*` and shared capability behavior lives in
     `weft.commands.*`.
   - Remove stale references to deleted helper shells such as
     `weft.commands._manager_lifecycle` or any old same-stem bootstrap
     modules.
   - Document the surviving private helper modules truthfully after the
     ownership cleanup.
9. `docs/plans/README.md`
   - Add this plan.

**Done signal**

- The specs and agent guidance match the shipped code.
- `rg "weft/core/ops|weft.core.ops" AGENTS.md docs/specifications docs/agent-context`
  returns zero relevant matches.
- `rg "input=" docs/specifications/13C-Using_Weft_With_Django.md`
  returns zero relevant matches.
- `rg "Python parity" docs/specifications/10-CLI_Interface.md` matches.

### Task 7 — Remove the legacy forwarders and leave one public client surface

**Outcome**

The new client surface stands alone. The migration-only top-level methods
on `WeftClient` are deleted. `Task` plus the namespaces are the only
public way in.

**Files to touch**

- `weft/client/_client.py`
- `weft/client/__init__.py`
- Legacy-only tests

**TDD prompt**

```python
def test_legacy_forwarders_are_gone() -> None:
    from weft.client import WeftClient

    for name in (
        "submit_taskspec",
        "submit_spec_reference",
        "submit_pipeline_reference",
        "wait",
        "events",
        "status",
        "result",
        "stop",
        "kill",
    ):
        assert not hasattr(WeftClient, name)
```

**Implementation**

1. Grep for remaining legacy call sites across `weft`, `integrations`,
   and `tests`. Migrate or delete them.
2. Delete the forwarders from `WeftClient`.
3. Keep `from_context(...)` if it is still part of the intended public
   constructor contract. This plan does not require removing it.
4. Ensure `weft.client` still does not re-export `SubmittedTask`.

**Done signal**

- Full test suite passes.
- No legacy forwarders remain on `WeftClient`.

### Task 8 — Final verification and review sign-off

**Outcome**

All gates pass. The review loop is complete. The plan can be marked
`completed` only after the code, docs, and tests all match.

**Done signal**

- Verification commands below all pass.
- Independent review signs off on Task 1, Task 4, and Task 5.
- The plan status is updated only after the landing is complete.

---

## Testing Plan

### Discipline

- Broker-backed tests only for queue-touching behavior.
- No mocks of `submit_spawn_request`, `Queue`, `WeftContext`,
  `build_context`, or `WeftTestHarness`.
- Red-green TDD per task.
- Parity tests focus on shared-op identity where appropriate and on
  observable behavior everywhere else. Do not write brittle
  `sourcefile == "ops.py"` tests.

### Required test files

- `tests/architecture/test_import_boundaries.py`
- `tests/core/test_ops_shared.py`
- `tests/core/test_exceptions.py`
- `tests/core/test_client.py`
- Updated `integrations/weft_django/tests/test_weft_django.py`

### Coverage requirements

`tests/core/test_ops_shared.py`

- One structural test per command group proving that the command adapter
  is wired to the shared op, without relying on source-file path checks.
- One broker-backed parity test per command group:
  tasks/results, queues, managers, specs, system.

`tests/architecture/test_import_boundaries.py`

- AST-based import boundary checks for:
  `core -> commands/cli/client` forbidden,
  `commands -> cli/client` forbidden,
  `cli -> core` forbidden,
  `client -> core` forbidden.
- Explicit allowlist for:
  `weft._constants`, `weft._exceptions`, `weft.context`,
  `weft.helpers`.
- A `typer` placement test that fails if `typer` appears outside
  `weft.cli.*`.
- A second guardrail for `integrations/weft_django/*` that forbids
  imports from `weft.commands.*` and `weft.core.*`.

`tests/core/test_client.py`

- Public import and scaffold tests from Task 2.
- Hot-path submission tests for `submit`, `submit_spec`,
  `submit_pipeline`, and `submit_command`.
- Handle tests for `snapshot`, `result`, `events`, `follow`, `stop`,
  `kill`, and `submitted_at_ns == int(tid)`.
- One happy-path test per namespace method family.

`integrations/weft_django/tests/test_weft_django.py`

- Existing Django coverage still passes.
- Add the missing rejection and authz tests already identified.
- Add the strengthened request-id test that asserts the actual captured
  request id value.

### Anti-pattern reminders

Reviewers should reject:

- Tests that prove only where code lives on disk instead of what it does.
- Tests that patch shared ops internals to force reachable conditions.
- Tests that use sleeps for correctness instead of the harness and the
  real wait helpers.
- Tests that claim to prove the request-id fix without asserting the
  captured request id.

---

## Verification

Run in order. Stop at the first failure.

```bash
. ./.envrc

# Phase 0 gate
./.venv/bin/python -m pytest tests/core/test_ops_shared.py -q
./.venv/bin/python -m pytest tests/core tests/cli -q

# Types and scaffold gate
./.venv/bin/python -m pytest tests/core/test_exceptions.py tests/core/test_client.py -q \
  -k "importable or public or legacy"

# Full client gate
./.venv/bin/python -m pytest tests/core/test_client.py -q

# Package-boundary gate
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q

# Scaffold deletion gate
test ! -d weft/core/ops
test ! -f weft/core/types.py
! rg -n "weft/core/ops|weft\\.core\\.ops|weft/core/types.py|weft\\.core\\.types" \
    weft tests integrations
! rg -n "sys.modules\\[__name__\\]" weft/commands weft/core
! rg -n "import typer|from typer" weft | rg -v "^weft/cli/"
! rg -n "from weft\\.core|import weft\\.core" weft/client weft/cli

# Ownership cleanup gate
test ! -f weft/commands/_manager_bootstrap.py
test ! -f weft/commands/_queue_wait.py
test ! -f weft/core/taskspec.py
test -d weft/core/taskspec
test ! -f weft/cli_utils.py
test ! -f weft/config.py
test "$(rg -n "def version_callback" weft/cli | wc -l | tr -d ' ')" = "1"
! rg -n "from weft\\.commands|import weft\\.commands|from weft\\.core|import weft\\.core" \
    integrations/weft_django/weft_django

# Django migration gate
./.venv/bin/python -m pytest integrations/weft_django/tests -q

# Final suite
./.venv/bin/python -m pytest -q

# Static checks
./.venv/bin/python -m mypy weft integrations/weft_django/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m ruff check weft integrations/weft_django tests
./.venv/bin/python -m ruff format --check weft integrations/weft_django tests

# Package build sanity
(cd integrations/weft_django && uv build)
```

Success means:

- `weft.commands.*` is the shared capability layer.
- `weft.client.*` and `weft.cli.*` are adapters over `weft.commands.*`.
- `weft/core/ops/` is deleted, not merely unused.
- `weft/core/types.py` is deleted or explicitly justified as pure core.
- No module-alias scaffolding remains.
- `weft.client.*` and `weft.cli.*` import no `weft.core.*`.
- `weft/commands/_manager_bootstrap.py` and `weft/commands/_queue_wait.py`
  are deleted.
- `weft.core.taskspec` is a package, not a single file.
- `weft/cli_utils.py` and empty `weft/config.py` are gone.
- Every in-scope row in the parity matrix has a live client method.
- `weft_django` imports no `weft.commands.*` or `weft.core.*`.
- The request-id fix is proven by value, not by mere submission success.
- Specs, agent guidance, and code tell the same story.

---

## Out Of Scope

- Async client APIs.
- `shell=True` command execution semantics for the client.
- Queue-format changes, message-envelope shape changes, or broker-backend
  changes.
- CLI grammar changes.
- HTTP control endpoints.
- Persistent session or agent-session client APIs.
- Any Django contract change beyond the client migration, the
  `payload=` rename, and the request-id capture timing fix.
