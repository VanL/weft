# Public Python Construction and Extension Contracts

Status: completed
Source specs: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3], [PY-4]; docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/01-Core_Components.md [CC-3]; docs/specifications/06-Resource_Management.md [RM-5.1]
Superseded by: none

Class: 5. Public import and extension contracts change; hardening applies.
Plan type: implementation with spec revision.

## Goal

Expose the existing task construction and context types through the client,
and make declared runner/agent contracts usable without private imports.
Keep the root metadata-only, commands public, and runtime implementation private.

## Source Documents

- [Python API surfaces](../specifications/14-Python_API_Surfaces.md) [PY-1], [PY-3], [PY-4].
- [TaskSpec](../specifications/02-TaskSpec.md) [TS-1], [TS-1.3].
- [Core components](../specifications/01-Core_Components.md) [CC-3].
- [Resource management](../specifications/06-Resource_Management.md) [RM-5.1].
- [Agent runtime](../specifications/13-Agent_Runtime.md) [AR-5].
- [Engineering principles](../agent-context/engineering-principles.md),
  [hardening](../agent-context/runbooks/hardening-plans.md), and
  [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md).

## Spec Baseline

Base commit: `a1e7d496437545579985f63d2fa130666b64a162`.
The API spec also contains independent task-state changes at authoring time.
The input snapshot at `/tmp/weft-public-api-baseline` records the exact source
files read for this slice; preserve unrelated changes. No commits are authorized.

Promotion baseline: API spec SHA-256 `2f5db24c5612b43f7833f5a32c026ba86f634447d4b661c84c6ac60dd0e57ef5` after
the reviewed prose promotion, before implementation mappings. Verification:
`sha256sum docs/specifications/14-Python_API_Surfaces.md` (later mapping edits
are recorded with final evidence).

## Context and Key Files

- `weft/client/__init__.py` owns the public client inventory; `_client.py`
  accepts a private-import-only TaskSpec and WeftContext today.
- `weft/core/taskspec/model.py` owns all validation; reuse its classes, never
  introduce a second schema. `weft/context.py::build_context` owns resolution.
- `weft/ext.py` owns public runner protocols but refers to private value and
  concrete session types. `core/runners/outcome.py`, `core/resource_monitor.py`,
  `core/agents/runtime.py`, and `core/tasks/sessions.py` own those definitions.
- `core/tasks/interactive.py` and `consumer.py` consume backend sessions.
  First-party extensions must continue to satisfy the structural contracts.
- `tests/architecture/test_import_boundaries.py` pins inventories and layering.
  Update the declared narrow facade re-export exception, not general core access.
- Update API spec, Overview implementation map, README examples, and plan index.
  Synchronize moved value-owner mappings in Core Components, Resource
  Management, and Agent Runtime; add reciprocal plan links in TaskSpec.

Comprehension checks: the submission seam alone assigns resolved TIDs and queue
names; context construction owns optional directory/database creation. Session
protocols describe existing operations, not new process ownership or cleanup.

## Invariants and Constraints

No changes to queue formats, TIDs, state transitions, TaskSpec validation,
immutability, reserved policy, process spawning, or timeout/cleanup behavior.
No new dependencies or CLI behavior. No broad core exports or standalone
third-party backend SDK promise. Public types remain the actual production
classes. Moving value definitions must preserve object identity at internal
import sites and spawn/pickle round trips. Existing internal imports are not
newly supported public paths. No second execution or configuration path.

## Proposed Spec Delta

Promotion strategy A: promote requirement prose into [PY-1]/[PY-4] first;
add implementation mapping and reciprocal references with code. Overview uses
strategy D to correct stale facade prose in the same documentation slice.

Insert in [PY-1], replacing the old exact ext inventory paragraph:

> `weft.ext.__all__` exports `RunnerHandle`, `RunnerCapabilities`,
> `RunnerRuntimeDescription`, `AgentResolverResult`, `AgentToolProfileResult`,
> `AgentMCPServerDescriptor`, `RunnerEnvironmentProfileResult`, `AgentResolver`,
> `AgentToolProfile`, `RunnerEnvironmentProfile`, `TaskRunnerBackend`,
> `RunnerPlugin`, `SpecRunInputRequest`, `ResourceMetrics`, `RunnerOutcome`,
> `SessionExecutionResult`, `NormalizedAgentMessage`, `NormalizedAgentWorkItem`,
> `CommandSessionProtocol`, and `AgentSessionProtocol`.

Insert in [PY-1]:

> The client additionally exports `TaskSpec`, `SpecSection`, `IOSection`,
> `StateSection`, `LimitsSection`, `RunnerSection`, `ReservedPolicy`,
> `AgentSection`, `AgentTemplateSection`, `AgentToolSection`,
> `ParameterizationSection`, `ParameterizationArgumentSection`,
> `RunInputSection`, `RunInputArgumentSection`, `RunInputStdinSection`,
> `WeftContext`, and `build_context`. These are the existing validated models
> and context owner, not parallel representations. Supported TaskSpec use is
> construction, validation, field inspection, and serialization. Lifecycle and
> transport helpers remain runtime internals. Template validation uses
> `TaskSpec.model_validate(payload, context={"template": True,
> "auto_expand": False})`; normal construction retains resolved expansion.
> Submission remains responsible for committing the task and its TID.
>
> `build_context` retains its existing arguments and side effects: by default
> it resolves configuration and creates the metadata directories and broker;
> `create_dirs=False` and `create_database=False` disable those respective
> creation steps; resolution can still create the project root. A supplied
> context is preserved by `WeftClient`. The context
> does not own a permanently open broker; callers close queues they obtain and
> use the broker context manager to release its resources. Context fields are
> snapshots for use, not a promise that mutating configuration reconfigures live
> objects. Broker types remain SimpleBroker's public contracts.
>
> Extension value types preserve their existing fields and behavior.
> `CommandSessionProtocol` exposes readonly `pid`, `handle`, `last_metrics`;
> `send(data)`, `close_stdin()`, `poll_stdout()`, `poll_stderr()`, `is_alive()`,
> `returncode()`, `terminate(*, deadline=None)`, `close()`, `poll_limits()`, and
> `stop_monitor()`. `AgentSessionProtocol` exposes readonly `pid`, `handle`,
> `execute(work_item, *, cancel_requested=None) -> SessionExecutionResult`, and
> `close(*, deadline=None)`. Backend session methods return these structural
> protocols; concrete process session constructors remain private. These types
> preserve existing lifecycle semantics and introduce no new cleanup guarantee.
> Agent callbacks receive the `AgentSection` publicly available from
> `weft.client` and normalized work-item types from `weft.ext`.
> First-party runners may still use private implementation under coordinated
> versioning; these exports do not promise a complete standalone backend SDK.

Replace [PY-4] type-import allowance with:

> Type-checking-only extension annotations may refer to public client schema
> types. The client package initializer may re-export only the declared schema
> types from `weft.core.taskspec.model`; this is a value-type facade exception,
> not permission for client operations to call core. Runtime ext imports never
> point into core, commands, or adapters. Core initializers remain markers.

Replace Overview's stale public facades bullet with:

> **Public Python surfaces**: application client and task construction in
> `weft/client/`, CLI-equivalent capabilities in `weft/commands/`, and extension
> contracts in `weft/ext.py`; `weft/__init__.py` exposes package metadata only.

### Ownership mapping clarification (strategy D)

Update existing implementation references without behavioral changes:
`weft/core/runners/outcome.py::RunnerOutcome` becomes
`weft/ext.py::RunnerOutcome`; `weft/core/resource_monitor.py::ResourceMetrics`
becomes `weft/ext.py::ResourceMetrics`. Agent Runtime [AR-3.1] names
`weft/ext.py::NormalizedAgentMessage` and `NormalizedAgentWorkItem` as the
value owners while normalization functions remain in core. Add reciprocal
plan links to these touched specs and TaskSpec. This implements the original
traceability scope rather than changing runtime behavior.

## Tasks and Verification

1. Review plan and exact spec delta independently; promote the requirements.
2. Client slice: add declared exports, type TaskSpec/mapping client input where
   applicable without changing validation, and test construction/invalid models,
   frozen fields, explicit context ownership and preparation through public imports.
3. Extension slice: move pure value definitions to ext, introduce the two
   structural session protocols, update consumers' annotations, and preserve
   first-party implementations. Test real value behavior and pickle round trips;
   compile a plugin using only public imports with mypy and reject an invalid
   session/result. Do not mock queues/processes in existing regression suites.
4. Integrate inventories and exact layering exception. Test that normal client
   core calls remain forbidden and that fresh imports resolve in either order.
   Update README, spec mappings, review findings, and evidence.

Before edits, reproduce missing imports with a small failing public API probe.
Run `./.venv/bin/python -m pytest -n 0 tests/architecture/test_import_boundaries.py`
and new focused public API tests, then TaskSpec/context/runner/session/agent tests.
Run `bin/mypy-check`, Ruff check/format, spec metadata/hygiene checks, and full
four-worker default suite after integration. Existing end-to-end process tests
exercise moved pickle classes across spawn. Reject incompatible plugin shapes
with actual mypy, not string or annotation-presence tests.

Stop and revisit if model validation changes, runtime reverse imports appear,
new session operations are needed, or protocol conformance requires casts that
hide missing behavior. Do not weaken inventory/layering tests globally.

## Rollout and Rollback

One coordinated package change; no persistence migration or one-way data door.
New value locations change Python pickle module names, so mixed-version live
process upgrades are unsupported. Existing processes finish before upgrading.
Rollback is a coordinated package rollback; do not remove another task's edits.
No lifecycle resources or cleanup paths are added.

## Review Record

Fresh-eyes self-review: caught a TaskSpec-to-ext import cycle if AgentSection
were runtime-re-exported by ext. Keep schema ownership and public client import,
using a type-checking-only callback annotation. Also limited agent session
protocol to members the consumer actually uses. Independent review: compare_taut PASS after inspecting the exact delta and
implementation. Accepted guardrails: client input annotations use a
TYPE_CHECKING public-facade import; public-only mypy probes cover agent
resolver/tool-profile callbacks as well as runner/session protocols.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

Review clarification: context flags disable metadata-directory and broker
creation, not project-root creation. Existing behavior was verified and the
proposed/promoted prose clarified before completion. No behavior change.

## Outcomes

- [x] Public construction/context imports and behavior verified.
- [x] Public extension types and structural session contracts verified.
- [x] Full checks and independent review recorded.


## Verification and Final Review

- Missing-import reproduction failed with `ImportError: cannot import name
  'TaskSpec' from 'weft.client'` before implementation.
- Client: four new public contract probes and 110 existing client/context cases
  passed. The invalid model probe asserts the `spec.type` literal-validation
  error so a missing TID cannot satisfy it accidentally.
- Extension: 221 nearby runner/agent cases passed; eight public contract cases
  passed after revision. Public-only plugin fixture goes through actual
  TaskRunner dispatch and stream callbacks, not merely its own fake methods.
- Five moved value classes have identical executable ASTs to the input snapshot:
  ResourceMetrics, RunnerOutcome, SessionExecutionResult, NormalizedAgentMessage,
  and NormalizedAgentWorkItem. Pickle round trips and existing spawn tests pass.
- Architecture: 54 cases passed, including the narrow schema exception and both
  public import orders. Strict mypy: 421 source files clean. Ruff check clean;
  formatter check clean. Metadata/spec hygiene: six cases passed.
- Independent native reviewer: plan PASS, client/architecture slice PASS,
  final extension/integrated review PASS. No blocking findings. This was a
  same-family independent review; a different-family CLI is available but was
  not used for this bounded, behavior-preserving implementation.
- Accepted review improvements: documented project-root creation, preserved
  public type-only schema annotations, corrected invalid-model oracle, and
  replaced fixture self-tests with real TaskRunner plugin dispatch.
- Full default suite: 4,788 passed, 14 PostgreSQL-only skips, one test-policy
  failure in 437.96 seconds. The two new test modules had local shared markers
  but lacked their entries in `tests/conftest.py::_SHARED_MODULES`. Added those
  exact two entries. Policy, public-contract, and architecture rerun: 72 passed
  in 9.10 seconds. The whole suite was not repeated after this classification-only
  correction; no runtime code changed after the full run.
- Final Ruff and formatter checks pass (854 files); mypy passes (421 files);
  `git diff --check` passes. No live-provider or PostgreSQL run was needed for
  this import/type-only change; existing default spawn tests exercised runtime
  serialization. Optional backends remain bounded by their existing coverage.

Final correction review: PASS. Independent comparison against the saved
baseline confirmed exactly two `_SHARED_MODULES` additions and no other
classification or fixture changes.
