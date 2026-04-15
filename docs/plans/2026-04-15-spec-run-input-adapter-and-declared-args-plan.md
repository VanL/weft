# Spec Run-Input Adapter And Declared Args Plan

## Goal

Add a small, explicit `weft run --spec` input-shaping feature so a TaskSpec can
declare a narrow set of named CLI arguments and an optional stdin contract, then
use a spec-owned Python helper to turn those inputs into the normal initial work
payload. Keep this on the existing durable spine, keep Docker-specific late
bound mounts separate, and ship one real consumer by upgrading the bundled
`example-dockerized-agent` builtin from raw JSON piping to a nicer
`--prompt`-style UX.

This is a public contract change. It touches the `weft run` CLI, `TaskSpec`
schema, builtin docs, and the example delegated-agent path. It should be
implemented as a thin CLI wrapper around the existing spawn-request submission
path, not as a new execution workflow.

## Scope Lock

Implement exactly this slice:

1. add an optional TaskSpec field for explicit `weft run --spec` input shaping
2. support a spec-owned Python adapter that converts declared CLI args plus
   optional piped stdin into the ordinary initial work payload
3. support declared long options like `--prompt VALUE` for specs that opt in
4. keep piped stdin working, but route it through the adapter when the spec
   opts in
5. keep the underlying runtime contract unchanged:
   - agent tasks still receive the ordinary agent work envelope
   - function tasks still receive the ordinary initial work item
   - command tasks still receive the ordinary initial input semantics
6. migrate `example-dockerized-agent` to the new UX as the first real bundled
   consumer
7. keep Docker late-bound file mounting as a separate existing feature; the new
   run-input adapter may feed data into that feature, but it does not replace
   or generalize it
8. update current specs, README examples, builtin docs, and tests so the new
   contract is explicit

Do not implement any of the following in this slice:

- a second execution path outside
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- manager-side or worker-side run-input adaptation
- a generic workflow language or mini DSL for composing work items
- broad dynamic CLI parsing beyond the declared long-option shape needed here
- spec-specific dynamic `--help` generation
- pipeline support
- `weft task send` support
- autostart-manifest support
- API/server-side support
- a new Docker feature
- a new agent-runtime feature
- automatic interpretation of `TaskSpec.metadata`
- hidden preflight or readiness checks in ordinary `weft run`
- boolean flags, repeated flags, nested objects, list-valued arguments, or
  short options for declared spec-run args in the first slice

If implementation pressure pulls toward any of those, stop and split a
follow-on plan.

## Design Position

The key design calls for this slice are fixed:

1. This is a CLI wrapper over the existing spawn path, not a new runtime path.
   The adapter runs in the `weft run --spec` client process before the spawn
   request is enqueued. The manager, consumer, task runner, and runtime
   adapters still see only the normal initial payload.

2. The new contract belongs in `TaskSpec`, not in caller metadata.
   `metadata` is caller-owned and Weft does not interpret it. The new contract
   is a static spec-owned declaration about how `weft run --spec` should turn
   CLI input into the initial work payload. It must live in a real spec field.

3. Docker-specific file mounting stays separate.
   The current Docker `work_item_mounts` feature remains runner-specific and
   late-bound. The new run-input adapter may choose to emit a work item that
   includes a path in `metadata.document_path`, but the CLI feature itself is
   not a Docker abstraction.

4. The adapter is descriptive at the schema layer and imperative in Python.
   The schema declares which named args and stdin shape the spec wants. The
   adapter function contains the logic that builds the work item. Do not turn
   the schema into a general templating or routing language.

5. Keep the first slice narrow.
   Declared spec-run args should be long options only, string-valued at the
   CLI surface, with optional `"path"` validation where explicitly declared.
   If the implementation starts drifting toward a general argparse clone, stop.

6. Specs that do not opt in must stay unchanged.
   Existing `weft run --spec` behavior for specs without the new field remains
   exactly as it is today.

7. Specs that do opt in deliberately change stdin semantics for that spec.
   When a spec declares the new run-input contract, `weft run --spec` routes
   declared args and stdin through the adapter. That spec no longer gets raw
   stdin passthrough from `weft run --spec` unless its adapter chooses to
   preserve that behavior.

8. Bundle-local helper code should reuse the current bundle-aware Python import
   path.
   Do not invent a second callable-ref syntax for this slice.

## Source Documents

Source specs:

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-0.1], [CLI-1.1.1]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.4]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-2.1], [AR-3], [AR-3.1]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)

Relevant current-state plans and docs:

- [`docs/plans/2026-04-06-piped-input-support-plan.md`](./2026-04-06-piped-input-support-plan.md)
  - This landed the current raw stdin behavior for `weft run --spec`
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
  - This is the current builtin-spec resolution model
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](./2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
  - This is relevant because `example-dockerized-agent` already depends on the
    Docker `codex` runtime descriptor and the current Docker `work_item_mounts`
    feature

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Source-spec gap:

- There is no current spec section that defines a spec-owned run-input adapter
  or declared `weft run --spec` argument contract. This work must add the
  missing current-state description to the touched specs rather than leaving the
  behavior implicit in code and examples.

## Context and Key Files

Files to modify:

- CLI and submission path:
  - `weft/cli.py`
  - `weft/commands/run.py`
  - `weft/commands/validate_taskspec.py`
- shared schema and adapter helpers:
  - `weft/core/taskspec.py`
  - recommended new module:
    `weft/core/spec_run_input.py`
  - likely `weft/core/targets.py` or `weft/core/imports.py` only if an
    existing callable-ref helper is the smallest clean reuse point
- builtin example:
  - `weft/builtins/tasks/example-dockerized-agent/taskspec.json`
  - `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
- docs:
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/13-Agent_Runtime.md`
  - `README.md`
- tests:
  - recommended new tests:
    - `tests/core/test_spec_run_input.py`
  - existing tests to extend:
    - `tests/taskspec/test_taskspec.py`
    - `tests/cli/test_cli_run.py`
    - `tests/cli/test_cli_spec.py`
    - `tests/core/test_builtin_example_dockerized_agent.py`
    - `tests/system/test_builtin_contract.py`

Read first:

1. `weft/commands/run.py`
   - Current owner of `_read_piped_stdin`, `_run_spec_via_manager`, and
     submission-time validation for `--spec`
2. `weft/cli.py`
   - Current `run` command parser and option declarations
3. `weft/core/taskspec.py`
   - Current schema ownership and field-validation patterns
4. `weft/core/agents/runtime.py`
   - Current normalized agent work-item contract; the adapter must return data
     that fits this path rather than inventing a new one
5. `weft/commands/specs.py`
   - Current bundle-root resolution behavior for bundled specs
6. `weft/builtins/tasks/example-dockerized-agent/taskspec.json`
7. `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
8. `tests/cli/test_cli_run.py`
9. `tests/core/test_builtin_example_dockerized_agent.py`
10. `weft/commands/validate_taskspec.py`
    - Current explicit validation seam for local callable refs and related
      submission-time support code

Current structure:

- `weft run --spec` currently loads the TaskSpec template locally, reads piped
  stdin once, and enqueues the initial payload through the normal spawn-request
  path
- `cmd_run()` currently rejects `--arg`, `--kw`, `--env`, and `--tag` for
  spec-backed runs
- agent tasks already accept structured work items through the normal public
  envelope documented in `13-Agent_Runtime.md`
- `example-dockerized-agent` currently uses the raw public work envelope plus
  Docker-specific `work_item_mounts`

Shared paths to reuse:

- `resolve_spec_reference()` in `weft/commands/specs.py`
- current bundle-aware callable import resolution
- `_read_piped_stdin()` in `weft/commands/run.py`
- the current `_enqueue_taskspec()` spawn path
- the existing explicit spec-validation command path
- current agent work-item normalization and validation

Do not duplicate:

- spawn submission
- bundle-root import resolution
- agent work-item normalization
- Docker `work_item_mounts` logic

### Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Where is non-TTY stdin currently read for `weft run`, and where does that
   payload enter the durable system?
2. Why is `TaskSpec.metadata` the wrong place for declared `--prompt`-style
   inputs?
3. Which layer currently owns Docker late-bound mounts from work-item fields?
4. Which function currently enqueues the spawn request, and why must the new
   feature still pass through it?
5. Which specs and docs must be updated together so the example builtin and the
   documented CLI surface do not drift apart?

## Invariants and Constraints

The following must stay true:

- the durable spine remains the only execution path
- spawn requests remain the only durable submission path
- `spec` and `io` immutability rules remain intact after resolved TaskSpec
  creation
- `weft run --spec` still attempts the real run; no hidden preflight is added
- specs without the new run-input field keep current behavior
- pipeline behavior stays unchanged
- Docker `work_item_mounts` stays Docker-specific
- `TaskSpec.metadata` remains caller-owned and uninterpreted by Weft
- bundle-local helper resolution reuses the existing bundle-aware Python import
  path
- no new dependency is added
- no drive-by CLI redesign lands in this slice

Hidden couplings to name explicitly:

- the `run` command parser is currently static, but declared `--prompt`-style
  args are spec-specific; this is the main parser seam for the slice
- `example-dockerized-agent` already depends on two separate concepts:
  runner-specific Docker mounts and agent-runtime work-envelope shaping; do not
  blur them
- current stdin behavior for spec runs is raw passthrough; opting a spec into
  the new feature deliberately changes that spec's CLI submission behavior
- bundle-local helper loading must remain consistent between builtin bundles and
  project-local spec bundles

Error-path priorities:

- schema errors, undeclared args, missing required args, ambiguous input
  sources, and adapter exceptions are fatal local submission errors and must
  fail before queueing
- doc/help discoverability gaps are not runtime-fatal, but they are still
  release blockers for this slice because this is a public CLI contract
- adapter output that is not JSON-serializable or otherwise cannot be submitted
  is a fatal local submission error

Review gates:

- if the implementation starts moving adaptation into manager, consumer, or
  runtime code, stop
- if the schema starts growing into a general CLI grammar or workflow DSL, stop
- if parser changes would make ordinary `weft run` unknown options silently
  succeed in confusing ways, stop and tighten the parser design before coding
- if the example builtin migration depends on weakening the current public
  agent-envelope contract, stop

## Rollout and Rollback

Rollout order matters because this adds a new TaskSpec field.

1. land schema support, CLI support, and docs in the same slice
2. migrate the builtin example only after the new schema and parser support
   exists
3. keep the existing Docker `work_item_mounts` example path intact underneath
   the nicer `--prompt`/stdin UX

Rollback notes:

- this is a soft one-way door for newly authored specs: older Weft versions
  will not understand the new `spec.run_input` field
- rollback is clean only if the new field is removed from shipped and local
  specs together
- there is no queue-format or persisted-state migration in this slice
- do not ship the builtin migration without the schema and CLI support

Observable success after implementation:

- a spec that declares the new field can accept named CLI args like `--prompt`
  and optional piped stdin, then submit the resulting shaped work item through
  the normal spawn path
- a spec without the new field still behaves exactly as it did before
- `example-dockerized-agent` can be invoked as:
  `cat /path/to/file | weft run --spec example-dockerized-agent --prompt "Summarize this document"`
- `example-dockerized-agent` can also use an explicit file path, for example:
  `weft run --spec example-dockerized-agent --prompt "Summarize this document" --document /abs/path/to/file`

## Planned Behavior

### 1. New TaskSpec field: `spec.run_input`

Add an optional static section under `spec`, tentatively:

```jsonc
{
  "spec": {
    "run_input": {
      "adapter_ref": "module:function",
      "arguments": {
        "prompt": {
          "type": "string",
          "required": true,
          "help": "Instruction to apply to the supplied document"
        },
        "document": {
          "type": "path",
          "required": false,
          "help": "Absolute host path to a document file"
        }
      },
      "stdin": {
        "type": "text",
        "required": false,
        "help": "Document text supplied through stdin"
      }
    }
  }
}
```

Fixed first-slice rules:

- `adapter_ref` is required when `run_input` is present
- `arguments` keys become long options by replacing `_` with `-`
- only long options are supported in this slice
- declared argument types are limited to:
  - `"string"`
  - `"path"`
- `stdin.type` is `"text"` only in this slice
- `required` defaults to `false`
- `help` is documentation only; do not build dynamic per-spec help in this
  slice

Validation rules:

- declared names must be valid Python-style identifiers
- dashed option names derived from them must not collide with existing
  `weft run` option names
- dashed option names must not collide with each other after `_` to `-`
  normalization
- `run_input` is optional and must be ignored entirely when absent

What it is not:

- it is not generic runtime metadata
- it is not the work payload itself
- it is not a Docker config block
- it is not a template language

### 2. Adapter callable contract

Add one shared adapter-invocation helper in core, recommended module:
`weft/core/spec_run_input.py`.

Proposed adapter request shape:

```python
@dataclass(frozen=True, slots=True)
class SpecRunInputRequest:
    arguments: Mapping[str, str]
    stdin_text: str | None
    context_root: str | None
    spec_name: str
```

Proposed callable contract:

- `adapter_ref` resolves to a Python callable that accepts exactly one
  `SpecRunInputRequest`
- the callable returns the normal initial work payload to enqueue

Allowed return shape:

- any JSON-serializable payload that the current target already accepts
  downstream
  - string
  - JSON object
  - JSON array if the existing target supports it

Disallowed:

- mutating the TaskSpec
- mutating environment or runner config
- returning arbitrary Python objects that cannot be submitted through the
  normal spawn path

Use bundle-aware callable resolution so bundled specs can keep using ordinary
`module:function` refs.

### 3. CLI parsing contract

Add declared spec-run arg parsing only to `weft run --spec`.

Desired user-facing shape:

```bash
weft run --spec example-dockerized-agent --prompt "Summarize this document"
weft run --spec example-dockerized-agent --prompt "Summarize this document" --document /abs/path/to/file
cat /abs/path/to/file | weft run --spec example-dockerized-agent --prompt "Summarize this document"
```

Implementation rule:

- built-in `weft run` options still parse first through Typer
- when `--spec` is present, any remaining tokens after known `weft run` option
  parsing are treated as candidate declared spec-run args, not as shell command
  tokens
- when `--spec` is absent, keep the current inline-command model
- if parser changes require broader unknown-option acceptance, add an explicit
  guard so `weft run --unknown` without `--spec` still fails clearly instead of
  accidentally treating `--unknown` as a command name

First-slice declared-arg grammar:

- `--name value`
- `--name=value`
- no short flags
- no boolean flags
- no repeatable flags
- no nested objects
- no list-valued flags

Runtime rules:

- specs without `spec.run_input` keep current behavior
- specs with `spec.run_input` consume declared args and stdin through the
  adapter before queueing
- if a declared required arg is missing, fail locally
- if an undeclared `--name` is present for that spec, fail locally
- if both stdin and a declared path arg are present and the adapter contract
  says that is ambiguous, the adapter should raise and `weft run` should show
  that error without queueing

Important parser constraint:

The implementer should assume the Typer/Click seam is fragile here. Do not
hand-wave this. The likely implementation is to let the `run` command preserve
raw trailing tokens when `--spec` is in play, then parse those tokens against
the loaded spec's `run_input.arguments`. Keep this narrow.

Stop gate:

- if this starts requiring broad command-wide unknown-option acceptance that
  makes ordinary `weft run` parsing ambiguous or misleading, stop and tighten
  the parser shape before landing code

### 4. `example-dockerized-agent` migration

Upgrade the builtin bundle so it demonstrates the new shape without changing
the existing Docker runner feature underneath.

Planned builtin contract:

- declared args:
  - `--prompt` required
  - `--document` optional absolute host path
- optional stdin text for document contents
- the adapter returns the ordinary agent work envelope

Recommended behavior:

- if `--document` is present:
  - return a work item that preserves the current Docker mount flow by writing
    the path into the existing work-item field expected by Docker
  - use the `explain_mounted` agent template so `prompt` stays data, not ad hoc
    string concatenation
- else if stdin text is present:
  - return a work item that embeds the provided document text through the
    `explain_inline` agent template
- if both `--document` and stdin text are present:
  - raise a clear local ambiguity error; do not silently choose one
- else:
  - raise a clear local error that one of `--document` or stdin is required

Recommended template shape:

- `explain_mounted`
- `explain_inline`

This keeps the example aligned with the current public agent-envelope contract
and shows both:

- runner-specific mounted-file input
- generic inline-text input

Do not hide Docker specifics:

- the builtin docs should still say that mounted file paths ultimately feed the
  current Docker `work_item_mounts` feature
- the builtin should not pretend that `--document` is a generic Weft feature

## Tasks

1. Add the new TaskSpec schema and validation.
   - Outcome:
     - `TaskSpec` can declare a narrow `spec.run_input` contract
     - schema validation rejects collisions and unsupported shapes early
   - Files to touch:
     - `weft/core/taskspec.py`
     - `tests/taskspec/test_taskspec.py`
   - Read first:
     - `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.4]
     - `weft/core/taskspec.py`
   - Implementation notes:
     - add new Pydantic models for the section
     - keep the field under `spec`
     - validate reserved-name collisions against the current `weft run` option
       surface
     - keep the first slice to `"string"` and `"path"` plus optional stdin text
   - Tests to add or update:
     - schema accepts a valid `run_input`
     - schema rejects unknown arg types
     - schema rejects reserved option-name collisions
     - schema rejects `_` / `-` normalization collisions
   - Stop gate:
     - if the schema starts turning into a generic CLI grammar, stop
   - Done signal:
     - a stored/bundled spec with `spec.run_input` validates cleanly and the
       failure messages for invalid declarations are clear

2. Add a shared core helper for adapter invocation.
   - Outcome:
     - one small shared path exists for turning declared args and stdin into the
       initial work payload
   - Files to touch:
     - new `weft/core/spec_run_input.py`
     - possibly `weft/core/imports.py` or `weft/core/targets.py` only if needed
       for the cleanest callable-ref reuse
   - Read first:
     - current bundle-aware callable loading helpers
     - `weft/commands/specs.py`
   - Implementation notes:
     - define the request object
     - reuse existing bundle-aware Python import resolution
     - require a JSON-serializable return value
     - keep this helper submission-only; do not thread it into manager or
       runtime code
   - Tests to add or update:
     - bundle-local adapter resolution works
     - adapter receives parsed args and stdin
     - non-serializable return values fail clearly
   - Stop gate:
     - if this helper starts depending on queue or manager state, stop
   - Done signal:
     - a synthetic bundle-local adapter can be loaded and invoked through one
       shared core path

3. Extend explicit spec validation to understand `spec.run_input`.
   - Outcome:
     - `weft spec validate` can validate the new schema and adapter ref with the
       same explicit, local-only boundary we already use for related callable
       refs
   - Files to touch:
     - `weft/commands/validate_taskspec.py`
     - `weft/core/spec_run_input.py` or a nearby helper module if a dedicated
       validation helper is warranted
     - `tests/cli/test_cli_spec.py`
   - Read first:
     - `weft/commands/validate_taskspec.py`
     - current validation helpers for agent runtimes, tool profiles, and runner
       environments
   - Implementation notes:
     - keep this validation explicit; do not thread it into ordinary
       `weft run --spec`
     - validate schema, callable-ref importability, and reserved-name rules
     - do not add runtime or provider preflight here; this is a local callable
       and schema check only
   - Tests to add or update:
     - `weft spec validate` succeeds for a valid bundled/local adapter ref
     - `weft spec validate` fails clearly for a missing adapter ref
   - Stop gate:
     - if validation work starts pulling logic into the normal run path, stop
   - Done signal:
     - explicit validation covers the new field, but ordinary run still attempts
       the real submission and run path

4. Extend `weft run --spec` to parse declared args and call the adapter before queueing.
   - Outcome:
     - `weft run --spec` can accept named long options declared by the loaded
       spec and shape the initial payload through the adapter
   - Files to touch:
     - `weft/cli.py`
     - `weft/commands/run.py`
   - Read first:
     - `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]
     - `weft/commands/run.py`
     - `weft/cli.py`
   - Implementation notes:
     - preserve built-in `weft run` option parsing
     - after loading the TaskSpec template, parse trailing declared args against
       `spec.run_input`
     - keep current raw-stdin passthrough behavior for specs without the new
       field
     - when the new field is present, route stdin through the adapter instead of
       passing it through unchanged
     - queue only after successful local parsing and adapter execution
   - Tests to add or update:
     - declared `--prompt`-style arg works
     - `--name=value` works
     - missing required arg fails before queueing
     - undeclared arg fails before queueing
     - specs without `run_input` still keep current stdin behavior
   - What not to mock:
     - use `run_cli(...)` and the real submission path
     - do not replace spawn submission with mocks when a real no-op/function
       spec can prove the behavior
   - Stop gate:
     - if parser changes start making ordinary `weft run` unknown options behave
       confusingly, stop and tighten the approach before merging
   - Done signal:
     - a real CLI test proves that the shaped payload enters the normal spawned
       task path

5. Migrate `example-dockerized-agent` to the new contract.
   - Outcome:
     - the bundled example becomes the first real consumer and demonstrates both
       `--document` and piped-stdin usage
   - Files to touch:
     - `weft/builtins/tasks/example-dockerized-agent/taskspec.json`
     - `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
   - Read first:
     - current builtin docs in `10B-Builtin_TaskSpecs.md`
     - current example helper module
   - Implementation notes:
     - add `spec.run_input`
     - implement the bundle-local adapter helper
     - keep Docker runner defaults and tool-profile behavior intact
     - use templates for mounted-path and inline-text modes rather than ad hoc
       prompt concatenation where practical
   - Tests to add or update:
     - builtin resolution still works from the bundle
     - builtin `run_input` declaration is present and valid
     - bundle-local adapter builds the expected agent work item for:
       - `--document`
       - stdin text
       - ambiguous or missing inputs
   - Stop gate:
     - if the builtin starts needing broader Docker feature changes, stop; that
       is a separate slice
   - Done signal:
     - the builtin can be invoked with `--prompt` plus either `--document` or
       stdin and produce the ordinary agent work envelope the current Docker
       lane already understands

6. Update docs and nearby examples to match the new current contract.
   - Outcome:
     - the public docs describe both the new schema and the new example UX
   - Files to touch:
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/13-Agent_Runtime.md`
     - `README.md`
   - Read first:
     - the new code and tests from tasks 1 through 4
   - Implementation notes:
     - update `CLI-1.1.1` to describe spec-declared args for `weft run --spec`
     - document the new `spec.run_input` field in `TaskSpec`
     - update the builtin example section to use `--prompt` and stdin / file
       path examples
     - keep the Docker-specific `work_item_mounts` explanation separate from the
       new CLI feature
   - Tests to add or update:
     - extend any doc-contract or builtin-contract tests that inspect the
       shipped example
   - Stop gate:
     - if the docs need caveats that contradict the implemented behavior, fix
       the code or the plan before landing
   - Done signal:
     - docs, example usage, and shipped builtin behavior all say the same thing

7. Run verification and capture the exact release gates for the slice.
   - Outcome:
     - the change is proven on the real CLI path and the bundled example path
   - Files to touch:
     - none unless verification exposes bugs
   - Verification commands:
     - `./.venv/bin/pytest -n0 tests/core/test_spec_run_input.py tests/cli/test_cli_run.py tests/cli/test_cli_spec.py tests/core/test_builtin_example_dockerized_agent.py tests/system/test_builtin_contract.py`
     - `./.venv/bin/ruff check weft tests`
     - `./.venv/bin/ruff format --check weft tests docs`
     - `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
   - Manual proof for the bundled example after automated tests:
     - `cat /abs/path/to/file | ./.venv/bin/python -m weft.cli run --spec example-dockerized-agent --prompt "Summarize this document"`
     - `./.venv/bin/python -m weft.cli run --spec example-dockerized-agent --prompt "Tell me the readability level of this document" --document /abs/path/to/file`
   - What not to mock:
     - the real CLI submission path
     - the real spawn-request enqueue path
   - Done signal:
     - automated tests pass and the manual bundled example proof works in an
       environment with Codex and Docker available

## Test Plan

Prefer real-path tests over mocks.

Required automated coverage:

- schema validation for `spec.run_input`
- adapter invocation through bundle-aware imports
- CLI parsing of declared args for `weft run --spec`
- raw behavior preservation for specs without the new field
- builtin bundle resolution plus example adapter behavior

Recommended test shapes:

- a synthetic bundle-backed function spec whose adapter returns a simple string
  or JSON payload, then assert the task receives that payload through the real
  manager path
- a CLI test that proves missing required args fail before queueing by checking
  there is no new spawn request or task TID
- a CLI test that proves undeclared args fail locally and clearly
- a CLI test that proves stdin is routed through the adapter when the spec opts
  in

Avoid:

- mocking the manager or spawn submission just to inspect adapter output
- mocking Click/Typer internals instead of exercising the CLI surface
- mock-only tests for builtin bundle helper loading

## Out of Scope

These are explicit non-goals for this slice:

- pipeline declared args
- dynamic per-spec CLI help output
- a generic `--input-arg` or `--set` fallback surface
- non-Python adapters
- command-target-specific rich stdin routing changes
- JSON-schema-driven work-item building
- manager-side adaptation for API callers
- changing or generalizing Docker `work_item_mounts`

## Review Loop

This slice needs independent review after the plan and again after
implementation because it changes both a public CLI contract and a TaskSpec
field.

Review prompt:

> Read the plan at
> `docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`.
> Carefully examine the plan and the associated code paths. Look for errors,
> bad ideas, parser ambiguities, and places where the change could drift into a
> second execution path. Could you implement this confidently and correctly if
> asked?

Reviewer bootstrap note:

- Prefer a different agent family than the author if one is available at
  implementation time.
- If only same-family review is available, record that limitation in the
  implementation notes.
