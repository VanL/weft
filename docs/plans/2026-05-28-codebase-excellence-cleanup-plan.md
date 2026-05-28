# Codebase Excellence Cleanup Plan

Status: completed
Source specs: docs/specifications/00-Overview_and_Architecture.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]; docs/specifications/09-Implementation_Plan.md [IP-0]-[IP-3]
Superseded by: none

## Goal

Move Weft toward higher conceptual integrity without turning cleanup into a
drive-by refactor. The work tightens specs, code-to-spec traceability,
ownership boundaries, test audit hygiene, and small pure decision seams. It
does not split large cohesive runtime files merely because they are large.

The intended end state is a codebase where a zero-context engineer can answer:

- what owns this behavior?
- why is the boundary shaped this way?
- which spec section is normative?
- which test proves the contract?
- which local helper should be reused?
- what must not change?

Implementation record:

- Landed spec hygiene guards for plan-status wording and exploratory Django
  references from shipped Python.
- Updated current implementation specs with ownership boundaries, monitor
  rationale, pure decision seams, and the public client parity matrix.
- Brought `weft.client` current with `connect()`, task PING, active-manager stop
  parity, and a test-side parity guard.
- Burned down the test audit allowlist by classifying simple CLI/spec/release
  tests as shared.
- Replaced a manager retry literal with named constants and annotated defensive
  exception/Queue-handle sites.

## Source Documents

Read these before implementation. Do not skim them as a checklist; they define
the local engineering taste this plan is trying to preserve.

- `AGENTS.md`: repo philosophy, file layout, style rules, and the warning that
  "large file" is not automatically a smell in Weft.
- `docs/agent-context/README.md`: shared agent context entry point.
- `docs/agent-context/decision-hierarchy.md`: conflict policy between specs,
  plans, lessons, and local prompt defaults.
- `docs/agent-context/principles.md`: general repo decision principles.
- `docs/agent-context/engineering-principles.md`: coding and testing
  expectations.
- `docs/agent-context/runbooks/writing-plans.md`: plan quality standard.
- `docs/agent-context/runbooks/hardening-plans.md`: hardening checklist for
  risky or boundary-crossing work.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  self-review and external review loop.
- `docs/agent-context/lessons.md` and `docs/lessons.md`: durable corrections
  from past mistakes.
- `docs/specifications/00-Overview_and_Architecture.md`: queue-first mental
  model and product shape.
- `docs/specifications/00-Quick_Reference.md`: queue names, constants, state
  names, and operator quick reference.
- `docs/specifications/01-Core_Components.md`: BaseTask, Consumer, Manager,
  TaskMonitor, runner, and control ownership.
- `docs/specifications/03-Manager_Architecture.md`: manager leadership,
  internal services, spawn dispatch, and service convergence.
- `docs/specifications/05-Message_Flow_and_State.md`: message flow,
  task-log/result reconstruction, Monitor collation, cleanup, and runtime
  state.
- `docs/specifications/07-System_Invariants.md`: state, queue, execution,
  observability, implementation, manager, and cleanup invariants.
- `docs/specifications/08-Testing_Strategy.md`: current test layout and why
  mocks are not the right proof for queue/process behavior.
- `docs/specifications/09-Implementation_Plan.md`: current implementation
  boundary and public Python client ownership.
- `docs/specifications/10-CLI_Interface.md`: CLI and command-layer surfaces.
- `docs/plans/README.md`: plan metadata, status taxonomy, and curation policy.

Comprehension questions before editing:

- Which document is normative when a spec and a plan disagree?
- Which files own CLI adapter code, shared command capability code, and runtime
  internals?
- Which queues are runtime-only state and must not become persisted product
  state?
- Why are `Manager`, `BaseTask`, `TaskMonitor`, and `TaskSpec` allowed to be
  large in this repo?
- Which test helpers use real broker/process paths and should be preferred over
  broad mocks?

If you cannot answer those questions from the files above, stop and reread
before touching code.

## Current Structure Snapshot

This section exists so the implementing engineer does not have to rediscover
the ownership map from scratch.

- `weft/cli/`: Typer adapters and CLI rendering only. It must stay thin.
- `weft/commands/`: shared capability layer used by CLI and the public Python
  client. It may call into `weft/core/`, but must not import `weft/cli/` or
  `weft/client/`.
- `weft/client/`: public Python adapter over the same command capabilities and
  `WeftContext` resolution used by the CLI. It must not create a second
  runtime, state model, or broker-targeting path.
- `weft/core/`: runtime internals. It must not import `weft/commands/`,
  `weft/cli/`, or `weft/client/`.
- `weft/core/manager.py`: background manager, child launch, service
  supervision, leadership, spawn dispatch, and runtime coordination. Large
  because coordination is its job.
- `weft/core/tasks/base.py`: queue wiring, state lifecycle, control handling,
  worker-lane plumbing, process titles, and reserved policy. These concerns
  share state.
- `weft/core/tasks/consumer.py`: concrete task execution path and result
  publication.
- `weft/core/monitor/task_monitor.py`: manager-supervised monitor service,
  collation, cleanup worker scheduling, PONG/status diagnostics, and cached
  operational summaries.
- `weft/core/monitor/store.py`: Monitor-owned durable collation tables. These
  tables are operational state, not lifecycle truth, result authority, queue
  truth, or public status dependency.
- `weft/_constants.py`: single source for production constants, queue names,
  metadata keys, cleanup policy identifiers, thresholds, and environment
  loading.
- `tests/conftest.py`: backend classification tables, test audit allowlist,
  CLI subprocess helper, broker fixtures, and xdist behavior.
- `tests/helpers/weft_harness.py`: integration-style harness for CLI, manager,
  lifecycle, and cleanup behavior.

Current measured large files are context, not automatic targets:

- `weft/core/manager.py`: about 6.5k lines
- `weft/core/monitor/task_monitor.py`: about 4.2k lines
- `weft/_constants.py`: about 2.4k lines
- `weft/core/tasks/base.py`: about 2.3k lines
- `weft/core/monitor/store.py`: about 2.2k lines
- `weft/commands/system.py`: about 2.1k lines
- `weft/core/manager_runtime.py`: about 2.1k lines

Do not use those counts as refactor justification. Use them only to prioritize
where better ownership notes, pure decision tables, and test seams may reduce
coordination cost.

## Invariants and Constraints

These apply to the whole plan.

- Specs are normative. Plans are execution records or active implementation
  slices. If the plan and spec disagree, fix the disagreement; do not silently
  implement the plan over the spec.
- Queue-first execution stays intact. Do not create a second durable path
  around `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- TID format and immutability stay intact.
- State transitions remain forward-only.
- Reserved queue policy remains correct and observable.
- `spec` and `io` sections stay immutable after resolved `TaskSpec` creation.
- Runtime-only queues such as `weft.state.*` stay runtime-only and must not
  become product persistence.
- Queue-history readers must keep generator-based or bounded scanning behavior.
  Do not replace them with fixed-limit snapshots that silently miss history.
- Constants and policy values live in `weft/_constants.py` unless they are
  true runtime objects, registries, sentinels, compiled regexes, or local test
  data.
- Documentation cleanup must not change public behavior by accident.
- Any behavioral change discovered during cleanup needs a separate targeted
  plan or a clearly scoped task in this plan with tests.
- New abstractions require evidence: meaningful duplication, a pure decision
  table already present in code, or an established local pattern. "This file is
  large" is not evidence.
- The monitor justification paragraph (Task 12.4) must use wording whose intent
  matches the user query for this plan: "Dealing with processes can be messy.
  We want a way to take advantage of our observability but also to keep the
  system auto-tuned and working well." The exact paragraph text is given in
  Task 12.4; do not paraphrase it into something vaguer.
- DRY means reuse existing helpers and command/runtime paths. It does not mean
  inventing a generic framework.
- YAGNI means no speculative plugin systems, metadata registries, cleanup
  engines, or package reorganizations unless a current spec requires them.
- Red-green TDD is preferred. When possible, write the failing guard before
  changing docs/code. If no executable guard is practical, state the smallest
  concrete review proof before editing.
- Tests for queue, process, state, result, manager, or cleanup behavior should
  use real broker-backed fixtures or `WeftTestHarness` when practical. Do not
  mock away the core proof.
- No new dependencies.
- No public CLI shape change.
- No queue name, payload shape, TaskSpec field, or persisted table change
  unless this plan explicitly says to make one. This plan currently does not.
- No drive-by formatting or unrelated module reorganization.
- Every slice must leave the repo green before moving on.

Stop and re-plan if:

- the work starts changing runtime behavior instead of documenting or
  tightening existing boundaries
- a second execution path appears
- a test needs broad mocks around queues, process lifecycle, state
  transitions, reservations, or result delivery
- an extraction wants to move side effects out of the current owner
- a spec update starts turning implementation history into normative behavior
  without saying why
- rollback cannot be described cleanly
- a zero-context engineer could read a task two ways

## Rollout and Rollback

This is a cleanup/meta plan. There is no runtime rollout sequence unless a
later slice discovers an actual behavior change, in which case that slice must
stop and get its own plan or an explicit amendment.

Rollback for documentation-only slices is simple: revert the doc/index changes
from that slice.

Rollback for test-only guards is also simple: revert the test guard and any
tiny docs/code reference updates from that slice.

Rollback for pure reducer or matrix extraction must preserve public behavior:
the old owner module should remain the only side-effect owner, and the extracted
helper should be called from the same path. If reverting the helper would
change queue behavior, state transitions, public CLI output, result
materialization, cleanup behavior, or manager scheduling, the slice has become
runtime behavior work and needs a separate plan.

## Out of Scope

- Splitting `Manager`, `BaseTask`, `TaskMonitor`, `TaskSpec`, or
  `_constants.py` merely because they are large.
- Rewriting manager scheduling, monitor cleanup, task execution, result
  materialization, queue pruning, or SimpleBroker integration.
- Creating a new architecture document that competes with the specs.
- Creating a new abstraction layer around queues, contexts, managers, or
  TaskSpecs.
- Changing public CLI commands, flags, JSON shapes, queue names, TaskSpec
  schema, persisted table schemas, or result payloads.
- Removing historical plans just because they are old. Plan curation should
  happen only where the curation policy clearly applies.
- Increasing project scope to "modernize" code style outside touched files.

## Task 1: Add Spec Information Architecture Guards

Outcome: specs explain owner, boundary, why, verification, and implementation
mapping consistently enough that future agents do not confuse invariants with
implementation notes.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/09-Implementation_Plan.md`
- `docs/specifications/README.md` only if the spec navigation needs a small
  clarification
- `tests/specs/` only for lightweight guard tests if a pattern can be enforced
  cheaply

Read first:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13], [IMPL.1],
  [MANAGER.*]
- `docs/specifications/09-Implementation_Plan.md` [IP-0]-[IP-3]
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`

Approach:

1. Identify dense sections where contract, implementation detail, and plan
   history are blended. Start with Monitor cleanup/collation in
   `05-Message_Flow_and_State.md` and TaskMonitor invariants in
   `07-System_Invariants.md`.
2. For each dense section, add or tighten a short structure:
   - owner: module/class/function that owns the behavior
   - boundary: what it may and may not decide
   - why: why this boundary exists
   - verification: tests or commands that prove the contract
   - implementation mapping: exact current code paths
3. Prefer tables where they clarify a decision matrix. Do not create a giant
   table that merely restates prose.
4. Remove status-sensitive wording such as "active plan", "draft plan", or
   "completed plan" from normative specs unless the status itself is the
   subject. Plan status belongs in `docs/plans/README.md`.
5. Keep plan backlinks status-neutral. Use "related plan" or "plan backlink"
   language.

Red-green TDD:

- If enforcing status-neutral plan language is practical, add a focused test
  in `tests/specs/` before editing the specs. It should search only
  `docs/specifications/*.md` for plan-status phrases that have caused drift.
  Do not overfit it to ordinary words like "active manager" or "completed
  task".
- If the regex would be noisy, skip the executable guard and record the
  manual review proof in this plan's implementation notes when the slice is
  completed.

Tests:

- Existing required guard:
  `./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py`
- If a new spec hygiene guard is added:
  `./.venv/bin/python -m pytest -q tests/specs/test_spec_hygiene.py`

Do not:

- change runtime behavior
- move spec sections wholesale
- remove plan backlinks just to make specs shorter
- rewrite historical plans
- add a documentation generator

Done when:

- `05` and `07` have clearer contract/why/owner structure in the densest
  areas
- no shipped-code implementation mapping is made stale
- plan status remains owned by plan metadata and `docs/plans/README.md`
- relevant spec tests pass

## Task 2: Build a Boundary Inventory

Outcome: create a current ownership inventory that makes later refactors
reviewable and blocks line-count-driven cleanup.

Files to touch:

- `docs/specifications/09-Implementation_Plan.md`
- optionally `docs/specifications/11-CLI_Architecture_Crosswalk.md` if CLI and
  command ownership needs one crosswalk update
- no Python files unless a docstring has an obviously stale spec reference

Read first:

- `docs/specifications/09-Implementation_Plan.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- module docstrings for the files listed in the current structure snapshot

Approach:

1. Add a "Boundary Inventory" section to `09-Implementation_Plan.md`.
2. Use a compact table with these columns:
   - area
   - owning files
   - owns
   - must not own
   - governing specs
   - primary tests
3. Include at least these areas:
   - CLI adapter layer
   - command capability layer
   - public Python client
   - context/broker targeting
   - manager runtime/bootstrap
   - manager service convergence
   - BaseTask reactor/control/state lifecycle
   - Consumer execution/result publication
   - TaskMonitor reactor and cleanup scheduling
   - Monitor store/collation
   - result/status reconstruction
   - pruning/retention commands
   - runner plugins and runtime handles
   - TaskSpec model/materialization
   - pipeline runtime
4. Keep each row short. The table is a map, not a second spec.
5. When a row says "must not own", be direct. Example: `weft/client/` must not
   own a second broker-targeting or lifecycle model.

Red-green TDD:

- This is documentation architecture, so a failing test first is not required
  unless adding a machine-checkable guard. The concrete proof is that
  `docs/specifications/09-Implementation_Plan.md` names each major area with
  owner, non-owner boundary, spec refs, and tests.

Tests:

- `./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py`
- `./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py`

Do not:

- add a new `docs/architecture/` tree
- duplicate the whole CLI crosswalk
- decide to split files while writing the map

Done when:

- a zero-context engineer can find the owner and test entry point for each
  major runtime surface from `09-Implementation_Plan.md`
- no new code boundaries are invented

## Task 3: Tighten Code-to-Spec References

Outcome: shipped code points at current normative specs, not exploratory
companions or obsolete plan fragments.

Files to inspect:

- all Python files under `weft/`
- `extensions/weft_docker/`
- `extensions/weft_macos_sandbox/`

Likely files to touch:

- module docstrings in shipped behavior modules with missing or stale refs
- compatibility modules only when the current docstring is misleading
- `docs/specifications/09-Implementation_Plan.md` if ownership mapping needs
  a small adjustment

Read first:

- `docs/specifications/README.md` for current-vs-planned spec guidance
- `docs/specifications/09-Implementation_Plan.md`
- `docs/specifications/13B-Using_Weft_In_Higher_Level_Systems.md`
- `docs/specifications/13C-Using_Weft_With_Django.md`

Approach:

1. Search for stale references:
   ```bash
   rg -n "13C-Using_Weft_With_Django|planned|roadmap|draft plan|active .*plan" \
     weft extensions docs/specifications
   ```
2. Decide case by case:
   - current shipped code should reference current specs
   - exploratory docs may reference `13C`
   - planned docs may reference planned specs
   - compatibility re-export modules may not need heavy spec refs if their
     owner module has them
3. Add module-level spec references only where they improve traceability.
   Avoid stuffing every tiny `__init__.py` with noisy refs.
4. If a function owns a spec boundary, prefer a function docstring `Spec:`
   reference near that function rather than only a module docstring.
5. Do not change behavior while editing refs.

Red-green TDD:

- Add a small guard only if it can be precise. A good guard would fail when
  shipped Python files reference `13C-Using_Weft_With_Django.md` outside
  allowed integration/proposal contexts.
- Do not add a broad "every file must contain docs/specifications" test. It
  will create noise in compatibility exports and package initializers.

Tests:

- new guard if added, likely under `tests/specs/`
- `./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox`
- `./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py`

Do not:

- cite a spec section that does not actually govern the code
- update docs to match code if the code is wrong; stop and file a behavioral
  plan instead
- turn proposed Django/framework guidance into current core contract

Done when:

- no shipped runtime/client/command code relies on exploratory specs for
  current behavior
- major modules have useful, current code-to-spec references

## Task 4: Create a Reducer and Decision-Matrix Candidate List

Outcome: identify pure logic that may be extracted later, without moving side
effects or inventing abstractions.

Files to inspect:

- `weft/commands/control_convergence.py`
- `weft/core/task_lifecycle.py`
- `weft/core/state_machines.py`
- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/policies/*.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- relevant tests in `tests/core/`, `tests/commands/`, and `tests/tasks/`

Files to touch:

- `docs/specifications/09-Implementation_Plan.md` for the candidate list
- optional small docstrings in existing pure reducers if they lack ownership
  notes
- no extraction in this task unless the candidate is already a tiny local
  constant/table move with existing tests

Approach:

1. Add a "Pure Decision Seams" section to `09-Implementation_Plan.md`.
2. For each candidate, record:
   - current owner
   - decision inputs
   - decision output
   - side effects that must stay out
   - existing tests
   - extraction readiness: `ready`, `needs tests`, or `do not extract`
3. Start with likely candidates:
   - command control convergence reducer
   - task lifecycle transition table
   - manager service convergence reducer
   - monitor service collation classification
   - cleanup policy progress classification
   - result/status evidence priority
4. Be skeptical. A candidate is not "ready" if extracting it would require
   passing many mutable runtime objects or moving queue/process side effects.

Red-green TDD:

- This task is inventory-only. Do not write failing extraction tests yet.
  Instead, note the exact test file that a later extraction would need.

Tests:

- Run only the existing test files relevant to the candidates that were
  inspected. Common starting points:
  ```bash
  ./.venv/bin/python -m pytest -q \
    tests/core/test_state_machines.py \
    tests/commands/test_task_commands.py \
    tests/commands/test_task_evidence.py \
    tests/commands/test_result.py
  ```
- Always run `./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py`
  if plan metadata/index changed

Do not:

- extract logic during the inventory unless separately approved
- use line count as readiness evidence
- create a generic reducer framework
- move side effects out of current owners

Done when:

- future engineers can tell which extractions are worth doing and which are
  traps
- no behavior changed

## Task 5: Add One Small Pure Extraction Only if It Is Already Proven

Outcome: demonstrate the cleanup style with at most one tiny extraction from
the candidate list. This task is optional and must be skipped if no candidate
is clearly ready after Task 4.

Allowed candidate profile:

- the logic is already pure or nearly pure
- inputs and outputs are small immutable values or dictionaries
- tests can be written as table tests without mocking queues/processes
- caller remains the same side-effect owner
- no public behavior changes

Likely candidate:

- Monitor service collation classification if it is still local and already
  table-tested.

Stop before this task if another active plan or concurrent branch already owns
the same extraction. Do not duplicate work across plans.

Files to touch depend on the chosen candidate. For the likely candidate:

- `weft/core/monitor/store.py`
- `tests/core/test_monitor_store.py`
- `weft/_constants.py` only if classification policy values need to be
  centralized
- relevant spec mapping in `docs/specifications/05-Message_Flow_and_State.md`
  and `docs/specifications/07-System_Invariants.md`

Approach:

1. Write or tighten table tests first. The red test should fail because the
   pure seam does not yet expose the intended contract or because a regression
   case is missing.
2. Extract the smallest helper or table.
3. Keep the existing caller as the side-effect owner.
4. Keep constants in `_constants.py`.
5. Update docstrings/spec refs if ownership changed.

Tests:

- candidate-specific table tests first
- then the closest integration-style tests that prove the caller still emits
  the same queue/log/status behavior

Do not:

- extract more than one seam in this plan
- move queue reads, writes, process control, or state mutation into the helper
- generalize to all reducers

Done when:

- red-green table tests pass
- nearest real-path tests pass
- side effects still live in the original owner

## Task 6: Audit and Harden Public Python Client API Parity

Outcome: make `weft.client` current with the shared public capability layer.
Every current public command/API capability should be either exposed through
the client, exposed through a task handle, or explicitly classified as
CLI-only/internal with a reason. Do not let new APIs silently bypass the
client.

Files to inspect:

- `weft/client/__init__.py`
- `weft/client/_client.py`
- `weft/client/_namespaces.py`
- `weft/client/_task.py`
- `weft/client/_types.py`
- `weft/commands/submission.py`
- `weft/commands/tasks.py`
- `weft/commands/queue.py`
- `weft/commands/manager.py`
- `weft/commands/specs.py`
- `weft/commands/system.py`
- `weft/commands/result.py`
- `weft/commands/events.py`
- `weft/commands/task_monitor.py`
- `weft/commands/retention_prune.py`
- `weft/commands/runtime_prune.py`
- `weft/commands/types.py`
- `weft/cli/app.py` for the operator-visible command inventory
- `tests/core/test_client.py`
- `docs/specifications/09-Implementation_Plan.md` [IP-1.1]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2]
- `docs/specifications/13C-Using_Weft_With_Django.md` only as a proposed
  integration consumer, not as current core authority

Files to touch:

- `docs/specifications/09-Implementation_Plan.md` if the current section is
  enough
- or a new canonical spec file only if the client is explicitly promoted to a
  larger current contract. Do not create this new file casually.
- `weft/client/_client.py`
- `weft/client/_namespaces.py`
- `weft/client/_task.py`
- `weft/client/_types.py`
- `tests/core/test_client.py`
- client module docstrings if spec refs drift

Approach:

1. Inventory the current public capability surface before editing client code.
   Build a "Client API Parity Matrix" in `09-Implementation_Plan.md` or in a
   dedicated subsection of the client spec if one is created. The matrix must
   include:
   - submission: task spec, stored spec, pipeline, command, prepare, submit
   - task handle: status, terminal snapshot, result, events, realtime events,
     follow, stop, kill
   - task namespace: list, stats, status, terminal snapshot, acknowledge
     terminal snapshot, watch, TID resolution, stop/kill one and many, ping
     if it is intended for programmatic use
   - queue namespace: read, write, write endpoint, peek, move, list, exists,
     stats, resolve endpoint, watch, delete, broadcast, aliases
   - manager namespace: start, serve, stop, list, status, diagnostics if
     intended for programmatic use
   - spec namespace: create, list, show, delete, validate, generate
   - system namespace: status, tidy, dump, load, builtins, prune surfaces if
     intended for programmatic use
   - monitor/prune/maintenance surfaces: classify as exposed or intentionally
     CLI-only with a reason
2. For each matrix row, choose exactly one status:
   - `client`: exposed directly on `WeftClient`
   - `namespace`: exposed through a namespace such as `client.tasks`
   - `task_handle`: exposed on `Task`
   - `cli_only`: deliberately not a Python client API
   - `internal`: shared helper but not public capability
3. For every `cli_only` or `internal` row, write the reason. Good reasons:
   operator-only UX, unsafe as an unscoped library call, already covered by a
   narrower task-handle method, or implementation helper. Bad reason: "not
   implemented yet" without a follow-up decision.
4. Add missing client methods only when the matrix says the capability is
   public and the shared command-layer helper already exists. Reuse the command
   helper. Do not call `weft/core/` directly from the client.
5. First decide and write down the answer: is `weft.client` a stable public API
   or a thin current adapter with limited compatibility promises?
6. If it remains a thin adapter, keep the spec in `09` and add only tests that
   protect current adapter behavior.
7. If it is stable public API, create a dedicated spec section or file only
   after confirming the desired compatibility promise with maintainers.
8. Ensure tests prove:
   - `connect()` resolves context through `WeftContext`
   - namespaces call command-layer capabilities, not runtime internals
   - `Task` handle methods reuse command/event/result helpers
   - realtime event iteration is non-consuming
   - terminal snapshot acknowledgement is explicit
   - the parity matrix and exported client surface agree

Red-green TDD:

- Prefer small tests in `tests/core/test_client.py` that exercise the public
  client against real harness/broker state where practical.
- Use monkeypatch only to prove a thin adapter delegates to an existing command
  helper when a full broker path would duplicate another test. Do not mock the
  task lifecycle and then claim lifecycle behavior is tested.
- Add a client parity guard before adding missing methods. It can be a
  hand-maintained table in `tests/core/test_client.py` that asserts expected
  namespaces/methods exist and intentionally omitted capabilities are named in
  the matrix. Do not introspect every `weft.commands.*` function; many command
  functions are helpers, not public API.

Tests:

- `./.venv/bin/python -m pytest -q tests/core/test_client.py`
- if realtime/result behavior is touched:
  `./.venv/bin/python -m pytest -q tests/commands/test_result.py`

Do not:

- let `weft.client` import from `weft/cli/`
- let `weft/core/` import from `weft/client/`
- add Django-specific behavior to core
- add async/web framework abstractions
- expose every CLI command mechanically; some commands are operator-only or
  helper-only and should remain classified rather than wrapped
- paper over a missing client method by calling `weft/core/` directly

Done when:

- every current public command/API surface has a matrix row and status
- any current API that should be available to Python callers has a client or
  task-handle method
- every intentionally omitted API has a reason
- the current client promise is explicit
- tests prove adapter behavior without over-mocking the runtime contract
- shipped code refs point at current specs

## Task 7: Burn Down Test Audit Allowlist Debt

Outcome: reduce temporary unaudited test-module exceptions without hiding
backend-sensitive behavior.

Files to touch:

- `tests/conftest.py`
- specific test files currently listed in `_UNAUDITED_MODULE_ALLOWLIST_REASONS`
- `docs/specifications/08-Testing_Strategy.md` only if the test taxonomy needs
  clarification

Read first:

- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]
- `tests/specs/test_test_audit_policy.py`
- `tests/conftest.py` around `_SHARED_MODULES`, `_SQLITE_ONLY_MODULES`,
  `_UNAUDITED_MODULE_ALLOWLIST_REASONS`, and pytest collection hooks
- the candidate test file being classified

Approach:

1. Pick two or three allowlisted files per slice. Do not attempt all at once.
2. For each file, inspect whether it is backend-neutral or SQLite-specific:
   - backend-neutral tests belong in `_SHARED_MODULES` or use
     `pytestmark = [pytest.mark.shared]`
   - SQLite-specific tests belong in `_SQLITE_ONLY_MODULES` or use
     `pytestmark = [pytest.mark.sqlite_only]`
3. If a test is accidentally backend-specific because of a weak fixture, fix
   the fixture or test path rather than marking it shared.
4. Remove the entry from `_UNAUDITED_MODULE_ALLOWLIST_REASONS` only after the
   file has an explicit classification.
5. Prefer central classification tables for legacy modules if that is the
   existing local pattern; prefer module-level `pytestmark` when the file is
   already being edited and the scope is obvious.

Red-green TDD:

- Start by removing one allowlist entry and running
  `tests/specs/test_test_audit_policy.py` or collecting the file. It should
  fail if the file is not classified.
- Add the correct classification and rerun.

Tests:

- `./.venv/bin/python -m pytest -q tests/specs/test_test_audit_policy.py`
- candidate file, for example:
  `./.venv/bin/python -m pytest -q tests/cli/test_cli_run.py`
- if classification touches backend behavior and Postgres is available:
  `bin/pytest-pg --all`

Do not:

- mark a test `shared` because it happens to pass locally under SQLite
- add broad path-prefix exceptions
- serialize tests broadly to hide isolation bugs
- mock CLI subprocess behavior when `run_cli()` or `WeftTestHarness` can drive
  the real path

Done when:

- `_UNAUDITED_MODULE_ALLOWLIST_REASONS` has fewer entries
- the moved files have explicit, defensible backend scope
- audit policy tests pass

## Task 8: Strengthen Constants and Policy Centralization

Outcome: keep production policy values centralized and make violations obvious.

Files to touch:

- `weft/_constants.py`
- `tests/system/test_constants.py`
- modules that currently define module-level uppercase policy values outside
  `_constants.py`

Read first:

- `AGENTS.md` section 4.4 Constants
- `weft/_constants.py`
- `tests/system/test_constants.py`
- current violation output, if any

Approach:

1. Run the constants policy test first:
   ```bash
   ./.venv/bin/python -m pytest -q \
     tests/system/test_constants.py::TestConstants::test_production_constants_live_in_constants_module
   ```
2. If it fails, classify each violation:
   - move policy constants to `_constants.py`
   - keep true runtime singletons/registries/sentinels/compiled patterns in
     their module and add an allowlist reason only when justified
3. Add a short docstring for each moved constant that says why it exists.
4. Update imports in the consuming module.
5. Rerun the policy test and nearest functional tests.

Red-green TDD:

- The policy test is the red test. Do not bypass it with an allowlist unless
  the value is genuinely a runtime object.

Tests:

- ```bash
  ./.venv/bin/python -m pytest -q \
    tests/system/test_constants.py::TestConstants::test_production_constants_live_in_constants_module
  ```
- nearest tests for the consuming module
- `./.venv/bin/ruff check weft/_constants.py <consumer files>`

Do not:

- hide policy constants in modules for convenience
- add allowlist entries for ordinary strings, thresholds, queue names, cleanup
  identifiers, classification values, or metadata keys
- move test-only constants into production constants

Done when:

- constants policy test passes
- consuming behavior still passes nearest tests

## Task 9: Curate the Plan Corpus Without Rewriting History

Outcome: reduce plan corpus confusion where historical draft plans look like
current direction.

Files to touch:

- `docs/plans/README.md`
- selected `docs/plans/*.md` only when metadata is missing, stale, or clearly
  wrong
- specs that backlink selected plans only if the backlink wording is
  status-sensitive or misleading

Read first:

- `docs/plans/README.md`
- `tests/specs/test_plan_metadata.py`
- `docs/agent-context/runbooks/writing-plans.md`
- specs that cite the candidate plan

Approach:

1. Do not delete plans in the first pass. Classify the problem first.
2. Search for confusing status-sensitive language:
   `rg -n "active .*plan|draft plan|completed plan|superseded draft" docs/specifications docs/plans`
3. For each hit:
   - if it is normative spec prose, make it status-neutral
   - if it is plan metadata, keep it
   - if it is a historical description inside the plan itself, leave it unless
     it violates the metadata/index policy
4. Check whether draft plans that describe shipped behavior should be marked
   completed or superseded. Do not guess. Only change status when tests/specs
   and code clearly prove the plan landed or was superseded.
5. Keep `docs/plans/README.md` count and index accurate.

Red-green TDD:

- `tests/specs/test_plan_metadata.py` is the guard for metadata and index.
- Add a separate status-language guard only if it can avoid false positives
  such as "active manager" and "completed task".

Tests:

- `./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py`

Do not:

- treat draft plans as backlog
- mark plans completed because they "sound done"
- delete historical plans without checking curation policy and backlinks
- encode plan status in normative specs

Done when:

- README metadata tests pass
- specs avoid stale plan-status language
- any status changes are backed by code/spec evidence

## Task 10: Add Documentation-to-Code Traceability Checks Where Cheap

Outcome: make important traceability drift harder to introduce, without
creating brittle documentation tests.

Files to touch:

- `tests/specs/`
- possibly `tests/system/`
- specs only if the guard reveals a real mismatch

Possible guards:

- Quick Reference queue names match `_constants.py`
- shipped code does not cite exploratory specs as current authority
- plan metadata/index stays normalized
- import boundaries remain one-way
- constants stay centralized

Existing guards to reuse:

- `tests/specs/quick_reference/test_queue_names.py`
- `tests/specs/test_plan_metadata.py`
- `tests/architecture/test_import_boundaries.py`
- `tests/specs/test_command_queue_seam.py`
- `tests/system/test_constants.py`

Approach:

1. Prefer extending an existing guard over adding a new one.
2. Keep each guard narrow and high-signal.
3. Do not assert prose snapshots.
4. Do not require every module to have a spec ref.
5. Do not write tests that make harmless wording changes expensive.

Red-green TDD:

- Add the failing assertion first against the known drift.
- Fix the drift.
- Rerun only the focused guard, then the final gates.

Tests:

- the guard being changed
- final spec/architecture guard group:
  ```bash
  ./.venv/bin/python -m pytest -q \
    tests/specs/quick_reference/test_queue_names.py \
    tests/specs/test_plan_metadata.py \
    tests/architecture/test_import_boundaries.py \
    tests/specs/test_command_queue_seam.py \
    tests/system/test_constants.py
  ```

Done when:

- at least one high-value traceability gap is guarded
- the guard has low false-positive risk

## Task 11: Documentation Maintenance for Completed Slices

Outcome: every cleanup slice leaves nearby specs, docstrings, tests, and plan
metadata synchronized.

Files to touch depend on the slice. Always check:

- governing spec section
- implementation mapping in that spec
- module docstrings in touched files
- tests proving the touched behavior
- `docs/plans/README.md` if plan status changes
- `docs/lessons.md` only if the slice exposes a repeated agent mistake

Approach:

1. Before declaring a slice done, run:
   `git diff -- docs/specifications weft tests docs/plans`
2. For each touched code module, ask whether the spec mapping still names the
   right owner.
3. For each touched spec, ask whether the code/docstring refs still point back
   to it.
4. If a repeated mistake was found, add a concise lesson to `docs/lessons.md`.
   Do not add lessons for one-off trivia.

Tests:

- nearest tests for the slice
- plan metadata test if any plan changed
- full final gates before completion

Do not:

- update docs after the fact with vague "implementation updated" language
- leave code comments pointing at stale section names
- add lessons as a dumping ground for opinions

Done when:

- traceability is bidirectional for the slice
- no stale references remain in touched files

## Testing Plan

Use the repo-managed toolchain. Do not assume global `pytest`, `ruff`, or
`mypy`.

Setup:

```bash
. ./.envrc
uv sync --all-extras
```

Per-slice verification:

- Documentation/plan metadata:
  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
  ```
- Spec/architecture guard group:
  ```bash
  ./.venv/bin/python -m pytest -q \
    tests/specs/quick_reference/test_queue_names.py \
    tests/specs/test_plan_metadata.py \
    tests/architecture/test_import_boundaries.py \
    tests/specs/test_command_queue_seam.py \
    tests/system/test_constants.py
  ```
- Client contract:
  ```bash
  ./.venv/bin/python -m pytest -q tests/core/test_client.py
  ```
- Monitor/store cleanup or collation:
  ```bash
  ./.venv/bin/python -m pytest -q -n0 \
    tests/core/test_monitor_store.py \
    tests/core/test_monitor_external_log.py \
    tests/tasks/test_task_monitor.py
  ```
- Manager/task lifecycle behavior:
  ```bash
  ./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/tasks/test_task_execution.py
  ```
- Test audit policy:
  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/test_test_audit_policy.py
  ```

Final gates before claiming the whole cleanup plan done:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox tests
git diff --check
```

Run the Postgres backend check when a slice touches backend-neutral queue,
context, wait, pruning, cleanup, or manager code and the local environment has
Postgres test support:

```bash
bin/pytest-pg --all
```

What not to mock:

- SimpleBroker queue semantics
- manager startup/selection when the behavior is about manager lifecycle
- task state transitions
- reserved queue movement
- outbox/result materialization
- TaskMonitor cleanup/collation when real broker/store fixtures exist
- CLI subprocess behavior when `run_cli()` or `WeftTestHarness` can drive it

Mock only:

- external services
- slow/non-deterministic platform boundaries
- narrow adapter delegation when the production path is already covered
  elsewhere and the test is explicitly about adapter wiring

## Verification and Gates

Before starting each task:

- confirm `git status --short`
- identify unrelated user changes and do not revert them
- write or identify the focused red test/proof

After each task:

- run the task's focused tests
- run `ruff` on touched Python files
- run `git diff --check`
- inspect `git diff` for unintended behavior changes

Before marking the plan complete:

- all final gates in the Testing Plan pass
- `_UNAUDITED_MODULE_ALLOWLIST_REASONS` has fewer entries, unless the plan is
  explicitly stopped before Task 7
- no shipped code points at exploratory specs for current behavior
- `09-Implementation_Plan.md` has an up-to-date ownership map
- the public Python client parity matrix covers current command/API surfaces,
  including explicit reasons for omitted CLI-only/internal capabilities
- no new policy constants live outside `_constants.py`
- specs explain why key boundaries exist
- any extraction is small, pure, table-tested, and keeps side effects in the
  original owner
- fresh-eyes self-review is recorded
- external review has been completed or the plan remains review-pending
- the four concrete hygiene gaps that blocked "excellent" in the 2026-05 evaluation
  are closed or explicitly tracked (broad except hygiene, inline sleep discipline,
  Queue handle preference, and explicit monitor justification per the exact wording
  required in the user query for this plan)
- `rg -n "Dealing with processes can be messy" docs/specifications/07-System_Invariants.md` returns a hit (the justification paragraph is present)

## Task 12: Close the Concrete Hygiene Gaps Blocking "Excellent" (Exceptions, Sleeps, Queue Handles, Monitor Justification)

Outcome:
The last four small but recurring hygiene gaps identified in the 2026-05 codebase
evaluation are closed with enforceable patterns and explicit justification text.
This task does not change runtime behavior, only makes the existing standards
machine- and human-checkable where they were still aspirational.

Why this task exists (for the zero-context implementer):
The project has extremely high written standards (AGENTS.md, 2026-04-16 lessons
on exceptions/sleeps/constants, testing-patterns.md, hardening-plans.md). A few
sites still violated those standards in ways that a future reviewer or agent would
correctly flag as blocking "excellent." This task makes the standards stick without
inventing new architecture.

Files to touch:
- `weft/core/monitor/store.py` (one transaction guard)
- `weft/core/tasks/consumer.py` (one state guard)
- `weft/core/tasks/service.py` (two places)
- `weft/core/tasks/sessions.py` (several defensive process guards)
- `weft/core/serve_log.py` (two small guards)
- `weft/core/manager.py` (one inline sleep site)
- `weft/core/monitor/task_monitor.py` (module docstring)
- `weft/core/monitor/store.py` (module docstring)
- `docs/specifications/07-System_Invariants.md` (add 2-4 sentence justification near OBS.13)
- `docs/plans/2026-05-28-codebase-excellence-cleanup-plan.md` (update this task's own completion notes)
- `tests/` only if adding a tiny guard test (prefer not to unless it is a one-line constant-existence check)

Read first (in this exact order; do not skip):
1. `docs/agent-context/runbooks/writing-plans.md` (audience assumptions and TDD requirement)
2. `docs/agent-context/runbooks/hardening-plans.md` (especially "Specify What Not To Mock", "Add Stop-and-Re-Evaluate Gates", "State Invariants Before Tasks")
3. `docs/lessons.md` sections:
   - `## 2026-04-16 Exception Boundaries In Manager And BaseTask`
   - `## 2026-04-16 Poll Floors And Grace Windows`
   - `## 2026-04-16 Constants Boundary`
4. `docs/specifications/07-System_Invariants.md` starting at OBS.13 (the long monitor paragraph)
5. `weft/_constants.py` (see how poll/interval constants are documented)
6. The four files containing the current hygiene violations (use the exact rg commands below)

Comprehension questions you must be able to answer before editing any file:
- Why does the 2026-04-16 Exception lesson say "Narrow the catch to the boundary, not the function"?
- What exact phrase must appear in a surviving broad `except Exception` per the project's own rule?
- Why do short poll values (0.05, 0.1, etc.) belong in `_constants.py` with a comment explaining the semantic reason?
- What is the single sentence the user query requires us to use when justifying the existence of the monitor and its collation tables?

Current structure (so you do not rediscover):
- Most broad `except Exception` sites in runtime code already carry `# pragma: no cover - <reason>`.
- A small number of state-reset or transaction guards still use bare `except Exception:` + re-raise or local reset. These are the ones this task touches.
- One inline `time.sleep(0.05 * (attempt + 1))` exists in manager inbox seeding retry (manager.py around line 1409).
- `WeftContext.queue()` and task `_queue()` helpers exist precisely to share broker target + stop-event wiring. Direct `Queue(...)` is allowed at true edges but is still noise when a helper was available.
- The monitor (TaskMonitor + collation tables in `weft_monitor_*`) is operational tooling that helps keep a messy process system healthy. It must never be treated as lifecycle truth. The justification paragraph does not change behavior; it only makes the "why it exists" explicit in the normative spec.

Invariants that must not move in this task:
- No change to any queue contract, state transition, reserved policy, or result payload.
- No new execution path.
- The monitor remains operational only (its tables and summaries must not become status or result authority — this is already stated in OBS.13; we are only adding the "why messy processes" sentence).
- `_constants.py` remains the single source; we only move values into it or add comments.
- All changes are hygiene or documentation. If you discover you need a behavioral change, stop and file a separate plan.

Approach (follow in order; red-green where possible):

Sub-task 12.1 — Exception hygiene pass (one file at a time)
- For each of the following files, first write a tiny failing guard or manual review proof that the bare `except` without pragma exists.
- Then add the minimal pragma comment that matches the 2026-04-16 lesson ("defensive", "best effort", "interpreter shutdown", "extension hook", etc.).
- Do not narrow the catch unless the lesson explicitly says that site should be narrowed (most of these are legitimate boundary guards).
- Use red-green: the "red" state is "I can still find the bare except without the required comment via grep".

Exact sites (run these commands to locate the current lines):
```bash
rg -n "except Exception:" weft/core/monitor/store.py weft/core/tasks/consumer.py weft/core/tasks/service.py weft/core/tasks/sessions.py weft/core/serve_log.py --context 1
```

What not to do:
- Do not turn these into specific `except (BrokerError, OSError)` unless the 2026-04-16 lesson says that site must be narrowed. Most are intentionally broad for process death / interpreter shutdown.
- Do not add tests that exercise the exception path unless one is already easy (these are defensive).

Done for 12.1 when:
- The rg command above returns only lines that also contain `# pragma: no cover`.
- `ruff` and `mypy` still pass on the touched files.
- `git diff` shows only added comments, no logic change.

Sub-task 12.2 — Sleep / poll constant discipline
- Locate the inline sleep:
  ```bash
  rg -n "time\.sleep\(0\.05 \* \(attempt" weft/core/manager.py
  ```
- Read the surrounding function (inbox seeding retry).
- Add (or reuse) a small constant in `weft/_constants.py` with a one-sentence comment explaining the semantic reason (retry backoff for durable write before child launch).
- Replace the inline expression with the constant.
- Add a one-line comment at the use site referencing the constant's docstring.

Red-green:
- Before the edit, a grep for the literal `0.05 * (attempt` succeeds (red).
- After the edit, that literal no longer appears in production code; the constant is the only place the number lives (green).

Done for 12.2 when the literal inline expression is gone and the constant has a docstring that a future reader can understand without reading the call site.

Sub-task 12.3 — Queue handle preference (documentation + one optional small improvement)
- Run:
  ```bash
  rg -n "^\s+.*= Queue\(" weft/core/manager.py weft/core/tasks/interactive.py weft/commands/ --glob "*.py"
  ```
- For each site, decide (and document in a one-line comment) whether a `WeftContext.queue()` or task `_queue()` helper was available and preferable.
- If a site is a true edge (no context or task object in scope), add a one-sentence comment: "# Direct Queue ok here: no WeftContext or task available; see runtime-and-context-patterns.md §2".
- Do not change call sites unless the change is a one-line variable swap that obviously reuses an existing helper already in scope. If it would require threading a context further, document the site and stop — that is future work.

What not to mock or invent:
- Do not add a new "QueueFactory" or wrapper. The existing `WeftContext.queue()` and `_queue()` are the canonical paths.

Done for 12.3 when every direct `Queue(` construction in the listed directories either reuses a helper or carries the explicit "edge case" comment above.

Sub-task 12.4 — Explicit monitor justification (the one the user query required)
- In `docs/specifications/07-System_Invariants.md`, near the beginning of the OBS.13 block (right after the sentence that introduces `TaskMonitor` and its tables), insert the following paragraph (use the user's exact intent):

  > The manager-supervised `TaskMonitor` and its durable collation tables exist because dealing with real processes is messy. Even with perfect queue semantics and clean task lifecycles, processes can exit uncleanly, leave stale queues, leak resources under Windows or Postgres, or produce ambiguous liveness evidence. The monitor gives the system a way to take advantage of the excellent observability already present in `weft.log.tasks` and runtime handles, while also keeping the overall installation auto-tuned and healthy through bounded, policy-driven cleanup. All of its outputs (collation summaries, PONG diagnostics, prune reports) are operational evidence only. They must never be treated as the source of truth for task status, results, or control authority (see the rest of OBS.13 for the exact boundaries).

- Add a one-sentence pointer in the module docstrings of `weft/core/monitor/task_monitor.py` and `weft/core/monitor/store.py` that says the justification lives in the spec at OBS.13 and that the module implements operational (not truth) behavior.
- Do not change any logic, table names, or policy.

Red-green / proof:
- Before: a reader searching for "why does the monitor exist" or "messy" in the spec finds nothing.
- After: the exact paragraph above (or a close approved variant) is present and the two module docstrings point to it.

Done for 12.4 when the paragraph exists in the spec, the two docstrings reference it, and no behavioral diff exists.

Stop-and-re-evaluate gates for the whole Task 12 (any of these means pause and talk to the plan author):
- You feel tempted to narrow a broad catch "because it looks better."
- You are considering adding a new constant that duplicates an existing semantic (check `_constants.py` first).
- The Queue improvement would require passing a context deeper into a call stack.
- The justification paragraph edit makes you want to also edit monitor policy or cleanup code.
- Any test you write for these hygiene items requires mocking `Queue`, `Manager`, or `Consumer`.

Verification commands (run after each sub-task and at the end):
```bash
./.venv/bin/python -m ruff check weft/core/monitor weft/core/tasks/consumer.py weft/core/tasks/service.py weft/core/tasks/sessions.py weft/core/serve_log.py weft/core/manager.py
./.venv/bin/python -m mypy weft/core/monitor weft/core/tasks/consumer.py weft/core/tasks/service.py weft/core/tasks/sessions.py weft/core/serve_log.py weft/core/manager.py
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py tests/architecture/test_import_boundaries.py
git diff --stat
git diff --check
```

## Independent Review Loop

This plan is boundary-crossing and should not be implemented without external
review.

Preferred reviewer:

- a different agent family than the author, if available
- otherwise a separate Codex review pass using review stance

Reviewer should read:

- this plan
- `AGENTS.md`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]
- `docs/specifications/09-Implementation_Plan.md` [IP-0]-[IP-3]
- `tests/conftest.py`

Review prompt:

> Read `docs/plans/2026-05-28-codebase-excellence-cleanup-plan.md`.
> Carefully examine the plan and associated specs/code. Look for errors, bad
> ideas, unsafe tradeoffs, and latent ambiguities. Do not implement anything.
> Answer: could a skilled engineer with zero Weft context implement this
> confidently and correctly? If not, list the exact blockers.

The plan author must respond to every external review finding by updating the
plan or recording why the point is out of scope.

## Fresh-Eyes Self-Review Log

The author must complete this section before implementation starts and again
after any material plan revision.

Pass 1 checklist:

- Are all tasks bite-sized and dependency-ordered?
- Does each task name files to touch and files to read first?
- Does each task say what not to change?
- Does each task name tests and anti-mocking guidance?
- Are runtime behavior changes out of scope unless separately planned?
- Is rollback possible for each task?
- Are spec/source references current?

Pass 1 findings:

- Fixed one invalid test path in Task 4. The draft referenced
  `tests/commands/test_tasks.py`, which does not exist. The plan now points at
  `tests/commands/test_task_commands.py`, `test_task_evidence.py`, and
  `test_result.py`.
- Tightened Task 5 so the optional extraction cannot be read as permission to
  start a broad refactor or duplicate another active plan.
- Wrapped long shell commands into code blocks so copy/paste verification is
  less error-prone.
- Expanded Task 6 after review to require a client API parity matrix. The first
  draft only hardened the existing client contract; it did not force every new
  command/API surface to be exposed or explicitly classified.

Pass 2 checklist:

- Re-read as a zero-context engineer with questionable taste.
- Search for vague verbs such as "update the flow", "clean up", "improve",
  "adjust", or "refactor" without a concrete file and boundary.
- Check whether any task could be misread as permission to split large files.
- Check whether the testing instructions allow broad mocks.
- Check whether the plan drifted away from cleanup governance into a runtime
  rewrite.

Pass 2 findings:

- Re-read after the Pass 1 fixes. The plan still stays in the discussed
  direction: cleanup governance, traceability, boundary inventory, test debt,
  constants centralization, and only optional pure extraction. No runtime
  rewrite or broad file split is authorized.
- Residual risk: the plan is intentionally broad. Implementation should happen
  slice by slice with external review before starting and again after any
  material runtime-adjacent slice.

Pass 3 (fresh-eyes after adding Task 12 for the 2026-05 evaluation gaps):

- The original draft (before this edit) had excellent governance scaffolding but
  was missing concrete, zero-context tasks for the four specific hygiene issues
  that the 2026-05 evaluation identified as blocking "excellent":
  (1) remaining unmarked broad `except Exception` in stateful guards,
  (2) one inline sleep in manager retry,
  (3) inconsistent direct `Queue()` usage vs helpers,
  (4) no explicit "why the monitor exists for messy processes" justification
  paragraph in the spec (user query explicitly required this).
- Added full Task 12 with:
  - exact `rg` commands the implementer must run to find sites
  - red-green TDD instructions for each sub-task
  - "what not to mock / what not to invent" warnings
  - stop-and-re-evaluate gates
  - the user's exact required justification language for the monitor
  - explicit "read these lessons and runbooks in this order" for questionable taste
- Updated Completion Criteria to reference the four hygiene gaps.
- No scope drift: Task 12 is pure hygiene + one documentation paragraph. No
  behavior change, no new abstractions, no monitor redesign.
- One remaining minor ambiguity risk: Task 12.3 (Queue) says "if the improvement
  would require threading context, just document and stop." This is correct per
  YAGNI and hardening, but the plan author should confirm in external review that
  this language cannot be misread as "never improve any Queue site."

Pass 4 (final re-review after all edits in this session):

- Ran grep for dangerous vague verbs ("update the", "clean up", "improve", "adjust", "refactor", etc.) across the whole plan. All remaining hits are either:
  - in warnings telling the implementer *not* to do those things,
  - in the self-review checklist (which is supposed to contain the bad words as search targets), or
  - inside the prescriptive Task 12 text that was just added (all of which are now concrete with files, commands, and stop gates).
- Re-read Task 12 as a zero-context engineer: every sub-task names exact rg commands, exact files, the precise pragma wording, the exact monitor paragraph text to insert, what harnesses not to use, and what "done" means in observable terms (rg no longer finds the bad pattern).
- Re-checked against writing-plans.md and hardening-plans.md requirements: every task has "Read first" (with order), "what not to do", red-green where practical, anti-mocking, stop gates, rollback notes (trivial for docs/hygiene), and no second path.
- The monitor justification task (12.4) uses the user's required phrasing and forces the implementer to verify the sentence exists in the spec.
- No material direction change from the original user request or from the pre-existing plan content. All additions close the exact gaps identified in the 2026-05 evaluation while staying strictly within hygiene + one required justification paragraph.
- The plan is now implementable by the target audience (skilled Python dev, zero Weft context, questionable taste, tends to over-mock) without guesswork or drift.

## Completion Criteria

The cleanup plan is complete when:

- spec information architecture is clearer in the densest sections
- ownership inventory exists and is current
- code-to-spec references point to current specs
- client API parity with current command/API surfaces is audited and either
  implemented or explicitly classified
- at least one high-value traceability guard has been added or tightened
- test audit allowlist debt has decreased
- constants policy remains strict
- any optional extraction is pure, small, and table-tested
- final gates pass
- external review findings are resolved
