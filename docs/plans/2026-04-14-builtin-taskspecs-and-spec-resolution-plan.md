# Builtin TaskSpecs and Explicit Spec Resolution Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

This plan adds a small builtin TaskSpec surface to Weft and uses it to ship a
real delegated-agent probe helper as an ordinary Weft task. The goal is not to
create a second control plane. The goal is to use Weft's existing reusable task
runner for a checked-in helper that is both useful and illustrative.

This is a deliberate CLI contract change. A recent doc-alignment slice locked
`weft run --spec` to file-only in that slice. This plan intentionally changes
that contract to `NAME|PATH` for task specs by adopting the same explicit
resolution model already used by `weft run --pipeline`, with no interaction
with bare command lookup.

## Scope Lock

Implement exactly this slice:

1. add a read-only builtin TaskSpec namespace shipped with Weft source
2. add one builtin helper task for delegated-agent probing and
   `.weft/agents.json` population
3. change explicit spec resolution so `weft run --spec` accepts `NAME|PATH`
4. resolve stored task specs in this order:
   - explicit existing file path
   - project-local stored task spec in `.weft/tasks/`
   - builtin task spec shipped with Weft
5. keep local stored specs authoritative over builtin specs with the same name
6. keep bare `weft run foo` command execution unchanged
7. keep builtin specs read-only; do not copy them into `.weft/` by default
8. update docs and tests so the new contract is explicit

Do not implement any of the following in this slice:

- implicit spec-name lookup for bare `weft run foo`
- builtin-spec copying during `weft init`
- a package manager or sync system for builtin specs
- hidden startup probing or background probe sidecars
- forced overwrite of explicit user-authored `.weft/agents.json` entries
- a broad builtin library; ship the minimum needed to prove the pattern

If the work starts pushing toward global command-name interception, builtin
installation policy, or background maintenance jobs, stop and split a follow-on
plan.

## Goal

Use Weft itself to ship a concrete, reusable helper task:

- a builtin probe task that uses isolated synthetic probe helpers
- writes missing delegated executable defaults into `.weft/agents.json`
- reports what it found in a normal task result

and make that helper discoverable and runnable through explicit spec
resolution, not through ambient startup behavior.

## Design Position

The core design calls for this slice are:

- builtin helpers should be normal checked-in TaskSpecs, not privileged
  bootstrap code
- builtin TaskSpecs should be read-only and versioned with Weft
- local project specs should shadow builtin specs of the same name
- explicit `--spec` is the right entrypoint for spec-name resolution
- bare command execution must remain a command-execution surface, not a mixed
  command-or-spec surface
- `.weft/agents.json` remains explicit project config; the builtin probe helper
  may fill missing entries by default but must not silently replace explicit
  user choices

This keeps Weft in its lane:

- Weft provides a reusable task substrate and a small set of concrete builtins
- the operator still chooses when to run helper tasks
- provider setup and auth remain outside Weft core

## Source Documents

Primary specs:

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], [CLI-1.4], [CLI-1.4.1]
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
  [CLI-X1]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-7]

Relevant earlier plans:

- [`docs/plans/2026-04-13-cli-surface-doc-and-help-alignment-plan.md`](./2026-04-13-cli-surface-doc-and-help-alignment-plan.md)
  - This plan intentionally supersedes the "file-only `--spec`" constraint from
    that slice
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
  - The builtin probe helper should reuse the isolated probe helpers and
    project-local agent settings boundary introduced there

Source spec note:

- there is no current spec section that defines a builtin TaskSpec namespace
  shipped with Weft source; this plan includes that doc alignment

## Context and Key Files

Likely touched files:

- `weft/commands/run.py`
- `weft/commands/specs.py`
- `weft/cli.py`
- `weft/context.py` only if current helpers are insufficient
- new package/module(s) under `weft/builtins/`
- new builtin task spec JSON under `weft/builtins/tasks/`
- `weft/core/agents/provider_cli/probes.py`
- `weft/core/agents/provider_cli/settings.py`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- likely `tests/cli/test_cli_run.py`
- likely `tests/cli/test_cli_spec.py`
- likely focused tests for the builtin probe helper

Read first:

1. [`weft/commands/specs.py`](../../weft/commands/specs.py)
2. [`weft/commands/run.py`](../../weft/commands/run.py)
3. [`weft/cli.py`](../../weft/cli.py)
4. [`weft/core/agents/provider_cli/probes.py`](../../weft/core/agents/provider_cli/probes.py)
5. [`weft/core/agents/provider_cli/settings.py`](../../weft/core/agents/provider_cli/settings.py)
6. [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
7. [`tests/cli/test_cli_spec.py`](../../tests/cli/test_cli_spec.py)

Current structure:

- stored task specs live in `.weft/tasks/`
- stored pipeline specs live in `.weft/pipelines/`
- `weft run --pipeline` already accepts `NAME|PATH`
- `weft run --spec` help and typing still force an existing file path
- `weft spec` commands already own named stored-spec lookup
- isolated delegated synthetic probes already exist in
  `weft/core/agents/provider_cli/probes.py`

The thin-slice opportunity is to reuse the existing named-spec surface rather
than inventing a new command family, and to reuse the current pipeline
resolution model rather than introducing a second ad hoc `NAME|PATH` pattern.

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Where does `weft run --spec` currently stop name-based resolution from even
   reaching `weft/commands/run.py`?
2. What should happen if a local `.weft/tasks/probe-agents.json` exists and
   Weft also ships a builtin `probe-agents` spec?
3. Why is `weft run foo` the wrong place to add spec-name lookup?
4. What is the difference between explicit project config in
   `.weft/agents.json` and observed probe output?

## Invariants and Constraints

The following must stay true:

- builtin specs are resolved only through explicit spec-management or `--spec`
  surfaces
- bare command execution remains unchanged
- builtin specs are read-only and versioned with the installed Weft code
- project-local specs shadow builtin specs
- builtin probe helpers do not silently overwrite explicit project config
- builtin probe helpers remain normal Weft tasks on the standard durable spine
- docs and help must describe the same `--spec` contract
- no hidden startup probing is reintroduced

Review gates for this slice:

- if `--spec` resolution starts depending on command PATH heuristics, stop
- if builtin listing and deletion semantics are ambiguous, resolve them
  explicitly before implementation
- if the probe helper starts mutating unrelated project files, stop

## Planned Behavior

### 1. Builtin TaskSpec namespace

Add a checked-in builtin task-spec directory under the Weft package, such as:

```text
weft/
├── builtins/
│   ├── __init__.py
│   ├── agent_probe.py
│   └── tasks/
│       └── probe-agents.json
```

Rules:

- builtin specs are ordinary JSON TaskSpecs
- builtin specs are not copied into `.weft/` during init
- builtin specs are treated as read-only shipped assets

### 2. Shared task-spec resolution

Introduce one shared task-spec resolver for explicit task-spec references used
by `weft run --spec` and relevant `weft spec` commands.

Design rule:

- match the current `weft run --pipeline NAME|PATH` mental model
- extract shared resolution mechanics rather than duplicating the current
  `_load_pipeline_spec()` shape for task specs separately

Resolution order:

1. explicit existing file path
2. `.weft/tasks/<name>.json`
3. builtin `weft/builtins/tasks/<name>.json`
4. error

Local shadowing rule:

- if both local and builtin exist for the same name, the local stored spec wins

Suggested result shape:

- resolved kind: `file`, `stored`, or `builtin`
- resolved path
- payload or parsed TaskSpec

### 3. Builtin probe helper

Ship one builtin helper task, tentatively `probe-agents`.

Suggested behavior:

- attempts the isolated delegated probes for known provider CLIs
- reports for each provider:
  - whether an executable was found
  - version or compact probe result when available
  - whether a missing `.weft/agents.json` entry was written
  - whether an existing explicit project setting was preserved
- writes only missing executable defaults into `.weft/agents.json`
- never claims auth or runtime health as durable truth

Default write policy:

- create `.weft/agents.json` if missing and the project exists
- fill missing provider executable entries
- do not replace existing explicit provider entries
- report preserved entries as such

Future force-overwrite behavior, if desired, is out of scope.

### 4. CLI surface

Change explicit spec handling only:

- `weft run --spec NAME|PATH`
- `weft run --spec` should follow the same "explicit path first, else stored
  name resolution" model already used by `weft run --pipeline`
- `weft spec show NAME` should also resolve builtin fallback
- `weft spec list` should decide builtin discoverability explicitly

Recommended minimal discoverability rule:

- include builtin specs in `weft spec list`
- mark them with a source field such as `"source": "builtin"`
- suppress builtin duplicates when a local spec with the same name exists

Deletion rule:

- `weft spec delete` only deletes local stored specs
- attempting to delete a builtin-only spec should fail clearly with a read-only
  message

Counterargument:

- skipping builtin listing would reduce CLI churn

Why not take it:

- discoverability matters for this slice because the probe helper should be
  findable without reading source code

### 5. Help and docs

Update CLI help and docs so they say:

- `weft run --spec NAME|PATH`
- local stored task specs shadow builtin specs
- builtin specs are read-only shipped helpers
- bare command execution is unchanged

## Tasks

1. Add builtin task-spec asset plumbing.
   - Outcome: Weft can load read-only builtin task specs from package data.
   - Files to touch:
     - new `weft/builtins/` package
     - `pyproject.toml` only if package-data inclusion is required
   - Constraints:
     - no copy-on-init
     - keep assets simple JSON files
   - Done when:
     - a builtin task-spec path can be resolved in tests

2. Add shared explicit task-spec resolution.
   - Outcome: one resolver handles `PATH -> local stored -> builtin`.
   - Files to touch:
     - `weft/commands/specs.py`
     - `weft/commands/run.py`
     - `weft/cli.py`
   - Constraints:
     - reuse the existing pipeline `NAME|PATH` model and as much resolution
       plumbing as is sensible
     - no effect on bare command execution
     - local shadowing must be deterministic
   - Done when:
     - `weft run --spec probe-agents` can resolve builtin or local correctly

3. Implement the builtin delegated-agent probe helper.
   - Outcome: a normal Weft task can probe known delegated CLIs and populate
     missing `.weft/agents.json` entries.
   - Files to touch:
     - `weft/builtins/agent_probe.py`
     - `weft/builtins/tasks/probe-agents.json`
     - reuse `weft/core/agents/provider_cli/probes.py`
     - reuse `weft/core/agents/provider_cli/settings.py`
   - Constraints:
     - no overwrite of explicit project settings
     - no startup side effects
   - Done when:
     - the helper returns a clear summary and writes only missing entries

4. Align spec-management commands with builtin behavior.
   - Outcome: `spec show` and `spec list` understand builtin specs; `spec delete`
     remains local-only.
   - Files to touch:
     - `weft/commands/specs.py`
     - `weft/cli.py`
   - Constraints:
     - deletion semantics must stay safe and obvious
   - Done when:
     - builtin discoverability and read-only behavior are covered by tests

5. Update docs and help.
   - Outcome: the CLI contract is explicit and traceable.
   - Files to touch:
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/11-CLI_Architecture_Crosswalk.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - any inline help text in `weft/cli.py`
   - Constraints:
     - do not leave stale file-only `--spec` language behind
   - Done when:
     - help, spec docs, and tests describe the same behavior

## Verification Plan

Focused checks:

- `source .envrc && uv run pytest tests/cli/test_cli_run.py tests/cli/test_cli_spec.py -q`
- targeted tests for the builtin probe helper if split into a separate file
- `source .envrc && uv run ruff check weft tests`
- `source .envrc && uv run mypy weft`

Expected new coverage:

- `weft run --spec NAME` resolves a local stored task spec
- `weft run --spec NAME` resolves a builtin spec when local is absent
- local stored spec shadows builtin spec
- `weft spec list` shows builtin specs distinctly
- `weft spec show` resolves builtin fallback
- `weft spec delete` refuses builtin-only specs
- builtin probe helper writes missing `.weft/agents.json` entries but preserves
  existing ones

Full sweep before claiming done:

- `source .envrc && uv run pytest`

## Review Plan

- Independent review after implementation.
- Review focus:
  - command/spec ambiguity
  - local-shadowing correctness
  - builtin discoverability vs accidental CLI complexity
  - accidental reintroduction of hidden startup probing
  - mutation safety around `.weft/agents.json`

## Related Documents

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
