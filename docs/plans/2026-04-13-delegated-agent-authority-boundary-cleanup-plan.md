# Delegated Agent Authority Boundary Cleanup Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

This plan tightens the delegated-agent boundary around the new `provider_cli`
work without widening Weft into a full agent-governance system. The goal is to
make Weft's substrate-level authority contract explicit, deterministic, and
honest, while leaving higher-level agent policy, workflow, and operator choice
to systems built on top of Weft.

This is risky boundary work. It touches:

`TaskSpec -> validation -> TaskRunner -> provider_cli backend -> provider registry -> docs/tests`

The likely failure modes are predictable:

- keeping the current hidden authority widening through raw provider options or
  ambient provider config,
- claiming stronger safety guarantees in docs than the code actually enforces,
- pushing Weft into a broad capability-policy framework instead of a narrow
  substrate contract,
- splitting validation across schema, backend, and provider layers until they
  drift,
- or turning cleanup into a large provider-abstraction refactor that does not
  materially improve the trust boundary.

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. preserve the current durable spine and public queue contract
2. keep `llm` as the bounded lane and `provider_cli` as the delegated lane
3. make delegated authority class explicit at the Weft layer, but keep it
   minimal and substrate-scoped
4. make configuration precedence deterministic:
   Weft-selected authority and tool-profile policy may narrow provider
   behavior, and raw provider options may not silently widen it
5. collapse duplicated validation into one canonical validation path plus
   runtime-only provider checks
6. clean up hidden provider defaults that currently act as undeclared policy
7. include documentation cleanup in scope:
   specs, related-plan backlinks, nearby implementation notes, and non-obvious
   provider code comments/docstrings
8. strengthen verification around the real provider boundary enough that the
   documented contract is honest

Do not implement any of the following in this slice:

- a second scheduler, session manager, or orchestration path
- a broad policy engine for approvals, action governance, or operator UX
- domain-specific agent policy in core Weft
- durable threads or transcript persistence
- a general capability-negotiation framework wider than Weft needs here
- a broad provider inheritance hierarchy just to reduce line count
- a sandbox project beyond wiring the current runner model honestly

If implementation pressure starts pulling toward any excluded item, stop and
write a follow-on plan instead.

## Goal

Make the delegated agent lane explicit and internally consistent. Weft should
say what class of agent authority the user selected, enforce the small part of
that contract that belongs to Weft, and document the rest honestly. It should
not pretend host-delegated agents are safer than they are, and it should not
try to absorb all agentic control responsibilities from downstream systems.

## Design Position

The cleanup should lock in this mental model:

- Weft is a substrate. It owns task lifecycle, runner isolation, declared
  execution shape, queues, and durable truth.
- Weft does not own the full inner policy of external agent CLIs.
- Weft may expose a small authority declaration for agent tasks, but that
  declaration must stay coarse and honest.
- Tool profiles and runner isolation may narrow authority inside the chosen
  class.
- Raw provider options are escape hatches for provider tuning, not a way to
  silently widen authority beyond the Weft-declared class.

For this cleanup, the intended authority ladder is deliberately small:

- `bounded`: explicit context and explicit Weft-managed tool surface only
- `general`: delegated agent with broad host or runner authority, chosen
  explicitly or inherited for compatibility

If naming changes during implementation review, keep the shape the same:
one durable enum-like field, not multiple booleans.

## Source Documents

Primary specs:

- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-2.1], [AR-5], [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A1], [AR-A3], [AR-A4], [AR-A5], [AR-A8]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3], [TS-1.4]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [QUEUE.3], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.4.1]

Local guidance and review standards:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Existing plans to align with, not replace:

- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md)

Source spec note:

- no existing spec yet defines the minimal `bounded` vs `general` authority
  declaration cleanly enough for implementation; this plan includes that doc
  cleanup as part of the work

## Context and Key Files

Files that will likely change in this cleanup:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/core/taskspec.py`
- `weft/core/agent_validation.py`
- `weft/core/environment_profiles.py`
- `weft/core/agents/backends/provider_cli_backend.py`
- `weft/core/agents/provider_registry.py`
- `weft/core/tasks/consumer.py`
- `weft/_constants.py`
- `tests/core/test_agent_validation.py`
- `tests/core/test_provider_cli_backend.py`
- `tests/core/test_provider_cli_session_backend.py`
- `tests/core/test_environment_profiles.py`
- `tests/tasks/test_agent_execution.py`

Read first:

1. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
   sections [AR-0.1], [AR-2.1], [AR-5], [AR-7]
2. [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
   sections [AR-A3], [AR-A4], [AR-A5], [AR-A8]
3. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
   sections [TS-1], [TS-1.3], [TS-1.4]
4. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
5. [`weft/core/agent_validation.py`](../../weft/core/agent_validation.py)
6. [`weft/core/environment_profiles.py`](../../weft/core/environment_profiles.py)
7. [`weft/core/agents/backends/provider_cli_backend.py`](../../weft/core/agents/backends/provider_cli_backend.py)
8. [`weft/core/agents/provider_registry.py`](../../weft/core/agents/provider_registry.py)
9. [`tests/fixtures/provider_cli_fixture.py`](../../tests/fixtures/provider_cli_fixture.py)
10. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)

Current structure:

- `AgentSection` validation in `weft/core/taskspec.py` already enforces most
  schema-level `provider_cli` constraints.
- `ProviderCLIBackend._validate_agent()` duplicates those same constraints.
- `ProviderCLIBackend._merge_provider_options()` currently lets raw
  `spec.agent.options` overwrite tool-profile-derived provider options.
- `provider_registry.py` currently embeds hidden policy in provider invocation
  builders, including dangerous fallbacks and a silent
  `skip_git_repo_check=True` default.
- `environment_profiles.py` is already the single materialization path for
  runner environment defaults. It should stay that way.
- `tests/fixtures/provider_cli_fixture.py` proves the wrapper contract.
  `tests/tasks/test_agent_execution.py` contains the opt-in live-provider
  exercises for real CLIs.

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which layer should own schema validity, load validation, preflight checks,
   and provider execution checks after this cleanup?
2. Where can raw provider options currently widen effective authority, and
   what exact precedence rule should replace that behavior?
3. Which parts of delegated-agent safety belong to Weft substrate semantics,
   and which parts must remain outside Weft because they are provider or
   downstream-system policy?

## Invariants and Constraints

The following must stay true:

- The current durable spine stays the only execution path.
- TIDs, queue names, and forward-only task-state transitions remain unchanged.
- `spec` and `io` remain immutable after resolved `TaskSpec` creation.
- `spec.runner.name` remains the authoritative runner choice.
- Environment profiles may materialize runner inputs. They may not become a
  second runner-selection or orchestration path.
- The public outbox contract stays protocol-light. No new public result
  envelope is added.
- Existing stored TaskSpecs must remain loadable. Any new authority field must
  be additive and backward-compatible.
- Weft may expose a minimal authority class. It must not grow a broad
  approval-policy or autonomous-action framework in this cleanup.
- Docs must not promise capability guarantees that the code does not enforce.

Review gates for this slice:

- no new dependency
- no drive-by refactor
- no provider-abstraction rewrite unless directly required by an authority fix
- no mock-heavy substitute for the real provider invocation path when the
  fixture-backed path can prove the contract
- no spec drift between touched docs and code

## Rollout and Rollback

Rollout order matters:

1. lock the doc model first
2. add any new additive TaskSpec field and validation
3. change provider precedence and defaults
4. update tests and CLI/docs together

Compatibility rule:

- if an explicit authority field is added, existing stored delegated specs
  must continue to run by mapping the missing value to the current compatible
  behavior. Do not make older specs invalid in this cleanup.

Rollback rule:

- queue contracts, task-state records, and task-log payload shape must remain
  compatible enough that the cleanup can be reverted without data migration

## Tasks

1. Lock the authority model in the specs before code changes.
   - Outcome: the current and planned specs say plainly that Weft owns a small,
     substrate-level authority declaration, not full agent policy.
   - Files to touch:
     `docs/specifications/13-Agent_Runtime.md`,
     `docs/specifications/13A-Agent_Runtime_Planned.md`,
     `docs/specifications/02-TaskSpec.md`,
     `docs/specifications/10-CLI_Interface.md`
   - Read first:
     current [AR-0.1], [AR-2.1], [AR-7], planned [AR-A3], [AR-A4], [AR-A5],
     and [TS-1].
   - Make these doc changes:
     - describe `llm` as the bounded lane
     - describe `provider_cli` on `host` honestly as a delegated lane that may
       be broad unless narrowed by runner or tool-profile policy
     - replace any wording that implies "top lane is always read-only by
       default" with a clearer statement: bounded defaults are narrow; general
       delegated work is an explicit choice
     - define the minimal authority-class field or equivalent contract in one
       place only
   - Done signal: a zero-context reader can tell where Weft stops, what the
     authority classes mean, and which guarantees are current vs planned.
   - Stop and re-evaluate if this starts inventing a detailed policy matrix or
     domain-specific action model.

2. Add one canonical Weft-level authority declaration to `TaskSpec`.
   - Outcome: agent tasks have a single durable field for the chosen authority
     class. Use `bounded` vs `general` unless spec review chooses different
     names.
   - Files to touch:
     `weft/core/taskspec.py`,
     `weft/core/agent_validation.py`,
     `tests/core/test_agent_validation.py`,
     `tests/taskspec/test_taskspec.py` if needed
   - Read first:
     `AgentSection` and existing `provider_cli` validation in
     `weft/core/taskspec.py`.
   - Implementation rules:
     - the field must live at the Weft-owned agent contract level, not hidden
       inside provider-specific `runtime_config`
     - keep it additive and backward-compatible
     - missing value must map deterministically to current compatible behavior
       during rollout
     - do not add multiple flags such as `--limited-agent` and
       `--general-agent` as the canonical schema; CLI sugar can come later
   - Tests to add or update:
     - default mapping for existing `llm` and `provider_cli` specs
     - invalid authority values
     - incompatible authority/runtime combinations
   - Done signal: one field is the single source of truth for declared agent
     authority in Weft.
   - Stop and re-evaluate if implementation tries to encode provider-specific
     permission matrices in `TaskSpec`.

3. Make precedence deterministic and authority-narrowing authoritative.
   - Outcome: the effective provider invocation can no longer be widened
     silently by raw `spec.agent.options`.
   - Files to touch:
     `weft/core/agents/backends/provider_cli_backend.py`,
     `weft/core/agents/provider_registry.py`,
     possibly `weft/core/agent_validation.py`
   - Read first:
     `_merge_provider_options()`, `_prepare_execution()`, provider
     `build_invocation()` methods, and tool-profile resolution.
   - Implementation rules:
     - establish one explicit precedence order and document it in code and
       spec:
       authority class -> runner/tool profile -> provider options
     - provider options may tune behavior within the selected class but may not
       widen authority beyond it
     - if a user requests an incompatible widening option, fail clearly rather
       than silently accepting it
     - ambient MCP/extensions/home-directory behavior must be either explicitly
       allowed by the selected class or explicitly disabled where Weft claims
       a narrower mode
   - Tests to add or update:
     - raw provider option cannot override tool-profile-derived narrowing
     - dangerous delegated behavior is explicit and deterministic
     - provider-specific invalid combinations fail at the correct layer
   - Done signal: the same spec produces the same effective authority on two
     machines with different ambient provider defaults, except where the spec
     explicitly opts into general delegated behavior.
   - Stop and re-evaluate if the cleanup starts depending on undocumented
     provider internals that Weft cannot keep stable.

4. Collapse validation drift and clean up the hidden policy seams.
   - Outcome: schema/runtime validation has one canonical path, and the most
     important hidden defaults become explicit.
   - Files to touch:
     `weft/core/taskspec.py`,
     `weft/core/agent_validation.py`,
     `weft/core/agents/backends/provider_cli_backend.py`,
     `weft/core/agents/provider_registry.py`,
     `weft/_constants.py`
   - Read first:
     `AgentSection.validate_output_schema()`,
     `ProviderCLIBackend._validate_agent()`,
     provider option validation in `provider_registry.py`.
   - Required cleanup:
     - remove duplicated `provider_cli` shape validation from backend code once
       the schema path is sufficient
     - keep backend/provider checks only for execution-time or provider-surface
       concerns
     - move inline provider probe timeouts into `_constants.py`
     - make `skip_git_repo_check` an explicit validated policy choice or a
       clearly documented unconditional behavior, not a silent default hidden
       in invocation building
     - add a docstring or strong comment for Gemini session-home shaping
   - Optional cleanup, only if touched naturally:
     - convert `ProviderCLIExecution` to the local dataclass house style
   - Explicit non-goal:
     - do not do a broad `CodexProvider` / `OpencodeProvider` base-class
       extraction unless the authority fix leaves a small obvious helper with a
       clearly shared contract
   - Done signal: there is one place to change schema constraints, and the
     remaining provider code is explicit about hidden policy.
   - Stop and re-evaluate if this turns into a large refactor whose main
     benefit is line-count reduction.

5. Strengthen verification at the boundary that actually matters.
   - Outcome: tests prove the Weft contract, not only the internal wrapper
     shape.
   - Files to touch:
     `tests/core/test_provider_cli_backend.py`,
     `tests/core/test_provider_cli_session_backend.py`,
     `tests/tasks/test_agent_execution.py`,
     `tests/fixtures/provider_cli_fixture.py`
   - Read first:
     the existing fixture-backed tests and the opt-in live-provider block in
     `tests/tasks/test_agent_execution.py`.
   - Required coverage:
     - precedence and narrowing behavior
     - persistent-session cleanup/error handling for the delegated path
     - explicit rejection of incompatible bounded/general combinations
     - docs-or-code proof for what is and is not guaranteed by the default
       suite
   - Real-provider guidance:
     - keep fixture-backed tests as the default fast proof
     - add a smaller real-provider smoke path where executable and auth are
       available
     - if there is still no stable environment to run those tests
       automatically, document that gap directly and treat it as a release gate
       for delegated-runtime boundary changes
   - What not to mock:
     - the effective invocation-building path
     - task lifecycle and outbox behavior in the higher-level delegated tests
   - Done signal: the test plan and docs make clear what fixture tests prove
     and what real-CLI smoke proves.

6. Finish the documentation cleanup and traceability loop.
   - Outcome: docs and nearby implementation notes match the cleaned-up
     boundary.
   - Files to touch:
     `docs/specifications/13-Agent_Runtime.md`,
     `docs/specifications/13A-Agent_Runtime_Planned.md`,
     `docs/specifications/02-TaskSpec.md`,
     `docs/specifications/10-CLI_Interface.md`,
     touched module docstrings/comments,
     related-plan sections in the touched specs
   - Required cleanup:
     - add or update related-plan backlinks
     - update implementation mappings if ownership moves
     - ensure current docs do not promise read-only delegated behavior unless
       code now enforces it
     - ensure planned docs preserve the "Weft is substrate" boundary and do
       not imply Weft will subsume all agent-control logic
   - Done signal: spec, plan, and owning code all point at each other, and the
     language is internally consistent.

## Verification

Minimum verification for the implementation slice:

1. Run targeted unit and integration tests:
   `./.venv/bin/python -m pytest tests/core/test_agent_validation.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_session_backend.py tests/core/test_environment_profiles.py tests/tasks/test_agent_execution.py`
2. Run the broader agent and TaskSpec coverage if touched:
   `./.venv/bin/python -m pytest tests/taskspec/ tests/core/test_agent_resolution.py tests/core/test_tool_profiles.py`
3. Run type and lint checks on touched modules:
   `./.venv/bin/mypy weft`
   `./.venv/bin/ruff check weft`
4. If the local machine has configured real provider CLIs, run the live smoke
   subset and record exactly which providers were exercised.

Observable success:

- the authority class is visible in docs and, if implemented in code, in the
  durable TaskSpec contract
- a tool profile can narrow provider behavior without being overridden by raw
  provider options
- existing stored specs still load and execute
- no public queue or outbox contract changed

## Independent Review

This plan requires an independent review pass before implementation and another
review pass after the first meaningful code slice. The review should focus on:

- authority semantics vs actual enforcement
- spec/current-doc drift
- hidden ambient capability channels
- precedence drift between schema, backend, and provider code
- whether any cleanup step is turning Weft into more than a substrate
