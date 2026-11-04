# Weft System Overview and Architecture

Weft is a durable task runner built on SimpleBroker. The design target is not
massive distributed orchestration. The target is a small, reliable, observable
process system that feels as direct as Unix tools while surviving beyond a
single shell session.

_Implementation mapping_: CLI adapters in `weft/cli/`; shared capability code
in `weft/commands/`; public Python client in `weft/client/`; context
resolution in `weft/context.py`; manager runtime in `weft/core/manager.py`,
`weft/core/manager_runtime.py`, and `weft/manager_process.py`; task runtime
in `weft/core/tasks/`; runner plugin contracts and resolution in
`weft/_runner_plugins.py` and `weft/ext.py`; built-in runner implementations in
`weft/core/runners/`.

See also:

- planned companion:
  [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)
- pipeline status and composition contract:
  [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)

## Executive Summary

The current system is organized around four ideas:

- queues are the only durable coordination surface
- the manager is a task-shaped dispatcher, not a separate control plane
- all externally visible work stays inspectable through stable CLI verbs
- operator trust comes from visibility, not hidden automation

That shape is deliberate. It keeps the system easy to inspect with normal queue
and process tools, and it avoids a second hidden state store that would drift
from runtime truth.

## Core Mission

Weft exists to coordinate commands, Python callables, and agent-backed work
that may take milliseconds or minutes, while preserving:

- durable submission
- observable lifecycle
- explicit control
- backend-neutral broker targeting

The goal is not "workflow engine first." The goal is "durable task/process
coordination first." Higher-order composition is allowed, but it should still
look and behave like tasks rather than like a separate platform.

That boundary also applies to agent work. Weft is the execution substrate for
higher-level intelligence and orchestration, not the agent-management layer
itself. The system should make execution easy, observable, and explicitly
validatable without quietly taking ownership of higher-level orchestration
policy.

## Product Identity

Weft owns the runtime substrate:

- task lifecycle
- queue-visible state
- manager dispatch
- runner selection and isolation
- resource limits
- task-shaped composition
- agent execution as one more task target

This means agent support and limited runners are not foreign to Weft. They are
core expressions of the same design. An agent is a task. A dangerous task in a
restricted container is still a task. Runner plugins and agent adapters extend
the same execution model rather than creating a second product.

## Product Boundary

What fits in Weft:

- agents as tasks
- limited runners such as Docker or sandboxed runtimes
- explicit composition over tasks
- enough runtime-specific knowledge to make common runs work or fail clearly
- explicit optional helpers that smooth a real execution path without becoming
  required for correctness

What does not fit in Weft:

- hidden setup or probing on ordinary execution paths
- turning Weft into the source of truth for provider ecosystem state
- broad agent-specific management logic that is not needed to execute a task
- convenience features that silently author project policy
- a second control plane for agent setup, health, or lifecycle outside the task
  model

The rule is simple: Weft may help you run work. It should not try to manage the
whole world around that work.

## Why SimpleBroker

SimpleBroker fits Weft because it already provides the persistence and queue
primitives Weft needs:

- queues appear on first use
- empty queues disappear again
- message IDs are durable, ordered, and unique
- the same broker abstraction works across file-backed and non-file-backed
  backends

That matters because Weft should spend its complexity budget on process
lifecycle, task semantics, and observability, not on rebuilding a broker.

## Design Principles

- **Queue-first**: caller-facing coordination goes through durable queues.
- **Task-shaped runtime**: managers and user work share the same outer task
  model.
- **Stable verbs**: `run`, `status`, `result`, `list`, `stop`, and `kill`
  remain the center of gravity even as the system grows.
- **Partial immutability**: execution config is frozen after TaskSpec creation;
  runtime state stays mutable.
- **Forward-only state**: lifecycle state is append-only and terminal states do
  not roll back.
- **Visible failure**: failures stay inspectable through state logs and reserved
  queues.
- **Backend-neutral targeting**: normal runtime paths operate on broker targets,
  not on SQLite-only path assumptions.
- **Operator intent over magic**: background behavior such as autostart exists
  only when the project explicitly configures it.

## Current Product Shape

The current public surface is:

- task and pipeline execution through `weft run` for commands, Python
  callables, TaskSpecs, and pipelines
- stored task specs and stored pipeline specs
- shipped builtin task helpers resolved only through explicit spec surfaces
  such as `weft run --spec`, `weft spec ...`, and `weft system builtins`
- pipeline execution through `weft run --pipeline`
- manager lifecycle commands
- queue passthrough commands
- result, status, and task-control commands
- first-class agent tasks through `spec.type="agent"`
- first-party restricted runners, including the Docker runner's current
  command lane and its narrow one-shot Docker-backed `provider_cli` agent lane

The reason pipelines live under `weft run --pipeline` rather than under a
separate top-level verb is architectural: composition is part of task
execution, not a second workflow language.

## System Architecture

### Runtime Layers

```text
CLI and client adapters
        |
        v
Shared command/client capabilities and context resolution
        |
        v
Manager dispatcher and task launch
        |
        v
Task runtime and runner plugins
        |
        v
SimpleBroker queues and broker target
```

_Implementation mapping per layer_:

- **Public Python surfaces**: application client and task construction in
  `weft/client/`, CLI-equivalent capabilities in `weft/commands/`, and extension
  contracts in `weft/ext.py`; `weft/__init__.py` exposes package metadata only.
  See [Python API surfaces](14-Python_API_Surfaces.md) [PY-1].
- **CLI and client adapters**: `weft/cli/`, `weft/client/`
- **Shared application capabilities**: `weft/commands/`
- **Context resolution**: `weft/context.py`
- **Manager dispatcher**: `weft/core/manager.py`,
  `weft/core/manager_runtime.py`, `weft/manager_process.py`
- **Task runtime**: `weft/core/tasks/base.py`,
  `weft/core/tasks/consumer.py`, `weft/core/tasks/runner.py`,
  `weft/core/runners/sessions.py`
- **Runner plugins**: `weft/core/runners/`, `weft/_runner_plugins.py`,
  `weft/ext.py`
- **Queues and persistence**: SimpleBroker broker target and queue API

**Single current contract.** Weft does not preserve alternate import paths,
CLI spellings, control envelopes, persisted record shapes, dependency APIs,
or accepted schema fields solely for backward compatibility. A contract
change updates all Weft-owned producers and consumers in one coordinated
release. Durable old data is handled by a bounded forward migration; normal
readers and writers use only the current shape after that migration. Multiple
documented forms may meet at one boundary normalizer only when all forms are
part of the current contract and immediately become one internal
representation.

The public Python adapter is `weft.client`. Internal and command code imports
its owning leaf modules directly. The `weft` package root exposes package
metadata only; `weft.commands` and `weft.core` do not preserve symbol
inventories from older module layouts.

### Manager Model

The manager is the dispatcher for spawn requests. It watches
`weft.spawn.requests`, expands a runtime TaskSpec, and launches child work.

The important part is the shape, not a class taxonomy:

- there is one canonical manager runtime per context
- the manager uses the same queue and control conventions as task work
- the spawn-request message ID becomes the child task TID
- startup, foreground serve, and lifecycle observation share one manager
  bootstrap path

This is why the architecture talks about "managers are tasks" rather than about
separate scheduler or pool objects. The outer lifecycle is intentionally
uniform.

## Observability and Security

Weft assumes user-level trust, not hostile multi-tenancy. The security model is
therefore mostly about explicitness and containment:

- all significant lifecycle changes are written to `weft.log.tasks`
- process titles expose enough identity for normal OS tooling
- reserved queues keep failed in-flight work visible
- broker/project context remains explicit so operators know which runtime they
  are touching

The reason this matters is operational clarity. When something fails, the
system should leave behind enough durable evidence that the operator can answer
what happened without guessing.

### Trust Domain

Weft operates within one operator trust domain. It trusts whatever the
operating system let the current user place or reach; the operating system,
filesystem permissions, and database grants decide who can. This is
SimpleBroker's security model, adopted: inputs that choose where the boundary
sits are trusted by placement, inputs that shape names inside it are checked
for shape, and intent is never inspected. The identity statement is
[WT-2a.11] in `docs/program-theory.md`; the sibling account is the "Security"
and "General security considerations" sections of SimpleBroker's
configuration guide.

**Trusted by placement.** Weft uses these as given:

- the broker target and its location: the SQLite database (by default
  `.weft/broker.db` under the project root) and the directory that holds it,
  or the PostgreSQL database and schema. Whoever can write the target can
  write any queue, including `weft.spawn.requests` and every `T{tid}.ctrl_in`.
- the Weft-scoped project config, by default `.weft/broker.toml`. Project
  scoping is on by default (`WEFT_PROJECT_SCOPE`), so the nearest such file at
  or above the discovery start directory selects the backend, target, and
  backend options for every command run beneath it, including absolute paths,
  parent paths, and remote backends. Do not run Weft beneath a project config
  you would not trust. An explicit root (`--context` or `WEFT_CONTEXT`) is
  trusted the same way.
- the Weft metadata directory: `config.json`, stored task and pipeline specs,
  and autostart manifests ([TS-1.2]) when autostart is enabled.
- the commands, Python callables, and agent runtimes a TaskSpec names. A
  TaskSpec that validates may run any program the current user can run.

**Checked for shape.** Weft validates shape for correctness and portability,
not as a trust boundary:

- TaskSpecs: every public TaskSpec model rejects unknown fields ([TS-1]), and
  `spec` and `io` are immutable after creation.
- control messages: the strict envelope of [QUEUE.2a]; a rejected row is
  acknowledged, never obeyed.
- task IDs and message IDs: canonical 19-digit decimal identifiers
  ([SB-0.2]).
- queue names: SimpleBroker validates every queue name; Weft composes its own
  names from validated task IDs.
- names Weft derives: `WEFT_DIRECTORY_NAME` must be a single directory name,
  not a path; process-title segments keep only letters, digits, `_`, and `-`
  ([OBS.4]); PostgreSQL owner labels match `[!-~]{1,54}` ([SB-0.4]).

**Recognized as its own.** SimpleBroker records a magic string and a schema
version in its broker metadata. It refuses a database whose recorded magic
string is not its own or whose recorded schema version is newer than it
supports. A database with no broker metadata is not refused: SimpleBroker
installs its metadata and tables there and leaves any existing tables in
place. Weft accepts a
stored task or pipeline spec only when it validates as one; an existing
`.weft/config.json` that is not a JSON object is an error that Weft reports
without rewriting the file ([SB-0.4]); Monitor tables are versioned and
verified before use ([SB-0.4a]). Recognition protects correctness. It says
nothing about who wrote the artifact.

**Bounds on effect, not trust.** Resource limits ([RM-0]) and restricted
runners, such as the first-party Docker, macOS sandbox, and Microsandbox
runner extensions ([CC-3.1], [TS-1.3]), bound what a task may do. They do not
decide whether a caller is trusted. Resource limits are a cooperative
operational control, not isolation against an adversarial task.

**Never.** Weft does not:

- authenticate in band: control messages, spawn requests, and owner labels
  carry no credential or signature, and owner labels confer no authority.
  Anyone who can write a queue can send to it.
- treat a sanitizer as a boundary: commands run as argv lists, never through
  a shell (`shell=True` is rejected), and message content is not inspected
  for intent ([ALT-WT-014] in `docs/program-theory.md`).
- infer confidentiality or integrity from file mode, ownership,
  parent-directory permissions, or ACLs, and it does not chmod, tighten,
  repair, or preflight them ([CTX.5]). Weft creates files with ordinary
  defaults (on POSIX, the process umask applies).
- provision operating-system users, groups, ACLs, PostgreSQL roles, or
  grants.

### Shared Access

**SQLite with more than one OS user.** Every intended writer needs effective
read and write access to the broker database and to every companion file
SimpleBroker creates or may create (its journal, WAL, shared-memory, lock,
status, and vacuum-lock files). The containing directory must let every
writer traverse it and create, replace, and remove entries. Weft adds entries
of its own: `config.json` in the metadata directory and the same-directory
temporary files that replace it atomically, `outputs/`, the logs directory
(`WEFT_LOGS_DIR`, by default `.weft/logs`), and `autostart/` when enabled.
Shared writers need the same rights there. On POSIX that means suitable file
permissions or ACLs plus directory write and execute permission; on Windows,
the equivalent file and directory ACL rights. Operators own placement,
ownership, groups or principals, ACLs, and (on POSIX) the umask. For
sensitive contents, use a directory restricted to the intended users.

**PostgreSQL.** Database grants decide who can read and write queues. The
database must already exist: SimpleBroker initialization creates its managed
schema and tables inside it, and the TaskMonitor creates and migrates only
its own Monitor tables in an initialized broker database ([SB-0.4a]). The
role Weft connects as needs the privileges those objects require. Every role
that can write the broker schema shares one trust domain with every Weft
process that uses it. Prefer `WEFT_BACKEND_PASSWORD`, or the driver's own
convention such as libpq's `PGPASSWORD`, over a password embedded in
`.weft/broker.toml`. Weft shows targets through SimpleBroker's redacted
display form. It strips a URL-userinfo password from the context keys and
runtime identities it derives ([MF-1]); a password given as a libpq
`password=` keyword or in a URI query stays in those derived identities.

## Scope Boundaries

The canonical architecture spec covers what Weft does now. Planned extensions,
aspirational performance targets, and future orchestration layers live in the
adjacent companion doc:

- [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)

That split is deliberate. The canonical file answers "what exists and why."
The companion file answers "what may exist later."

## Related Documents

- retired: 2026-08-10-canonical-contract-and-dead-code-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-29-import-boundary-remediation-plan — source 396c3b85; see docs/plans/README.md
- [Docs and the broker boundary](../plans/2026-11-02-docs-and-broker-boundary-plan.md)
- [`01-Core_Components.md`](01-Core_Components.md)
- [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)
