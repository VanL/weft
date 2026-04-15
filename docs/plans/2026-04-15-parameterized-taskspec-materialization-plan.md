# Parameterized TaskSpec Materialization Plan

## Goal

Add a narrow parameterized-TaskSpec feature so `weft run --spec` can take
declared arguments like `--provider` and `--model`, materialize a concrete
TaskSpec template locally before queueing, then continue through the normal
durable path. The immediate product goal is one public
`example-dockerized-agent` builtin that can select `codex`, `gemini`,
`opencode`, `qwen`, or `claude_code` explicitly, without teaching the Docker
runner to inspect work-item metadata and without mutating the spec after the
task is already in the durable system.

This is a contract change. It touches `TaskSpec`, `weft run --spec`,
validation, builtin examples, and current docs. It is risky because it changes
the submission path on the durable spine. The implementation must stay thin and
must not turn TaskSpec into a generic templating or JSON-patching DSL.

## Scope Lock

Implement exactly this slice:

1. add an optional TaskSpec field for submission-time parameterization
2. support a spec-owned Python materialization adapter that turns declared CLI
   args into a concrete TaskSpec template before queueing
3. keep `spec.run_input` separate and unchanged in role: it still shapes the
   initial work payload, not the TaskSpec
4. support one public bundled example, `example-dockerized-agent`, with:
   - `--provider`
   - optional `--model`
   - existing `--prompt`
   - existing optional `--document`
   - existing optional stdin document text
5. keep the durable execution path unchanged after local materialization:
   `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
6. preserve the existing provider-specific sibling builtins during this slice
   for compatibility and explicitness
7. update current specs, builtin docs, and README examples so the new contract
   is explicit

Do not implement any of the following in this slice:

- runner-side or worker-side provider selection
- manager-side materialization
- a generic JSON patch or interpolation language for TaskSpecs
- parameterized pipelines
- `weft task send` parameterization
- autostart-manifest parameterization
- API/server-side parameterization
- dynamic `weft --help` synthesis for spec-specific args
- deletion or renaming of the existing provider-specific example builtins
- a new provider-auth mechanism
- any attempt to make missing Claude portable auth disappear

If implementation pressure pulls toward any of those, stop and split a
follow-on plan.

## Design Position

The key design calls for this slice are fixed:

1. Parameterization happens locally in `weft run --spec`, before queueing.
   The output of parameterization is a concrete TaskSpec template payload. The
   manager and worker never see an unresolved parameterized spec.

2. Parameterization is a TaskSpec contract, not caller metadata.
   `metadata` is caller-owned input. It is the wrong place for provider/model
   selection. This belongs in a real spec field.

3. `spec.run_input` stays about work-item shaping only.
   It may depend on the materialized TaskSpec, but it does not mutate the
   TaskSpec itself.

4. The schema is declarative, but the materialization logic is imperative.
   The spec declares allowed parameters and points at a Python adapter. The
   adapter returns the concrete TaskSpec template payload. Do not build a
   general declarative overlay DSL first.

5. The queued task remains explicit.
   What gets resolved, validated, queued, and later inspected by TID is a
   concrete TaskSpec. We are not adding a hidden “provider selector” path.

6. Existing specs without parameterization remain unchanged.
   This feature is opt-in for stored TaskSpecs only.

7. Docker-specific behavior stays Docker-specific.
   Parameterization can select a Docker-backed provider spec, but it does not
   generalize `work_item_mounts` or any other runner feature.

8. Keep the first slice narrow.
   Declared parameter args should be long options only. The initial supported
   scalar types should stay the same as current `spec.run_input` argument
   types: `"string"` and `"path"`. Add optional defaults and explicit choices,
   but do not grow this into a general CLI declaration system.

The main counterargument is that a builtin-specific selector helper would be
cheaper. That is true in the short run. It is weaker as a system abstraction.
We already have a Weft idea of stored TaskSpec templates. If we want one public
spec name that still resolves to a concrete explicit TaskSpec before queueing,
parameterized TaskSpec materialization is the cleaner boundary.

## Proposed Contract

Add a new optional field under `spec`:

```jsonc
{
  "spec": {
    "parameterization": {
      "adapter_ref": "pkg.module:function_name",
      "arguments": {
        "provider": {
          "type": "string",
          "required": false,
          "default": "codex",
          "choices": ["claude_code", "codex", "gemini", "opencode", "qwen"],
          "help": "Delegated provider to run"
        },
        "model": {
          "type": "string",
          "required": false,
          "help": "Optional provider-specific model override"
        }
      }
    }
  }
}
```

Recommended request object:

```python
@dataclass(frozen=True, slots=True)
class SpecParameterizationRequest:
    arguments: dict[str, str]
    context_root: str | None
    spec_name: str
    taskspec_payload: Mapping[str, Any]
```

Recommended adapter contract:

- input: `SpecParameterizationRequest`
- output: one JSON-serializable TaskSpec template payload
- output must validate as a TaskSpec template, not a resolved runtime TaskSpec
- Weft does not recursively apply parameterization a second time
- the request payload should be treated as read-only input; the adapter should
  return a new payload rather than mutating the original mapping in place

For the first slice, the materialized payload should be validated with these
extra rules:

- it must still be a TaskSpec template (`tid` absent or `null`)
- it must keep the same `spec.type` as the base template
- it may change static fields such as:
  - `name`
  - `description`
  - `spec.agent.model`
  - `spec.agent.runtime_config`
  - `spec.agent.authority_class`
  - `spec.agent.runtime_config.tool_profile_ref`
  - `spec.runner`
  - `spec.run_input`
- the adapter is allowed to remove or replace `spec.parameterization` in the
  materialized payload, but Weft must not re-run parameterization after the
  first materialization step

This is the narrow mental model:

- stored spec: parameterized template
- materialization step: produce a concrete spec template
- existing TaskSpec resolution: fill runtime fields like `tid` and queues
- `run_input` step: produce the initial work payload
- enqueue: ordinary spawn request

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3], [TS-1.4]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-0.1], [CLI-1.1.1]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-7]

Relevant current-state plans:

- [`docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](./2026-04-15-spec-run-input-adapter-and-declared-args-plan.md)
  - landed the current `spec.run_input` contract
- [`docs/plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](./2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md)
  - landed the current multi-provider Docker-backed `provider_cli` surface
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
  - current builtin bundle and spec-resolution model

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

- there is no current spec section that defines parameterized TaskSpec
  materialization
- current docs use “template” in two other senses already:
  - stored TaskSpecs with omitted runtime fields
  - agent prompt templates under `spec.agent.templates`
- the spec text for this slice must make the distinction explicit instead of
  leaving “template” overloaded and ambiguous

Review availability note:

- this plan was self-reviewed only in this session
- no independent different-family review was bootstrapped here because
  delegation was not requested in this conversation
- treat that as a review limitation, not as proof that the plan is ambiguity-free

## Context and Key Files

Files to modify:

- shared TaskSpec schema and validation:
  - `weft/core/taskspec.py`
- submission-time helpers:
  - recommended new module:
    `weft/core/spec_parameterization.py`
  - existing shared submission helper:
    `weft/core/spec_run_input.py`
- CLI and validation path:
  - `weft/commands/run.py`
  - `weft/commands/validate_taskspec.py`
- builtin example and helper code:
  - `weft/builtins/tasks/example-dockerized-agent/taskspec.json`
  - `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
  - `weft/builtins/dockerized_agent_examples.py`
- docs:
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/13-Agent_Runtime.md`
  - `README.md`
- tests:
  - recommended new tests:
    - `tests/core/test_spec_parameterization.py`
  - existing tests to extend:
    - `tests/taskspec/test_taskspec.py`
    - `tests/cli/test_cli_run.py`
    - `tests/cli/test_cli_spec.py`
    - `tests/core/test_builtin_example_dockerized_agent.py`
    - `tests/system/test_builtin_contract.py`

Read first:

1. `weft/commands/run.py`
   - current owner of spec loading, piped stdin reading, run-input invocation,
     and spawn submission
2. `weft/core/taskspec.py`
   - current owner of TaskSpec schema, template validation, and bundle-root
     preservation
3. `weft/core/spec_run_input.py`
   - current owner of declared `--prompt`-style submission args and adapter
     invocation for work-item shaping
4. `weft/commands/validate_taskspec.py`
   - current explicit validation seam for submission-time callable refs
5. `weft/commands/specs.py`
   - current stored-spec and builtin bundle resolution path
6. `weft/builtins/tasks/example-dockerized-agent/taskspec.json`
7. `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
8. `weft/builtins/dockerized_agent_examples.py`
9. `tests/core/test_spec_run_input.py`
10. `tests/core/test_builtin_example_dockerized_agent.py`

Current structure:

- `weft run --spec` currently:
  1. loads one stored TaskSpec template locally
  2. optionally reads piped stdin locally
  3. optionally runs `spec.run_input`
  4. enqueues the TaskSpec through the normal spawn path
- `resolve_taskspec_payload()` is the canonical runtime-expansion path for
  stored TaskSpec templates
- `spec.run_input` is already a public contract for work-item shaping
- builtin bundles already support bundle-local `module:function` refs
- the current multi-provider Docker example surface is spread across one main
  builtin plus several provider-specific sibling builtins

Shared paths to reuse:

- `resolve_spec_reference()` in `weft/commands/specs.py`
- `_read_piped_stdin()` in `weft/commands/run.py`
- `_enqueue_taskspec()` in `weft/commands/run.py`
- current bundle-aware callable import resolution
- current declared-argument parsing style from `spec.run_input`
- `resolve_taskspec_payload()` in `weft/core/taskspec.py`

Do not duplicate:

- spawn submission
- bundle-root import logic
- declared option parsing rules
- Docker-specific mount behavior
- provider-specific invocation logic

### Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Where does `spec.run_input` run today, and why must parameterization run in
   the same general place rather than in the manager or runner?
2. Which function currently turns a stored TaskSpec template into a resolved
   runtime TaskSpec with `tid` and queues?
3. Why is work-item metadata the wrong place to choose `codex` versus
   `gemini`?
4. Which layer currently owns Docker-specific runtime mounts and provider
   runtime defaults?
5. Why would a generic JSON patch DSL be the wrong first implementation even
   if it looks flexible?

## Invariants and Constraints

The following must stay true:

- the durable spine remains the only execution path
- spawn requests remain the only durable submission path
- manager and worker code do not learn a second “parameterized spec” concept
- `spec` and `io` immutability rules remain intact after resolved TaskSpec
  creation
- `spec.run_input` remains about work-item shaping, not spec mutation
- specs without `spec.parameterization` keep current behavior
- bundle-local callable refs reuse the current bundle-aware import resolution
- no new dependency is added
- no drive-by CLI redesign lands in this slice
- provider selection remains explicit in the materialized TaskSpec that gets
  queued

Hidden couplings to name explicitly:

- `weft run` currently owns both spec loading and `spec.run_input`. This slice
  inserts a new local phase before queueing, so ordering matters.
- parameterization and run-input declared args share one CLI surface. Name
  collisions must be forbidden or the UX becomes ambiguous.
- the stored spec may be bundle-backed, and both adapter refs must resolve
  against the same bundle root.
- provider-specific example behavior lives partly in:
  - the TaskSpec
  - bundle-local helper code
  - shared Docker example helper code
  - provider runtime descriptors
  This slice must not blur those layers.

Error-path priorities:

- undeclared args, missing required args, invalid defaults, invalid choices,
  adapter exceptions, and invalid materialized payloads are fatal local
  submission errors and must fail before queueing
- builtin/doc drift is not a runtime error, but it is still a release blocker
  because this is a public TaskSpec contract

Review gates:

- no new execution path
- no manager-side or runner-side parameterization
- no JSON patch DSL
- no hidden provider-selection behavior inside Docker or agent runtime code
- no removal of the existing provider-specific builtin names in this slice

Stop-and-re-evaluate gates:

- you are about to let a runner pick the provider from the work item
- you are about to let a materialization adapter return something other than a
  TaskSpec template payload
- you are about to re-run parameterization recursively
- you are inventing nested or expression-based substitution rules
- the parser design starts depending on broad Typer dynamic option mutation
- `weft spec validate` is growing into a second submission workflow

## Rollout and Rollback

Rollout:

1. land the schema and local helper path first
2. land CLI materialization before builtin migration
3. migrate `example-dockerized-agent` to the new parameterized surface
4. keep existing provider-specific sibling builtins intact during this slice
5. update docs only after the concrete CLI shape is real in code

Rollback:

- because the feature is opt-in, rollback is straightforward:
  - stop using `spec.parameterization`
  - revert the `example-dockerized-agent` builtin to the current static shape
  - leave the provider-specific sibling builtins untouched
- do not delete the sibling builtins as part of rollout, because that would
  turn rollback into a public-name compatibility problem

One-way doors:

- adding the public TaskSpec field name is the main one-way door
- pick the field name carefully and document it clearly
- do not reuse the bare word `template` without qualification

## Tasks

1. Define the schema and validation contract for parameterization.

   Modify `weft/core/taskspec.py` to add a new optional
   `spec.parameterization` section. Keep the section shape parallel to the
   narrow `spec.run_input` contract:

   - `adapter_ref`
   - `arguments`
   - each argument supports:
     - `type`
     - `required`
     - `default`
     - `choices`
     - `help`

   Reuse the smallest clean declared-argument model possible. If the current
   `RunInputArgumentSection` can be safely generalized without creating spec
   confusion, do that. If not, add a small shared declared-argument model and
   point both features at it.

   Validation rules:

   - argument names must be non-empty
   - normalized option names must not collide with reserved `weft run` options
   - parameterization arg names must not collide with `spec.run_input`
     argument names after normalization
   - defaults must satisfy the declared type and `choices`
   - `choices` are valid only for string-like values in the first slice

   Tests first:

   - add schema tests in `tests/taskspec/test_taskspec.py`
   - add focused tests in `tests/core/test_spec_parameterization.py`
   - do not mock Pydantic or adapter loading

   Stop gate:

   - if this task starts inventing expression syntax, back out and keep only
     scalar declarations

2. Implement the local materialization helper path.

   Add `weft/core/spec_parameterization.py` with:

   - `SpecParameterizationRequest`
   - `validate_parameterization_adapter_ref()`
   - `validate_parameterization_adapter()`
   - `invoke_parameterization_adapter()`
   - `materialize_taskspec_template()`

   `materialize_taskspec_template()` should:

   1. take the stored TaskSpec template payload plus parsed args
   2. invoke the adapter locally
   3. require a JSON-serializable mapping result
   4. validate that result as a TaskSpec template payload
   5. enforce the first-slice extra rules:
      - template shape only
      - same `spec.type`
   6. preserve bundle-root association on the resulting `TaskSpec`

   Do not let this helper enqueue anything, read stdin, or know about Docker.

   Tests first:

   - happy path materialization
   - invalid return type
   - invalid TaskSpec payload
   - same-type guard
   - bundle-local adapter import

   Stop gate:

   - if this helper starts talking about work-item shaping, split that logic
     back out into `spec.run_input`

3. Integrate materialization into `weft run --spec`.

   Modify `weft/commands/run.py` so the local submission order becomes:

   1. load stored TaskSpec template
   2. parse declared parameter args from the extra `--name value` tokens
   3. materialize a concrete TaskSpec template when `spec.parameterization`
      exists
   4. rebuild/validate the resulting `TaskSpec`
   5. read piped stdin
   6. apply `spec.run_input` on the materialized spec, not the original one
   7. enqueue through `_enqueue_taskspec()`

   Parser rules:

   - keep one raw extra-token surface, but parse it in two passes:
     1. parameterization args against the stored spec
     2. run-input args against the materialized spec
   - the parameterization parser must return leftover tokens rather than
     treating every unknown token as a hard error
   - allow the user to intermix `--provider`, `--model`, `--prompt`, and
     `--document` on the CLI
   - apply parameter defaults before adapter invocation
   - after materialization, parse the leftover tokens against the materialized
     `spec.run_input`
   - forbid name collisions between parameterization args and run-input args so
     the two-pass parse stays unambiguous

   Do not teach Typer dynamic runtime options. Keep the current “extra tokens
   parsed locally” approach.

   Tests first:

   - parameterized spec with no `run_input`
   - parameterized spec plus `run_input`
   - unknown option
   - duplicate option
   - missing required parameter
   - defaults applied
   - materialized spec drives later `run_input` behavior

   Use real `weft run --spec` command tests where practical. Do not mock away
   the local CLI submission path.

   Stop gate:

   - if this task starts changing manager, consumer, or runner code, stop

4. Extend explicit validation, but keep it narrow.

   Modify `weft/commands/validate_taskspec.py` so explicit validation covers:

   - schema validity of `spec.parameterization`
   - importability/callability of `adapter_ref`
   - argument-name collision checks

   Do not add a second submission workflow to `weft spec validate` in this
   slice. In particular:

   - no parameter-value CLI on `weft spec validate`
   - no runner preflight on derived variants
   - no materialization preview command yet

   The first-slice rule is simple:

   - `weft spec validate` validates the stored template contract
   - `weft run --spec` validates the concrete materialized template used for
     this run

   Tests:

   - adapter-ref validation
   - collision errors
   - invalid defaults/choices surfaced clearly

   Stop gate:

   - if `weft spec validate` starts needing actual provider/model values, split
     that into a future plan

5. Migrate the shipped example to the parameterized surface.

   Update `weft/builtins/tasks/example-dockerized-agent/taskspec.json` so it
   becomes the preferred public example with:

   - `spec.parameterization` arguments:
     - `provider` with default `"codex"` and explicit choices
     - optional `model`
   - existing `spec.run_input` for:
     - `prompt`
     - optional `document`
     - optional stdin text

   Add a bundle-local parameterization adapter in
   `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
   that selects provider-specific static overlays. Keep the overlay logic
   explicit in code. Do not build a generic spec-patch engine.

   The adapter should handle at least:

   - `provider`
   - optional `model`
   - provider-specific `runtime_config.provider`
   - provider-specific `tool_profile_ref` when needed
   - example-specific defaults such as bounded mode for `claude_code` and
     explicit default model guidance for `opencode` where appropriate

   Keep the provider-specific sibling builtins in place during this slice.
   They are compatibility and debugging aids until the new main example is well
   proven.

   Tests first:

   - one test per provider choice for materialized provider selection
   - one test for explicit `--model`
   - one CLI test proving `--provider` plus stdin works through the main
     example path

   Manual/live proof:

   - `codex`
   - `gemini`
   - `opencode`
   - `qwen`
   - `claude_code` only if portable auth is actually configured in the current
     environment

   Stop gate:

   - if the example adapter starts duplicating provider invocation logic from
     `registry.py`, back out and keep only static overlay selection

6. Update docs and examples after the code is real.

   Update:

   - `docs/specifications/02-TaskSpec.md`
   - `docs/specifications/10-CLI_Interface.md`
   - `docs/specifications/10B-Builtin_TaskSpecs.md`
   - `docs/specifications/13-Agent_Runtime.md`
   - `README.md`

   Required documentation points:

   - define `spec.parameterization` clearly
   - distinguish it from:
     - stored TaskSpec templates
     - agent prompt templates
     - `spec.run_input`
   - document the local submission order:
     - parameterize spec
     - shape work item
     - queue spawn request
   - document the new preferred example UX:
     - `--provider`
     - optional `--model`
     - `--prompt`
     - optional `--document`
     - stdin
   - keep the Claude auth caveat explicit
   - mention that provider-specific sibling builtins remain available

   Stop gate:

   - if docs start describing runner-side provider selection, the plan has
     drifted

## Testing and Verification

Prefer red-green TDD for the schema, helper, and CLI parsing work.

Tests to add or extend:

- `tests/taskspec/test_taskspec.py`
  - schema validation, defaults, collisions
- `tests/core/test_spec_parameterization.py`
  - adapter invocation and materialized payload rules
- `tests/cli/test_cli_run.py`
  - `weft run --spec` parameter parsing and ordering
- `tests/cli/test_cli_spec.py`
  - builtin/spec show coverage for the new field
- `tests/core/test_builtin_example_dockerized_agent.py`
  - example materialization behavior
- `tests/system/test_builtin_contract.py`
  - builtin inventory and basic contract coverage

What not to mock:

- bundle-local callable resolution
- TaskSpec validation
- the local `weft run --spec` submission path

Mock only what is external or nondeterministic:

- external provider CLIs in unit tests
- live networked delegated runs in automated tests

Manual verification checklist:

1. `weft spec show example-dockerized-agent` shows the stored parameterized spec
2. `cat doc | weft run --spec example-dockerized-agent --provider codex --prompt ...`
   works
3. `cat doc | weft run --spec example-dockerized-agent --provider gemini --prompt ...`
   works
4. `cat doc | weft run --spec example-dockerized-agent --provider opencode --model ... --prompt ...`
   works
5. `cat doc | weft run --spec example-dockerized-agent --provider qwen --prompt ...`
   works
6. Claude is exercised only if portable auth exists in the environment
7. sibling builtins still resolve and run as before

Repo-wide gates before calling the slice done:

- `./.venv/bin/pytest -n0`
- `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
- `./.venv/bin/ruff check weft extensions tests`
- `./.venv/bin/ruff format --check weft extensions tests`

## Observable Success

Success is not just “tests pass.” It must be observable in operator terms:

- one public `example-dockerized-agent` spec name accepts explicit
  `--provider`
- the queued task is a concrete explicit TaskSpec, not a deferred selector
- `weft run --spec` remains the only submission path
- existing unparameterized specs behave exactly as before
- the provider-specific sibling builtins still work
- docs explain the difference between:
  - TaskSpec template
  - parameterization
  - run-input shaping
  - agent prompt templates

## Out of Scope

These are tempting, but not part of this plan:

- parameterized pipeline specs
- API-level or server-side materialization
- a generic spec-overlay language
- deleting the provider-specific example builtins
- provider auto-selection from `probe-agents`
- runner/plugin/provider preflight during ordinary `weft run`
- making Claude auth portable automatically
