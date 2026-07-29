# Validation Capability and CLI Layering Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.3]; docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0], [IP-1.1]; docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
Superseded by: none

Class: 5 — Spec-changing. This plan changes the documented contract of the
public Python validation surface and moves CLI presentation across a package
boundary. The hardening checklist and independent review are mandatory before
implementation.

## 1. Goal

Make `WeftClient.specs.validate(..., load_runner=True|preflight=True)` honor the
options it already accepts, while restoring the intended
`cli -> commands -> core` ownership boundary. Two compatibility-preserving
ingestion adapters (raw JSON text for the CLI, parsed Path/mapping input for the
client) will converge on one command-layer validation engine. That engine will
perform schema, adapter, runner, environment, agent-runtime, and tool-profile
validation and return a structured result. A dedicated CLI adapter will render
that result with the current Rich output and exit codes.

This is the first of four independent plans extracted from
[`2026-07-29-structural-review-remediation-plan.md`](./2026-07-29-structural-review-remediation-plan.md).
It does not depend on the other three plans and must be independently
revertible.

## 2. Source Documents

Governing specifications:

- `docs/specifications/01-Core_Components.md` [CC-3.3] defines validation and
  preflight ordering.
- `docs/specifications/09-Implementation_Plan.md` [IP-1], [IP-1.0], and
  [IP-1.1] define the CLI, command capability, and public Python client
  boundaries.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.4.1] defines
  `weft spec validate`, `--load-runner`, `--preflight`, and the rule that
  preflight implies runner loading.
- `docs/specifications/07-System_Invariants.md` remains the standing invariant
  registry.

Required local guidance:

- `CLAUDE.md` §4.1, §4.2, §4.5, §4.7, §4.8, and §4.11
- `docs/agent-context/engineering-principles.md`, especially the boundary rule
  "validate at the boundary, then stay strict"
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Non-normative review input:

- `docs/plans/2026-07-29-structural-review-remediation-plan.md`, Task 2. This
  plan replaces that task with a narrower and more explicit implementation
  boundary.

## 3. Context and Key Files

### Current behavior

`weft/commands/validate_taskspec.py` currently owns all of these concerns:

- explicit path and stored-spec resolution
- TaskSpec schema validation
- parameterization and run-input adapter validation
- environment-profile, runner, agent-runtime, and tool-profile validation
- Rich tables and styling
- direct terminal writes through a module-level `Console`
- CLI exit-code selection

`weft/cli/app.py::spec_validate` delegates the whole task-spec branch to that
command handler. In contrast,
`weft/commands/specs.py::validate_spec_source` is the structured capability
used by `WeftClient.specs.validate`. It accepts `load_runner` and `preflight`
but immediately deletes both arguments. A caller therefore receives
`valid=True` after schema validation even when the requested runner or runtime
cannot be loaded.

The existing CLI tests already cover most concrete runner and runtime probes.
The missing proof is that the Python client reaches the same capability and
that rendering can move without changing output.

### Current and target flow

```text
CURRENT

weft spec validate
        |
        v
weft/cli/app.py
        |
        v
weft/commands/validate_taskspec.py
  [resolution + validation + Rich + exit code]

WeftClient.specs.validate
        |
        v
weft/commands/specs.py::validate_spec_source
  [schema only; load_runner/preflight discarded]


TARGET

weft spec validate
        |
        v
weft/cli/validate_taskspec.py
  [CLI resolution/read errors + Rich rendering + exit code]
        |
        | raw JSON text + resolved bundle root
        v
weft/commands/specs.py::validate_task_spec_text
        |
        | parsed payload
        v
_validate_task_spec_payload
  [one structured, short-circuiting validation engine]
        |
        +--> core TaskSpec and adapter validators
        +--> core environment and runner validators
        +--> core agent-runtime and tool-profile validators

WeftClient.specs.validate
        |
        v
weft/commands/specs.py::validate_spec_source
  [preserves Path read/parse exception compatibility]
        |
        | parsed payload serialized to canonical JSON
        v
validate_task_spec_text -> _validate_task_spec_payload
```

The two entry functions are ingestion adapters, not separate validation
implementations. Only `validate_task_spec_text` performs TaskSpec schema
validation, and only `_validate_task_spec_payload` performs post-schema adapter
and preflight work.

### Files to modify

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/09-Implementation_Plan.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/commands/specs.py`
- `weft/commands/types.py`
- `weft/commands/__init__.py`
- `weft/cli/app.py`
- new `weft/cli/validate_taskspec.py`
- delete `weft/commands/validate_taskspec.py` after its capability logic and
  presentation logic have moved to their owning layers
- `tests/commands/test_specs.py`
- `tests/core/test_client.py`
- `tests/cli/test_cli_validate.py`
- `tests/architecture/test_import_boundaries.py`
- `docs/plans/README.md`

### Read first

- `weft/commands/validate_taskspec.py` in full. It is the only current owner of
  the complete ordered validation path.
- `weft/commands/specs.py::validate_spec_source`,
  `resolve_spec_reference`, and `validate_spec`
- `weft/commands/types.py::SpecValidationResult`
- `weft/client/_namespaces.py::SpecNamespace.validate`
- `weft/cli/app.py::spec_validate`
- `tests/cli/test_cli_validate.py` in full
- `tests/commands/test_specs.py`
- `tests/core/test_client.py::test_specs_namespace_create_validate_and_delete_roundtrip`
  and `test_specs_namespace_validate_uses_bound_client_context`

### Existing paths to reuse

- `SpecValidationResult` remains the public return type. Extend it additively;
  do not replace it with a parallel result hierarchy.
- `resolve_spec_reference` remains the CLI stored/builtin-name resolution path;
  the adapter preserves its current explicit-path branch separately.
- `read_spec_json` remains the client Path ingestion boundary. Its malformed
  JSON exception behavior is preserved before delegation to the shared
  validation engine.
- `validate_taskspec`, `validate_parameterization_adapter`,
  `validate_run_input_adapter`, `validate_taskspec_runner_environment`,
  `validate_taskspec_runner`, `validate_taskspec_agent_runtime`, and
  `validate_taskspec_agent_tool_profile` remain the validators. Do not copy or
  wrap their policy in a second implementation.
- `apply_bundle_root_to_taskspec_payload` and
  `bundle_root_from_taskspec_payload` remain the bundle-local import mechanism.
- `tests/fixtures/provider_cli_fixture.py` and the TaskSpec factories under
  `tests/taskspec/fixtures.py` provide real deterministic runtime descriptors.

### Comprehension questions

Before editing, the implementer must be able to answer:

1. Which function currently discards `load_runner` and `preflight`, and which
   public client method reaches it?
2. In what order does the current CLI run schema, adapter, environment,
   runner, agent-runtime, and tool-profile checks, and where does it stop?
3. Why must the capability validate a copy with bundle-root metadata while
   returning the caller-visible payload without new internal metadata?
4. Which layer owns exit codes and Rich output after this change?

## 4. Invariants and Constraints

Must remain unchanged:

- `weft spec validate` human stdout, stderr, and exit codes for every existing
  case. Task-spec validation failure remains exit 1. Invocation/type misuse
  remains exit 2.
- `--preflight` implies `--load-runner`.
- Validation remains short-circuiting in the current order. Do not run later
  probes after an earlier stage fails.
- `weft run` does not gain an implicit preflight gate.
- Pipeline validation does not start loading task runners.
- Bundle-local environment profiles, parameterization adapters, and run-input
  adapters continue to resolve from the bundle root.
- Validation does not mutate a mapping supplied by the client and does not add
  internal bundle-root metadata to `SpecValidationResult.payload`.
- Malformed JSON preserves the existing surface-specific ingestion behavior:
  the CLI receives a structured `_json` schema failure and exit 1, while
  `WeftClient.specs.validate(Path(...))` continues to raise from
  `read_spec_json`. Both surfaces share all validation after successful
  ingestion.
- Existing `SpecValidationResult` fields keep their meaning:
  `errors` contains fatal messages; `warnings` contains non-fatal findings;
  `valid` is false after any failed requested validation stage.
- Probe exceptions are converted into structured validation failures. They do
  not escape through `WeftClient.specs.validate`.
- No TaskSpec, queue, manager, worker, persistence, or execution behavior
  changes.

Layering constraints:

- `weft/commands/` must not import Rich, Typer, `weft.cli`, or `weft.client`.
- `weft/cli/validate_taskspec.py` owns presentation and exit-code adaptation.
- `weft/cli/app.py` remains registration and argument parsing. Do not paste the
  renderer into `app.py`.
- Do not add a renderer protocol, validation service class, plugin registry, or
  second validation result type.
- Do not address the broader `weft.commands` package facade or other import
  cycles in this plan. The independent import-cycle plan owns those changes.

Public structured-result contract:

- Add `errors_by_stage: dict[str, dict[str, str]]` to
  `SpecValidationResult` with an empty default, preserving source
  compatibility for existing constructors.
- Stage keys are:
  `options`, `schema`, `parameterization`, `run_input`,
  `environment_profile`, `runner`, `agent_runtime`, and `tool_profile`.
- The outer mapping contains fatal stages only, in execution order. The current
  short-circuit contract means it normally contains zero or one stage. Each
  inner mapping preserves the field-to-message rows rendered by the current
  CLI; schema validation may return several rows, while later probes normally
  return one.
- `errors` remains the ordered flat list of the inner mappings' messages for
  callers that do not need stage or field detail.
- `warnings` does not carry failed preflight probes.

Review gates:

- no new dependency
- no CLI output drift
- no second schema or post-schema validation path; two thin ingestion adapters
  are required for source-compatibility and must converge immediately
- no mock-only proof of runner or runtime behavior
- independent plan review before the spec-promotion slice
- fresh-eyes review before the plan is declared implementation-ready

Rollback and rollout:

- There is no persisted state or staged runtime rollout.
- The code slice is reverted as one unit: restore
  `weft/commands/validate_taskspec.py`, its imports, and the prior
  `validate_spec_source` implementation.
- Keep `rich>=14.0.0` in `pyproject.toml`. Rich is still an intentional CLI
  dependency; only its layer changes.
- This is not a one-way door.

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 4b. Spec Baseline

- `a391a59b345a8e37dc6d5c362525f84be0f70343` —
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/09-Implementation_Plan.md`, and
  `docs/specifications/10-CLI_Interface.md` at plan authoring time
- Plan type: **implementation with spec revision**
- Promotion baseline identifier:
  `671e7939a8d86e08f47e1fa237afa4e17ba8eef4`

## 4c. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/01-Core_Components.md` | A — in-file text before implementation-link claims | [CC-3.3] |
| `docs/specifications/09-Implementation_Plan.md` | A — in-file text before implementation-link claims | [IP-1.1] |
| `docs/specifications/10-CLI_Interface.md` | No behavioral delta; update mapping only with code | [CLI-1.4.1] |

### [CC-3.3] — append after the existing validation-layer bullets

> Task-spec validation and optional preflight are shared command-capability
> behavior. Source-specific ingestion may preserve established error contracts,
> but all successfully ingested task specs converge on the same ordered
> validation engine and structured result. CLI adapters own terminal rendering
> and exit-code selection.
>
> Every public validation surface that accepts runner-loading or preflight
> options must honor them through that shared capability. Preflight implies
> runner loading. A failed requested validation or preflight stage produces an
> invalid result with a fatal error; it is not downgraded to a warning and is
> not silently skipped. A surface that cannot honor an option rejects it
> explicitly.

### [IP-1.1] — append to the public-client behavior bullets

> `WeftClient.specs.validate` uses the same ordered task-spec validation
> capability as `weft spec validate`. Its `SpecValidationResult` preserves the
> existing `valid`, `spec_type`, `errors`, `warnings`, and `payload` fields and
> exposes fatal errors by validation stage. A failed requested stage sets
> `valid` false; `warnings` remain non-fatal. Pipeline validation explicitly
> rejects task-only runner-loading and preflight options.

Implementation mappings are deliberately not part of the promotion slice.
Task 4 updates them atomically with the new ownership and reciprocal code
docstrings.

## 5. Tasks

### Task 1 — Promote the behavioral spec delta

- **Outcome:** [CC-3.3] and [IP-1.1] contain the exact behavior from §4c before
  code begins relying on it.
- **Files:** `docs/specifications/01-Core_Components.md`,
  `docs/specifications/09-Implementation_Plan.md`
- **Actions:**
  1. Insert the two exact deltas.
  2. Add this plan to each spec's `## Related Plans` section.
  3. Do not change implementation mappings yet.
  4. Record the promotion baseline identifier in §4b.
- **Stop if:** the proposed behavior conflicts with existing CLI flag semantics
  or requires changing `weft run`.
- **Verify:** `./bin/check-doc-paths` and the relevant spec/plan metadata tests.
- **Done when:** the text is promoted, backlinks exist, and the baseline is
  recorded.

### Task 2 — Build one structured task-spec validation capability

Red-green TDD is required for the discarded-option defect.

- **Outcome:** `validate_task_spec_text` and `validate_spec_source` converge on
  one post-ingestion validation engine; every requested task-spec check runs;
  fatal stage detail is returned; task-only options are explicitly rejected
  for pipelines.
- **Files:** `weft/commands/specs.py`, `weft/commands/types.py`,
  `tests/commands/test_specs.py`, `tests/core/test_client.py`
- **Approach:**
  1. Add the failing client regression first using a real provider-runtime
     descriptor whose executable path does not exist. Assert
     `valid is False`, `warnings == []`, and
     `errors_by_stage["agent_runtime"]["agent_runtime"]` names the missing
     executable. Prove the test is red against `a391a59b`.
  2. Add `errors_by_stage` to `SpecValidationResult` with an empty default.
  3. Move the two adapter-validation helpers from
     `weft/commands/validate_taskspec.py` into `weft/commands/specs.py`.
  4. Add `validate_task_spec_text(json_content, *, bundle_root, load_runner,
     preflight)`. It is the raw-text ingestion adapter. It calls
     `validate_taskspec(json_content)` so malformed JSON retains the current
     `_json` schema row, parses JSON only after schema success, then delegates
     all remaining work.
  5. Add one private post-schema function in `specs.py`. It receives the parsed
     payload, bundle root, `load_runner`, and `preflight`; runs adapter and
     preflight validators in the current order; and returns
     `SpecValidationResult`. It must not repeat schema validation.
  6. Make `validate_spec_source` preserve its existing ingestion contracts:
     Path input still uses `read_spec_json` and therefore retains its malformed
     JSON exception; mapping input is copied. For task specs, serialize the
     successfully ingested payload and delegate to
     `validate_task_spec_text`. For a Path named `taskspec.json`, use its parent
     as the bundle root; for mapping input, preserve any existing bundle-root
     metadata through `bundle_root_from_taskspec_payload`. Pipeline validation
     remains in its existing branch.
  7. If `preflight` is true, normalize `load_runner` to true once at the
     capability boundary.
  8. Validate a deep copy after applying bundle-root metadata. Return an
     unmodified copy of the caller-visible payload.
  9. Convert the first caught probe exception to the appropriate outer stage
     and inner field entry, copy its message into `errors`, set `valid=False`,
     and stop. Preserve every field row returned by schema validation rather
     than collapsing it into one string.
  10. For pipeline input with either task-only option, return an invalid result
     with the `options` stage. Do not call task runner validators.
- **Required command-level matrix:**

  | Source | Options | Expected result |
  |--------|---------|-----------------|
  | malformed raw JSON text | none | invalid at `schema` with the existing `_json` field row |
  | malformed JSON Path through `validate_spec_source` | none | existing `read_spec_json` exception preserved |
  | malformed task mapping | none | invalid at `schema`; every schema field row preserved |
  | task mapping with missing parameterization adapter | none | invalid at `parameterization` |
  | task mapping with missing run-input adapter | none | invalid at `run_input` |
  | task mapping with missing environment profile | `load_runner=True` | invalid at `environment_profile` |
  | valid function mapping with missing configured runner | none | valid; schema-only behavior preserved |
  | same mapping | `load_runner=True` | invalid at `runner` |
  | valid host function mapping | `preflight=True` | valid; proves preflight implies load |
  | provider CLI mapping with missing executable | `preflight=True` | invalid at `agent_runtime` |
  | provider CLI mapping with unsupported tool profile | `preflight=True` | invalid at `tool_profile` |
  | valid pipeline mapping | none | valid |
  | valid pipeline mapping | `load_runner=True` | invalid at `options`; no runner probe |
  | valid pipeline mapping | `preflight=True` | invalid at `options`; no runner probe |
  | relative bundle path through a bound client context | applicable options | bundle-local adapters still resolve |

- **Not allowed:** a second schema or post-schema validation implementation, a
  caller-mode flag, exception-string reconstruction, a second public result
  type, string-prefix parsing in the CLI, patching the runner validators in the
  primary regressions, or changing validator order.
- **Stop if:** exact CLI headings cannot be derived from `errors_by_stage` and
  the payload, or the implementation needs presentation strings inside
  `commands/`.
- **Verify:**
  `./.venv/bin/python -m pytest tests/commands/test_specs.py tests/core/test_client.py -q`
- **Done when:** the red client test and the full matrix pass without mocks
  around runner or runtime validation.

### Task 3 — Move task-spec rendering into a focused CLI adapter

- **Outcome:** task-spec validation rendering lives in `weft/cli/`; the CLI
  uses the shared capability; stdout, stderr, and exit codes are unchanged.
- **Files:** new `weft/cli/validate_taskspec.py`,
  `weft/cli/app.py`, `weft/commands/__init__.py`, delete
  `weft/commands/validate_taskspec.py`, update
  `tests/cli/test_cli_validate.py`
- **Approach:**
  1. Before moving code, add deterministic characterization cases for complete
     stdout, stderr, and exit code. Set a fixed terminal width and color mode
     in the subprocess environment so Rich output is byte-stable.
  2. Move `_display_taskspec_summary` and `_display_validation_errors` to the
     new CLI module.
  3. Implement `cmd_validate_taskspec` in the new CLI module. Preserve the
     current explicit-path heuristic (`.json`, absolute paths, and multi-part
     paths), directory-to-`taskspec.json` handling, and their exact missing-file
     diagnostics. Use `resolve_spec_reference` only for the existing
     non-explicit stored/builtin-name branch. Preserve the current raw-text read
     and error handling, then call `validate_task_spec_text` with that text and
     the resolved bundle root. Render the ordered result and return the same
     integer exit code as today.
  4. Preserve the exact special headings for schema, parameterization,
     run-input, environment-profile, runner, agent-runtime, and tool-profile
     failures.
  5. Change `weft/cli/app.py` to import the handler from the new CLI module.
  6. Remove the command-package re-export and delete the old module only after
     no caller imports it.
- **Characterization cases:**
  - valid function TaskSpec without runner loading
  - missing runner under `--load-runner`
  - provider executable missing under `--preflight`
  - bundle-local adapter success
  - missing explicit file
  - pipeline with a task-only option
- **Adversarial acceptance checks:** every failure has no traceback; missing
  input and unsupported flag combinations retain their documented exit-code
  classes; malformed JSON reports a clean diagnostic.
- **Not allowed:** adding Rich to `commands/`, putting renderer helpers in
  `app.py`, or changing output to make the new renderer easier.
- **Stop if:** an existing CLI assertion must change, except to strengthen a
  substring assertion into an exact characterization assertion.
- **Verify:**
  `./.venv/bin/python -m pytest tests/cli/test_cli_validate.py tests/cli/test_commands.py -q`
- **Done when:** all characterization cases match the baseline byte-for-byte
  and `rg -n "from rich|import rich" weft/commands` returns no matches.

### Task 4 — Lock the boundary and reconcile traceability

- **Outcome:** architecture tests prevent presentation from returning to the
  capability layer, and spec/code ownership descriptions match the new tree.
- **Files:** `tests/architecture/test_import_boundaries.py`,
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/09-Implementation_Plan.md`,
  `docs/specifications/10-CLI_Interface.md`,
  module docstrings in `weft/commands/specs.py` and
  `weft/cli/validate_taskspec.py`
- **Actions:**
  1. Extend the existing third-party import boundary loop so Rich, like Typer,
     is permitted only under `weft.cli`.
  2. Update [CC-3.3], [IP-1], and [CLI-1.4.1] implementation mappings to name
     `weft/commands/specs.py` as the structured capability and
     `weft/cli/validate_taskspec.py` as the renderer/exit adapter.
  3. Add reciprocal `Spec:` references to the two owning module docstrings.
  4. Close every Deviation Log row. No `pending` proposal may remain.
- **Not allowed:** broadening this test into the all-module cycle gate. The
  import-cycle plan owns that graph and its exclusions.
- **Verify:** architecture tests, plan metadata, doc paths, mypy, and ruff.
- **Done when:** the Rich boundary test fails under a deliberate
  `weft.commands` Rich import, passes after restoration, and every ownership
  mapping is reciprocal.

## 6. Testing Plan

### Test layers

```text
STRUCTURED CAPABILITY
  tests/commands/test_specs.py
    raw malformed JSON -> structured schema result
    malformed Path -> existing client exception
    schema-only success
    load-runner failure
    preflight success and failure
    pipeline option rejection
    bundle-root preservation
           |
           v
PUBLIC CLIENT
  tests/core/test_client.py
    real client context reaches the same preflight path
           |
           v
CLI ADAPTER
  tests/cli/test_cli_validate.py
    exact rendering and exit-code characterization
           |
           v
ARCHITECTURE
  tests/architecture/test_import_boundaries.py
    Rich confined to weft.cli
```

Use real TaskSpec payloads and the existing deterministic provider CLI fixtures.
No broker or manager harness is required for pure validation, but
`WeftTestHarness` remains appropriate for tests that need a bound client
context and stored spec paths.

Do not mock:

- the runner loader in the primary `load_runner` regression
- agent-runtime executable resolution in the primary preflight regression
- bundle-local adapter loading
- `validate_spec_source` when testing `WeftClient.specs.validate`

Mocking is acceptable only for a narrow negative assertion that proves a later
probe was not called after an earlier failure. Such a test supplements, and
does not replace, the real-path matrix.

### Failure modes

| Failure | Structured result | CLI behavior | Required proof |
|---------|-------------------|--------------|----------------|
| malformed schema | `schema` fatal error | current validation-failed table, exit 1 | existing CLI case plus command result assertion |
| missing adapter | adapter-stage fatal error | current layer-specific heading, exit 1 | bundle-local negative case |
| missing runner plugin | `runner` fatal error | current runner heading, exit 1 | real missing plugin |
| unavailable agent executable | `agent_runtime` fatal error | current runtime heading, exit 1 | real nonexistent path |
| task-only option on pipeline | `options` fatal error for client | current CLI invocation error, exit 2 | client and black-box CLI cases |
| malformed JSON | raw-text adapter returns `_json` schema failure; Path adapter preserves `read_spec_json` exception | current validation table, no traceback, exit 1 | command adapter tests plus black-box CLI and client compatibility cases |
| missing source | existing client source-read exception behavior remains unchanged | current diagnostic, no traceback, current exit class | black-box CLI acceptance case plus unchanged client test |

The tempting shortcut is to test only `preflight=True`. It is not sufficient:
`load_runner=True` has a distinct capability contract and pipeline inputs must
reject both flags independently.

## 7. Verification and Gates

Per-task:

```bash
./.venv/bin/python -m pytest tests/commands/test_specs.py tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_validate.py tests/cli/test_commands.py -q
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q
```

Final:

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft
./bin/check-doc-paths
./bin/check-dom15-fixtures
```

Manual observable proof:

1. Run the CLI and Python client against the same valid host TaskSpec with
   preflight. Both report success.
2. Run both surfaces against the same missing-runner and missing-provider
   executable cases. Both report the same failing stage and diagnostic.
3. Run `weft run echo hello` to confirm no hidden preflight gate was added.

Full-suite gates are required because the import location and public client
result type have broad import and compatibility blast radius.

## 8. Independent Review Loop

Independent review is required before Task 1.

The reviewer should read:

- this plan in full, including §4c
- [CC-3.3], [IP-1.0], [IP-1.1], and [CLI-1.4.1]
- `weft/commands/validate_taskspec.py`
- `weft/commands/specs.py::validate_spec_source`
- `weft/commands/types.py::SpecValidationResult`
- `tests/cli/test_cli_validate.py`

Review prompt:

> Review this plan and its proposed spec delta. Verify that a zero-context
> engineer can implement one shared validation capability without changing CLI
> output or creating a second path. Check the public result contract,
> short-circuit ordering, bundle-root handling, pipeline flag rejection,
> rollback, and test matrix. Prefer deleting ceremony over adding abstractions.
> Answer PASS or BLOCKED: could you implement it confidently as written, and
> would it avoid degrading behavior or layering?

Every finding receives an explicit disposition. A BLOCKED verdict must be
resolved before spec promotion. Run a second scoped verification only over
accepted changes.

## 9. Out of Scope

- Other `weft.commands` package cycles and the eager root facade
- The `host` ↔ `subprocess_runner` cycle
- Status reducer extraction
- Duplicate converter consolidation
- Vacuous property and queue-wiring test repairs
- Removing Rich from project dependencies
- New validation stages, changed probe order, or new preflight policy
- `weft run` preflight
- Pipeline runner loading

## 10. Fresh-Eyes Review

Completed as a separate pass after the first draft. Findings and dispositions:

1. **Renderer placement:** the source umbrella plan moved Rich rendering into
   `weft/cli/app.py`, which would mix command registration with a cohesive
   renderer. This plan instead creates `weft/cli/validate_taskspec.py`.
2. **Schema error shape:** a single error string per stage could not preserve
   the current multi-row schema error table. `errors_by_stage` is now a nested
   stage-to-field-to-message mapping, and every stage key has a firing test.
3. **Explicit-path behavior:** `resolve_spec_reference` alone would change the
   diagnostic for a missing explicit `.json` path. Task 3 now pins the current
   explicit-path heuristic and uses name resolution only for the current
   stored/builtin branch.
4. **Malformed JSON ingestion:** the first independent review found that the
   CLI returns a structured `_json` row while the client Path surface raises
   during `read_spec_json`. The plan now specifies two thin ingestion adapters
   converging on one schema engine and one post-schema engine. This preserves
   both contracts without a caller-mode flag or exception reconstruction.
5. **Coverage completeness:** the command-level matrix now fires every
   documented stage key, both pipeline option rejections, both malformed JSON
   ingestion behaviors, and bundle-root resolution.

Residual risk: exact Rich output characterization is sensitive to terminal
width and color mode, so Task 3 fixes both in the subprocess environment before
recording the baseline.

## 11. Independent Review Result

- Initial verdict: **BLOCKED**
- Blocking finding: the draft required malformed JSON to be both a structured
  CLI result and an unchanged client Path exception while sending both surfaces
  through the same Path entry function.
- Disposition: **accepted**. Added raw-text and Path/mapping ingestion adapters
  that converge on one schema and post-schema validation path; pinned explicit
  bundle-root passage and prohibited caller-mode flags and exception-string
  reconstruction.
- Scoped round-2 verdict: **PASS**
- Round-2 result: the fix preserves both ingestion contracts, shares all
  validation after ingestion, and introduced no new defect.

The planning review closed with no open blocker before implementation began.

## 12. Implementation Review and Verification

Implemented 2026-07-29.

External implementation reviewer: `claude -p`.

- Round 1: **BLOCKED**. The renderer treated parameterization/run-input
  failures as if every later preflight stage had completed, emitting false
  success lines. It also lacked the required byte-stable CLI characterization.
- Disposition: accepted. Pre-preflight failures now stop all preflight success
  rendering. Deterministic CLI tests pin the complete adapter-failure output,
  missing-file contract, and pipeline task-option rejection.
- Round 2: **PASS**. The reviewer verified the renderer correction and the
  characterization tests, with no new material finding.

Verification evidence:

- full suite: `2395 passed, 14 skipped`
- full mypy target: 193 source files, no issues
- Ruff: passed
- plan metadata and DOM-15 fixture gates: passed
- documentation path check: eight pre-existing dangling example paths; none
  introduced by this slice
