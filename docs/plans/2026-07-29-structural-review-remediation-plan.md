# Structural Review Remediation Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.3], [CC-3.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-1.4.1]
Superseded by: none

Class: 5 — Spec-changing. A risky trigger also fires (a public contract is
changing: the Python client's `spec.validate()` behavior, plus consolidation of
two public snapshot converters), so the `hardening-plans.md` checklist applies
and independent review is required **before** implementation begins.

## 1. Goal

An external structural review of `a391a59b` produced eleven findings. This plan
implements the seven that survived independent verification, in dependency
order, and records the reasoning for the four that were modified or rejected.

The highest-value item was not in the original report: `validate_spec_source()`
accepts `load_runner` and `preflight` and then discards both, so
`WeftClient.spec.validate(source, preflight=True)` returns a passing result with
no preflight having run. That is a silent false negative on a validation API and
is fixed first.

The remaining accepted work removes four verified exact-duplicate converters
that feed public surfaces, breaks one real import cycle, gives the [MF-5] status
reducer a named and table-tested boundary, and repairs two tests that pass while
asserting nothing.

## 2. Source Documents

Governing specs (normative):

- `docs/specifications/01-Core_Components.md` [CC-3.3] — validation and
  preflight layering; [CC-3.4] — monitoring ownership, names `host.py` and
  `subprocess_runner.py`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] — state observation
  flow; owns status reconstruction from `weft.log.tasks`
- `docs/specifications/10-CLI_Interface.md` [CLI-1.4.1] — `weft spec validate`,
  `--load-runner`, `--preflight` semantics (lines 570–590)
- `docs/specifications/07-System_Invariants.md` — invariants below

Local guidance the implementer must follow:

- `CLAUDE.md` §4.2 (imports), §4.5 (docstrings and traceability), §4.7 (error
  handling), §4.11 (validate at the boundary, reject unsupported fields
  explicitly), §7 (handoff protocol)
- `docs/agent-context/engineering-principles.md` §"Two floors" (lines 285–309) —
  floor 2 (named, contract-tested state machines) is the basis for Task 5
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md` — required for
  the CLI/input-parsing surface touched in Task 2

Review input being dispositioned: the external review of `a391a59b`
(`DONE_WITH_CONCERNS`, eleven findings). It is an input, not a governing
document. §11 records the disposition of every finding.

## 3. Context and Key Files

### Current structure — what owns what today

- **Validation.** `weft/commands/validate_taskspec.py` (296 lines) owns spec
  resolution, schema validation, runner/environment/agent-runtime/tool-profile
  preflight (lines 119–194), Rich table rendering, direct `console.print` (24
  sites), and exit-code selection (12× `return 1`, 1× `return 0`). It
  constructs a module-level `Console()` at line 36. `weft/cli/app.py:633` is a
  thin delegate that only calls it and raises `typer.Exit`.
  `weft/commands/specs.py:380` (`validate_spec_source`) is the *structured*
  entry point used by the Python client — it accepts `load_runner` and
  `preflight` and executes `del load_runner, preflight` at line 391.
  `weft/client/_namespaces.py:409` passes both flags through to it.
  **No preflight logic exists outside the printing function.**
- **Status reconstruction.** `weft/commands/system.py:981`
  (`_collect_task_snapshot_records`, 353 lines, C901 36) folds `weft.log.tasks`
  events into `records`, then projects each record to a public
  `CollectedTaskSnapshot`. The projection loop calls
  `task_evidence.task_local_terminal_evidence(ctx, ...)` (~line 1175) and
  `task_evidence.claimed_outbox_result_evidence(ctx, ...)` (~line 1216) — these
  are the only I/O calls inside the projection phase. Zero tests reference the
  function by name.
- **Runner outcome.** `RunnerOutcome` is defined in
  `weft/core/runners/host.py`. `host.py:40` imports from
  `weft/core/runners/subprocess_runner.py`, and `subprocess_runner.py:88`
  imports `RunnerOutcome` back from `host` *inside a function body* with no
  comment naming the cycle.
- **Duplicate converters.** Four verified byte-identical pairs; see Task 3.
  `weft/commands/tasks.py:206` already delegates `_split_stdio` to
  `task_evidence.split_stdio` — the intended shape exists and `result.py` holds
  a stale copy.

### Files to modify

| Task | Files |
|---|---|
| 1 | `docs/specifications/01-Core_Components.md` |
| 2 | `weft/commands/validate_taskspec.py`, `weft/commands/specs.py`, `weft/cli/app.py`, `weft/commands/types.py`, `tests/commands/test_validate_taskspec.py`, `tests/core/test_client.py` |
| 3 | `weft/commands/system.py`, `weft/commands/manager.py`, `weft/commands/result.py`, `weft/commands/tasks.py`, `weft/commands/events.py`, `weft/core/task_evidence.py`, `weft/commands/task_evidence.py`, `tests/core/test_client.py` |
| 4 | `weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`, new `weft/core/runners/outcome.py` |
| 5 | `weft/commands/system.py`, new `weft/commands/task_snapshot_reduce.py`, new `tests/commands/test_task_snapshot_reduce.py` |
| 6 | `tests/tasks/test_tasks_simple.py`, `tests/taskspec/test_taskspec_properties.py`, `tests/specs/taskspec/test_state_transitions.py` |
| 7 | `tests/architecture/test_import_boundaries.py`, `tests/system/test_constants.py` |
| 8 | `docs/specifications/01-Core_Components.md`, `docs/specifications/05-Message_Flow_and_State.md`, `docs/plans/README.md` |

### Read first

- `docs/specifications/01-Core_Components.md` [CC-3.3] (lines 717–735)
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (lines 359–420)
- `weft/commands/validate_taskspec.py` in full — it is short and is the only
  place preflight logic exists
- `weft/commands/types.py:252` (`SpecValidationResult`) and `:224`
  (`ManagerSnapshot`)
- `tests/architecture/test_import_boundaries.py:141`
- `tests/helpers/weft_harness.py`

### Comprehension questions (answer before editing — hardening §14)

1. Which module owns the *only* implementation of agent-runtime preflight
   today, and what does a Python-client caller passing `preflight=True`
   currently receive?
2. Which queue does `_collect_task_snapshot_records` read to reconstruct
   status, and which two calls inside its projection loop perform I/O?
3. `weft status --json` and `client.system.status().managers` — do they go
   through the same converter today? Which one does the CLI actually use?

If you cannot answer these, you are not ready to edit.

### Shared paths — reuse, do not duplicate

- `weft/core/task_evidence.py` is the canonical home for evidence converters.
  `weft/commands/task_evidence.py` is an existing re-export shim (30+ names) —
  add to it rather than creating a new shim.
- `SpecValidationResult.warnings` already exists; carry preflight findings there
  rather than inventing a new result type.
- `weft/core/task_lifecycle.py` already exports `validate_task_status_transition`
  and `valid_task_status_targets` — Task 6 uses these as the reference model.
  Do not write a second transition table.

## 4. Invariants and Constraints

Must not change:

- TID format and immutability; forward-only state transitions; reserved-queue
  policy; `spec`/`io` immutability after resolved TaskSpec creation
- Queue names and the `weft.state.*` runtime-only boundary
- **Public CLI output.** `weft spec validate` human output, exit codes
  (0 success, 1 failure, 2 not found), and every `--json` payload shape must be
  byte-identical before and after Tasks 2 and 3. These tasks change *where* code
  lives and *what the Python client receives*, not what the CLI prints.
- `weft run` must not gain a hidden preflight gate. [CLI-1.4.1] (line 588) and
  [CC-3.3] both state explicitly that `weft run` does not silently perform
  preflight. Task 2 must not change that.
- Terminal lifecycle truth continues to derive from lifecycle events, terminal
  control evidence, and result evidence — never from diagnostics ([MF-5]).

Review gates for this plan:

- no new execution path
- no new dependency (Task 2 *removes* the only `rich` usage; do not remove the
  `pyproject.toml` entry in the same slice — see Task 2 rollback)
- no drive-by refactor outside the named files
- no mock-heavy substitute for a real broker/process proof
- external review required before implementation (class 5 + risky trigger)

### Hidden couplings to respect

- `weft/commands/tasks.py:1096` reaches into `status_cmd._describe_runtime_handle`
  — a third module depending on a `system.py` private. Task 3 must update this
  call site, not leave it dangling.
- `weft/commands/__init__.py:11` imports `.validate_taskspec`, so *any*
  `from weft.commands import ...` constructs a Rich `Console()` at import time.
  Task 2 removes that side effect; confirm no test asserts on it.
- `_collect_task_snapshot_records` accepts injected `now_ns` and
  `service_registry_evidence`. Those are existing test seams — Task 5 must
  preserve both signatures.

### Error-path priorities

- Preflight failure is a **validation result**, not an exception: it populates
  `errors`/`warnings` and drives exit code 1. It must not raise through the
  client.
- A preflight probe that cannot run (plugin import failure, missing runtime)
  is a *reported* failure, not a crash — preserve the existing
  `except Exception` probe behavior at `validate_taskspec.py:127`.
- Evidence-gathering failure inside the [MF-5] reducer stays best-effort and
  must not downgrade an otherwise-correct snapshot (current behavior at
  `system.py:1015`, `# pragma: no cover - defensive status reconciliation`).

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 4b. Spec Baseline

- `c96ab1e1efd90ebeb3bec74e4ae758dc2682e073` — `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/10-CLI_Interface.md` at plan authoring time
- Code baseline: `a391a59b345a8e37dc6d5c362525f84be0f70343`
- Plan type: **implementation with spec revision**
- Promotion baseline identifier: _recorded at the end of Task 1_

## 4c. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/01-Core_Components.md | **A** — in-file, text before link claims | [CC-3.3], bullet list + `_Implementation mapping_` (mapping updated later, in Task 8) |
| docs/specifications/05-Message_Flow_and_State.md | **D** — clarification only | [MF-5], add `_Implementation mapping_` (none exists today) |

Rationale for A on [CC-3.3]: the section already carries an
`_Implementation mapping_` naming `weft/commands/validate_taskspec.py`. That
mapping is accurate today and becomes wrong when Task 2 moves ownership.
Landing new requirement text without link claims in Task 1, then updating the
mapping together with the code in Task 8, avoids reciprocity debt between
landings.

### [CC-3.3] — insert after the existing bullet "ordinary `weft run` submission does not add a hidden…"

> - validation and preflight are capability-layer behavior, not
>   presentation-layer behavior. The shared capability layer owns resolution,
>   schema validation, capability validation, and preflight execution, and
>   returns a structured result. Terminal rendering and exit-code selection
>   belong to the CLI adapter.
> - the programmatic validation surface and the CLI validation surface run the
>   same preflight work. A caller that requests runner loading or preflight
>   receives the result of that work; a validation surface must not accept a
>   preflight request and silently skip it. If a surface cannot honor a
>   validation option, it rejects the option explicitly rather than returning a
>   result that appears to have satisfied it.

### [MF-5] — append at end of section, before `### 6.`

> _Implementation mapping_: public status reconstruction is implemented by
> `weft/commands/system.py` (evidence collection and public projection) and
> `weft/commands/task_snapshot_reduce.py` (the pure event fold and terminal
> precedence reducer). Task-local and claimed-result evidence gathering is
> implemented by `weft/core/task_evidence.py`.

No behavior change is intended by the [MF-5] delta — it records ownership that
[MF-5] already governs but never mapped.

## 5. Tasks

Tasks are dependency-ordered and executed in sequence. Tasks 3, 4, 6, and 7 are
mutually independent and may be reordered among themselves if convenient; Tasks
1 → 2 and 1 → 5 → 8 are strictly ordered.

---

### Task 1 — Spec-promotion slice

- **Outcome:** the [CC-3.3] and [MF-5] deltas in §4c are landed in
  `docs/specifications/`, with no implementation-link claims added yet.
- **Files to touch:** `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md`
- **Read first:** §4c above; `writing-plans.md` §4d promotion strategies
- **Constraints:** do **not** edit the existing `_Implementation mapping_` line
  in [CC-3.3] in this task — that happens in Task 8 alongside the code. Do not
  cite the new [CC-3.3] bullets from code until Task 2.
- **Done when:** both deltas are present verbatim; `bin/check-doc-paths` passes;
  the promotion baseline identifier (diff base + worktree state, or a commit
  SHA) is recorded in §4b of this plan.

---

### Task 2 — Make the validation capability honor `load_runner` and `preflight`

This is the defect slice. **Red-green TDD is required here.**

- **Outcome:** `WeftClient.spec.validate(source, preflight=True)` actually runs
  preflight and reports its findings. `weft/commands/` no longer imports Rich.
  CLI output and exit codes are unchanged.
- **Files to touch:** `weft/commands/validate_taskspec.py`,
  `weft/commands/specs.py`, `weft/cli/app.py`, `weft/commands/types.py`,
  `tests/commands/test_validate_taskspec.py`, `tests/core/test_client.py`
- **Read first:** `weft/commands/validate_taskspec.py` in full;
  `weft/commands/specs.py:380–420`; `weft/client/_namespaces.py:405–423`;
  [CC-3.3] as promoted in Task 1
- **Approach:**
  1. **Write the failing test first.** In `tests/core/test_client.py`, assert
     that `client.spec.validate(<agent spec with an unavailable runtime>,
     preflight=True)` returns `valid is False` (or a populated `warnings`,
     per the shape you settle on in step 2). This must fail red against
     `a391a59b` — confirm the red before writing implementation.
  2. Extract the preflight body currently at
     `validate_taskspec.py:119–194` into a function in
     `weft/commands/validate_taskspec.py` that **returns** structured findings
     instead of printing: reuse `SpecValidationResult`
     (`weft/commands/types.py:252`) — it already carries `errors`, `warnings`,
     and `payload`. Do not add a new result dataclass.
  3. Have `validate_spec_source()` (`specs.py:380`) call that function when
     `load_runner` or `preflight` is set, and delete the
     `del load_runner, preflight` line.
  4. Move all `console.print` and `Table` construction to `weft/cli/app.py`,
     rendering from the returned `SpecValidationResult`. Delete the
     module-level `Console()` at `validate_taskspec.py:36` and the three `rich`
     imports at lines 15–17.
  5. Keep `cmd_validate_taskspec()`'s signature and integer return contract
     intact so `weft/cli/app.py:633` still receives an exit code.
- **Reuse:** `SpecValidationResult`; the existing
  `validate_taskspec_runner_environment` and `weft/core/agents/validation.py`
  probes. Do not reimplement any probe.
- **Not allowed:** changing which checks run or their order; adding preflight to
  `weft run`; introducing a rendering abstraction or a `Renderer` protocol —
  render inline in the CLI adapter like the pipeline branch at
  `weft/cli/app.py:646–653` already does.
- **Stop and re-evaluate if:** the extraction requires threading a
  `WeftContext` into a probe that does not take one today; the CLI output
  cannot be reproduced byte-for-byte from the structured result; or the change
  starts wanting a second validation path. **Fallback if blocked:** make
  `validate_spec_source()` raise an explicit error for unsupported options
  (CLAUDE.md §4.11) rather than leaving the silent drop in place, record a
  Deviation Log row, and open a follow-up for the wire-through. A loud
  rejection is an acceptable landing state; a silent drop is not.
- **Tests:** the red test from step 1; a CLI golden-output test asserting
  `weft spec validate` human output and exit codes are unchanged; an
  adversarial probe pass per
  `runbooks/adversarial-acceptance-probes.md` (this is CLI-facing,
  input-parsing work).
- **Rollback:** the slice is self-contained and revertible. Leave the
  `rich>=14.0.0` entry in `pyproject.toml` in place for this slice so a revert
  does not require a dependency change; removing it is a separate follow-up
  once the tree is confirmed Rich-free.
- **Done when:** the red test passes; CLI output tests pass unchanged;
  `grep -rn "rich" weft/` returns no import in `weft/commands/`.

---

### Task 3 — Consolidate the four verified duplicate converters

- **Outcome:** one implementation per converter; public surfaces cannot drift
  apart. Behavior-preserving.
- **Verified pairs (all byte-identical bodies):**

  | Pair | Copies | Canonical home | Note |
  |---|---|---|---|
  | `_manager_snapshot` | `commands/manager.py:49`, `commands/system.py:2116` | `commands/manager.py` | `system.py:51` already imports from `manager.py`; delete the `system.py` copy and extend the import |
  | runtime description | `core/task_evidence.py:754` (`_runtime_description`), `commands/system.py:953` (`_describe_runtime_handle`) | `core/task_evidence.py` | promote to public, add to the `weft/commands/task_evidence.py` re-export shim, delete the `system.py` copy, **and update `commands/tasks.py:1096`** which reaches into the `system.py` private |
  | `split_stdio` | `core/task_evidence.py:198`, `commands/result.py:781` | `core/task_evidence.py` | already public and already re-exported; `commands/tasks.py:206` shows the exact delegation shape to copy |
  | deadline helpers | `commands/tasks.py:307`, `commands/events.py:55` | `commands/tasks.py` | `events.py:21` already imports `tasks`. Note `events.py:67` has a third helper `_timed_out` and `tasks.py:961` inlines the same check — unify on the `events.py` shape |

- **Also in this task:** delete four helpers in `weft/commands/tasks.py` that
  are referenced nowhere in `weft/` or `tests/` and are leftovers from a
  partially-completed consolidation: `_mapping_has_prior_live_proof` (:327),
  `_runtime_snapshot_from_mapping` (:335), `_bounded_log_terminal_snapshot`
  (:348), `_stale_observer_snapshot` (:365). Re-verify they are unreferenced
  before deleting.
- **Constraints:** layering (`core` ↛ `commands`) permits every move above —
  each canonical home is reachable through an import edge that already exists
  in the consuming file. Do not add a new shared module. Do not change any
  field, name, or output shape.
- **Stop and re-evaluate if:** any "identical" pair turns out to differ after
  your own `diff` — re-verify each pair yourself before deleting a copy.
- **Tests:** add a divergence-catching test to `tests/core/test_client.py`
  asserting `client.managers.list()` and `client.system.status().managers`
  return equal field-by-field snapshots for the same manager. The existing
  `test_system_and_manager_namespaces_expose_shared_runtime_state` (:472)
  asserts only `item.tid` — strengthen it rather than adding a parallel test.
- **Done when:** each converter has exactly one definition; the new equality
  test passes; full `tests/commands/` and `tests/core/` are green.

---

### Task 4 — Break the `host` ↔ `subprocess_runner` import cycle

- **Outcome:** no function-level import used to break a module cycle in the
  runners package.
- **Files to touch:** new `weft/core/runners/outcome.py`,
  `weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`
- **Read first:** CLAUDE.md §4.2 (function-level imports are allowed only to
  break a real cycle or guard an optional dependency, **and must carry a
  comment naming that reason** — the import at `subprocess_runner.py:88` has
  no such comment, so the current state violates house style either way);
  [CC-3.4], which names both modules.
- **Approach:** move the `RunnerOutcome` dataclass to
  `weft/core/runners/outcome.py`; import it normally at module level in both
  files; delete the function-level import at `subprocess_runner.py:88` and the
  `TYPE_CHECKING` import at `:32`. Re-export `RunnerOutcome` from `host` if any
  external caller imports it from there — check first with
  `grep -rn "RunnerOutcome" weft/ tests/ extensions/ integrations/`.
- **Not allowed:** moving anything else out of `host.py`; this is a one-symbol
  move.
- **Tests:** no new behavior, so no new behavioral test. The proof is Task 7's
  cycle assertion plus green `tests/tasks/` and `tests/core/`.
- **Done when:** `mypy` and `ruff` pass; the Task 7 cycle test (written later)
  covers this permanently; `tests/tasks/test_runner.py` is green.

---

### Task 5 — Give the [MF-5] status reducer a named, table-tested boundary

This is the structural core of the plan and the one with the largest blast
radius. It is **behavior-preserving**.

- **Outcome:** the event fold and terminal-precedence decisions in
  `_collect_task_snapshot_records` become a named, independently testable unit
  with a contract test, per `engineering-principles.md` floor 2.
- **Files to touch:** `weft/commands/system.py`, new
  `weft/commands/task_snapshot_reduce.py`, new
  `tests/commands/test_task_snapshot_reduce.py`
- **Read first:** [MF-5] (lines 359–420) — note the rule "Verification must
  assert both sides: public status/result reconstruction remains correct from
  retained task evidence"; `engineering-principles.md` lines 285–309, which
  names `reducer decision ordering` and `control signal precedence` as floor-2
  triggers and gives `weft/core/task_lifecycle.py` +
  `weft/core/state_machines.py` (contract-tested in
  `tests/core/test_state_machines.py`) as the repo's model case — **read that
  model case before designing this one**.
- **Approach:**
  1. Extract the event-folding phase (`system.py` ~1010–1147, everything
     between `log_queue = _queue(...)` and `records_out = []`) into a **pure**
     function in the new module: `(events, tid_filters, ...) -> dict[str, dict]`.
     It must perform no I/O; the caller passes the already-read events.
  2. Extract the terminal-precedence and staleness decisions from the
     projection loop into a **pure** reducer taking `(record, local_evidence,
     claimed_evidence, manager_evidence, now_ns) -> public snapshot fields`.
     The two I/O calls at ~1175 and ~1216
     (`task_evidence.task_local_terminal_evidence`,
     `task_evidence.claimed_outbox_result_evidence`) stay in `system.py` and
     their results are **passed into** the reducer. Do not move I/O into the
     new module.
  3. `_collect_task_snapshot_records` keeps its current signature — including
     the injected `now_ns` and `service_registry_evidence` seams — and becomes
     the I/O orchestrator that calls both pure units.
- **Not allowed:** splitting `system.py` for size; moving the function to
  `weft/core/` (it is a commands-layer capability and `core` must not depend on
  it); changing precedence order, staleness windows, or any output field;
  introducing a state-machine library.
- **Stop and re-evaluate if:** the "pure" fold turns out to need `ctx` — that
  means the phase boundary is in the wrong place; re-read before forcing it.
  Also stop if extraction requires passing more than ~6 parameters, which
  signals the seam is false (see `engineering-principles.md` line 282:
  "Splitting genuinely coupled code manufactures false seams").
- **Tests:** a **table-driven** contract test in the new test file covering, at
  minimum: terminal precedence (terminal event wins over live liveness);
  task-local evidence overriding non-terminal lifecycle status; claimed-result
  evidence precedence; manager/service-owner reconciliation; the stale-window
  boundary on both sides; and same-status non-terminal updates. Model the table
  shape on `tests/core/test_state_machines.py`. **Do not mock queues** — the
  pure units take plain data, so the table test needs no broker at all. Keep
  the existing end-to-end `tests/commands/test_status.py` coverage green as the
  other side of the [MF-5] verification rule.
- **Rollback:** behavior-preserving and self-contained; revert restores the
  single function. Nothing persists across the change.
- **Done when:** the table test passes; `tests/commands/test_status.py`,
  `tests/commands/test_tasks.py`, and `tests/core/test_client.py` are green
  with no assertion changes; `ruff --select C901` shows
  `_collect_task_snapshot_records` materially reduced.

---

### Task 6 — Repair two tests that pass while asserting nothing

- **Outcome:** two tests assert the behavior their names claim.
- **Files to touch:** `tests/tasks/test_tasks_simple.py`,
  `tests/taskspec/test_taskspec_properties.py`,
  `tests/specs/taskspec/test_state_transitions.py`
- **6a — `test_custom_taskspec_queues` (`test_tasks_simple.py:122`).** It
  asserts `task.taskspec.io.inputs[...]` — the object it constructed and passed
  in. `test_task_has_basic_attributes` at :168 proves `task.taskspec is
  taskspec`, so these assertions are tautological. Re-point them at the
  **resolved** names: `task._queue_names["outbox"] == "custom.outbox"`,
  `task._queue_names["ctrl_in"] == "custom.control.in"`, etc. (see
  `weft/core/tasks/base.py:361`, `_resolve_queue_names`).
  **Expect a surprise:** `_resolve_queue_names` reads only the `inbox` key from
  `io.inputs`; the test's custom `data` and `config` input queues are never
  consulted. Do **not** "fix" that by widening `_resolve_queue_names` — that
  would be a behavior change outside this plan. Assert what the resolver
  actually does, and record the question in the Deviation Log with a pointer
  for follow-up. Apply the same treatment to `test_required_queues` (:107),
  which currently asserts the fixture, not the Consumer.
- **6b — the two property tests** (`test_taskspec_properties.py:228`,
  `test_state_transitions.py:168`). Both wrap every generated operation in
  `except ValueError: pass`, so all assertions are skipped if no transition
  ever succeeds. **Scope note:** verification showed these tests *do* catch
  realistic bugs — mutating `mark_started` to transition without setting
  `started_at` fails them immediately — and `test_state_transitions.py:84`
  already table-tests the full transition pair matrix. So the fix is small, not
  a redesign: assert that at least one operation in each generated sequence
  succeeded, so a totally-broken transition surface cannot pass vacuously. Do
  **not** build a second reference transition model — `validate_task_status_transition`
  in `weft/core/task_lifecycle.py` already exists if you need one.
- **Done when:** 6a fails against a `_resolve_queue_names` stubbed to ignore
  custom names (verify this red), and passes after; 6b fails when every
  transition is forced to raise, and passes otherwise.

---

### Task 7 — Add the guardrails that would have caught these

- **Outcome:** the class of defect fixed above cannot silently return.
- **Files to touch:** `tests/architecture/test_import_boundaries.py`,
  `tests/system/test_constants.py`
- **7a — Rich guard.** `test_internal_import_boundaries` (:141) already tracks
  `typer_violations` separately and confines Typer to `weft.cli`. Extend the
  same mechanism to `rich`. This is a few lines in an existing loop; do not
  write a new test function.
- **7b — Import-cycle floor.** Add a test asserting the runtime import graph of
  `weft/` contains no strongly connected component among modules (excluding
  `TYPE_CHECKING`, platform, optional-dependency, and plugin-loading imports —
  the existing `_iter_import_edges` helper already parses edges). This locks in
  Task 4 and is the permanent form of review finding 2.
- **7c — Config key parity.** Add a test asserting the live key sets of
  `_load_weft_env_vars()` and `_normalize_weft_override_value()`
  (`weft/_constants.py:2271` and `:2480`) agree, with an explicit allowlist for
  the four known-intentional asymmetries: `WEFT_MANAGER_RUNTIME_HANDLE_JSON`
  (identity parser; normalizer fall-through is equivalent), the three
  removed-key rejection guards, and the internal
  `_WEFT_MANAGER_SERVE_LOG_ACTIVE`. Extract the key sets by AST or by
  introspection — do not hand-copy a third list, which would create the same
  drift problem a third time.
- **Done when:** each new test fails when its invariant is deliberately broken,
  and passes on the current tree.

---

### Task 8 — Traceability reconciliation (final slice)

- **Outcome:** the spec ↔ plan ↔ code graph closes.
- **Files to touch:** `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, `docs/plans/README.md`,
  plus module docstrings in the files touched by Tasks 2–5
- **Actions:**
  1. Update the [CC-3.3] `_Implementation mapping_` to name the new ownership
     after Task 2 (`weft/commands/specs.py` and `weft/cli/app.py` join
     `weft/commands/validate_taskspec.py`).
  2. Land the [MF-5] `_Implementation mapping_` from §4c, now that
     `weft/commands/task_snapshot_reduce.py` exists.
  3. Add this plan to the `## Related Plans` section of
     `docs/specifications/01-Core_Components.md` (line 765) and
     `docs/specifications/05-Message_Flow_and_State.md` (line 1306).
  4. Add reciprocal `Spec:` docstring backlinks in
     `weft/commands/task_snapshot_reduce.py` ([MF-5]),
     `weft/commands/specs.py` ([CC-3.3]), and
     `weft/core/runners/outcome.py` ([CC-3.4]), per CLAUDE.md §4.5.
  5. Add the index row to `docs/plans/README.md` and update the plan count
     (currently stated as 155).
  6. Close the Deviation Log — no row may remain `pending`.
  7. Add a `docs/lessons.md` entry **only if** implementation exposed a
     repeated pattern. Do not add ceremony for its own sake.
- **Done when:** `tests/specs/test_plan_metadata.py` and `bin/check-doc-paths`
  pass; no `_Implementation mapping_` names a module that no longer owns the
  behavior.

## 6. Testing Plan

Harness and fixtures:

- `WeftTestHarness` (`tests/helpers/weft_harness.py`) for anything touching CLI,
  manager, or lifecycle behavior (Tasks 2, 3).
- `broker_env` / real `Queue` instances for queue semantics (Task 6a).
- Task 5's table test needs **no** harness — the extracted units are pure. If
  you find yourself building a broker fixture for it, the extraction is wrong.

Do **not** mock:

- broker-backed queues, reservation semantics, outbox/task-log behavior
- manager or task lifecycle
- the runner plugin probes in Task 2 — use a spec naming a genuinely
  unavailable runtime instead of patching the probe, so the test proves the
  real path

Mock only: clocks (`now_ns` is already injectable), and genuinely external
provider SDKs.

Red-green requirements:

- Task 2: the client-preflight test **must** be proven red against `a391a59b`
  before implementation.
- Task 6a: prove red by stubbing `_resolve_queue_names` to ignore custom names.
- Task 6b: prove red by forcing every transition to raise.
- Tasks 3, 4, 5 are behavior-preserving; red-green does not apply. The
  equivalent proof is: existing tests stay green with **no assertion edits**,
  plus the new divergence test (Task 3) and table test (Task 5). If a
  pre-existing assertion needs changing, stop — that means behavior moved.

Edge case explicitly in scope: Task 3's manager-snapshot equality test must
compare *every* field, not just `tid`. The whole point is that a defaulted new
field is exactly what would silently diverge.

Edge case explicitly out of scope: exhaustive preflight coverage for every
runner plugin. Task 2 proves the wire-through with one representative
unavailable runtime; per-plugin preflight behavior is already covered where it
exists.

## 7. Verification and Gates

Per-task (while implementing):

```bash
./.venv/bin/python -m pytest tests/commands/test_validate_taskspec.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/tasks/test_tasks_simple.py tests/specs/taskspec/ tests/taskspec/ -q
./.venv/bin/python -m pytest tests/architecture/ -q
```

Final gates (before claiming done). Load `.envrc` first; do not assume global
tools:

```bash
./.venv/bin/python -m pytest -m ""
```

```bash
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

```bash
./.venv/bin/ruff check weft
```

```bash
./bin/check-doc-paths && ./bin/check-dom15-fixtures
```

Full-suite gates are required — the blast radius crosses commands, core,
runners, and the client surface. A narrow slice run is not sufficient for the
completion claim.

Observable success beyond local tests:

- `weft spec validate <spec> --preflight` and
  `client.spec.validate(spec, preflight=True)` report the **same** findings for
  the same spec — this is the defect's disappearance, and it is worth
  exercising by hand once.
- `weft status --json` and `weft task list --json` payloads are unchanged
  before/after Task 3 for a real completed task.
- `weft run echo hello` still completes with no preflight gate introduced.

## 8. Independent Review Loop

Required **before implementation begins** (class 5 + risky trigger).

- **Reviewer:** a different agent family than the author where available. The
  author of the original structural review should also review, since §11
  modifies or rejects four of their findings and they hold the most context on
  the rejected reasoning.
- **Reviewer should read:** this plan in full including `## Proposed Spec
  Delta` and §11; `docs/specifications/01-Core_Components.md` [CC-3.3];
  `docs/specifications/05-Message_Flow_and_State.md` [MF-5];
  `docs/agent-context/engineering-principles.md` lines 285–309;
  `weft/commands/validate_taskspec.py`; `weft/commands/specs.py:380–400`;
  `weft/commands/system.py:981–1334`.
- **Review prompt:**

  > Read the plan and its `## Proposed Spec Delta`, including the named
  > promotion strategy. Examine the plan, the proposed spec text, and the
  > associated code. Look for errors, bad ideas, latent ambiguities, and
  > performative overengineering — process, abstraction, or ceremony that does
  > not address a real risk; recommending removal is as valuable as
  > recommending additions. Pay particular attention to §11: are the four
  > modified/rejected findings correctly reasoned, or is the plan rationalizing
  > away real work? Don't implement. Answer carefully: could you implement this
  > confidently and correctly against the delta as promoted, if asked?

- **Feedback handling:** the author considers each point explicitly and either
  updates the plan, explains why the current path is still best, or records the
  point as out of scope. Disagreement about §11 in particular should be
  resolved before Task 2 starts.

## 9. Out of Scope

- Removing `rich>=14.0.0` from `pyproject.toml` — a follow-up once the tree is
  confirmed Rich-free (keeps Task 2 revertible without a dependency change).
- Widening `_resolve_queue_names` to honor arbitrary named input queues (see
  Task 6a) — a real question, but a behavior change needing its own spec work.
- Any change to `weft run`'s preflight posture.
- Splitting `weft/helpers/__init__.py` (see §11, finding 9).
- Reducing C901 across the other ~100 functions over the default threshold.
- Enabling `C901` in the enforced ruff rule set. The measured distribution
  (105 functions over 10; max 69) means enabling it at the default threshold
  would fail the build immediately. If it is ever enabled, start near 20 with
  named exceptions — not in this plan.
- Renaming for style (see §11, finding 11).

## 10. Fresh-Eyes Review

Completed as a separate pass after drafting. Findings and fixes:

1. *Ambiguity:* Task 2 originally said "return structured findings" without
   naming a type — a zero-context implementer would have invented a dataclass.
   Fixed by naming `SpecValidationResult` and its existing `warnings` field.
2. *Missing constraint:* Task 3 did not mention `commands/tasks.py:1096`, which
   reaches into the `system.py` private being deleted. Added — without it the
   implementer breaks a third module.
3. *False-seam risk:* Task 5 could have been read as "move the function to
   `core/`", which layering forbids. Added the explicit prohibition and the
   ~6-parameter stop gate.
4. *Hidden landmine:* Task 6a would surprise the implementer when custom
   `data`/`config` queues turn out to be unused. Called out explicitly with an
   instruction *not* to fix it in this plan.
5. *Overstated scope:* Task 6b was initially written as "build a reference
   transition model." Verification showed the property tests already catch
   realistic bugs and a full pair-matrix table test already exists, so the task
   was reduced to a one-line guard. Recorded in §11.
6. *Rollback gap:* removing `rich` from `pyproject.toml` inside Task 2 would
   have made the slice non-revertible without a dependency edit. Moved to §9.

Remaining known ambiguity: Task 5's exact phase boundary (where the fold ends
and the projection begins) is described by line ranges that will shift as the
implementer edits. The stop gates are the safety net; if the boundary is
unclear on contact, re-plan rather than guessing.

## 11. Disposition of the Source Review

The external review's measurements were accurate — every complexity score and
clone pair reproduced exactly on independent check. Four findings are modified
or rejected, with reasoning, because verification changed the picture.

| # | Finding | Disposition | Reasoning |
|---|---|---|---|
| 1 | Eager package init / SCCs | **Partially accepted** → Task 7b | The 107-module count is real. But a lazy facade was measured to save ~27ms of 186ms on the spawn path, not the bulk the module count implies — most of those modules are core modules the Consumer genuinely needs. The dominant cost is `import llm` (~51–57ms) via `weft/core/agents/backends/__init__.py:10`, on the **core** path, which a lazy root facade would not remove. The durable value is the cycle/import-floor test, which is adopted. Lazy agent-backend registration is a separate performance question with its own trade-offs and is not smuggled into a structural plan. |
| 2 | `host` ↔ `subprocess_runner` cycle | **Accepted** → Task 4 | Confirmed. Strengthened: the lazy import at `subprocess_runner.py:88` carries no comment, which independently violates CLAUDE.md §4.2. |
| 3 | Config duplication, C901 69 | **Modified** → Task 7c (parity test only) | Complexity 69 confirmed exactly; no parity gate confirmed. But the two functions are not duplicate policy — one supplies defaults and parses raw env strings, the other coerces already-typed values and *rejects* bad types; the parsers are already shared. The override path has exactly one production caller (`weft/commands/serve.py:32–37`, at most three fixed keys with already-correct types), and `TaskMonitorRuntimeConfig.from_config` independently re-coerces and re-validates, so most drift is caught downstream. The genuine silent-wrong surface is bool keys (`bool("0") is True`) and `WEFT_DIRECTORY_NAME`. A declarative key registry would be a large change to a 2,817-line file for a narrow hazard; a parity test closes nearly all the risk for ~15 lines. Rejecting the registry, accepting the gate. |
| 4 | `_collect_task_snapshot_records` unnamed state machine | **Accepted and escalated** → Task 5 | The review filed this as one P2 among several; it is the strongest structural item. It does **not** conflict with CLAUDE.md §1.1 "size is not a smell": `engineering-principles.md:303–309` distinguishes structural coupling (safe at any size) from behavioral coupling, names "reducer decision ordering" as a floor-2 trigger, and supplies the exact precedent (`state_machines.py` + `tests/core/test_state_machines.py`, both of which exist). [MF-5] additionally requires that "public status/result reconstruction remains correct from retained task evidence" be verified. Zero tests reference the function by name. The review's own prescription — extract a pure fold, don't split the file for size — matches repo governance verbatim. |
| 5 | Result/follow/interactive shared observation policy | **Rejected as framed; one part accepted separately** | The four functions have genuinely different consumption and cancellation semantics (persistent batches, read-only follow, one-shot wait, interactive session), and the review itself concedes they are "not wholesale duplicates." Extracting a shared reducer across four different semantics is precisely the speculative abstraction CLAUDE.md forbids ("do not introduce new abstractions unless you are forced to duplicate logic") and risks a false seam per `engineering-principles.md:282`. **However**, `_run_interactive_session` (C901 55 across 267 lines, no direct test) is the worst single function on the runtime path and merits floor-2 treatment on its own terms. That is not bundled here — it deserves its own plan rather than being smuggled in as a side effect of a de-duplication argument that does not hold. |
| 6 | Rich in `commands/` | **Accepted, and its real defect promoted** → Task 2 | Confirmed and isolated: 1 of 34 modules; Rich appears exactly once in all of `weft/`. The layering violation itself is cosmetic. The consequential part, which the review did not surface, is directly downstream of it: `specs.py:391` executes `del load_runner, preflight`, so `client.spec.validate(..., preflight=True)` is a silent false negative. That is the plan's first code slice. |
| 7 | Vacuous property tests | **Accepted, scope reduced** → Task 6b | Real, but narrower than filed. Mutating `mark_started` to skip its timestamp fails both tests immediately — they do catch realistic bugs. They are vacuous only under "make every transition raise," an unrealistic mutation, and `test_state_transitions.py:84` already table-tests the full transition pair matrix. Fix reduced from "build a reference model" to a one-line guard. |
| 8 | Duplicate converters | **Accepted** → Task 3 | All four pairs independently diffed and confirmed byte-identical. Layering blocks none of the moves. Two pairs have partial dedup already done elsewhere (`tasks.py:206` delegates `split_stdio`; `tasks.py:1096` reaches into `system.py`'s private), which is evidence of drift rather than design. Four dead helpers found nearby are removed in the same task. |
| 9 | `helpers/__init__.py` mixes domains | **Deferred, with the rationale corrected** | The cohesion claim is correct and understated: 953 lines of implementation across seven domains, only 6 of 45 definitions touch module state, and caller sets are largely disjoint. But the review's stated justification — transitive import cost — is false: the marginal cost of `weft.helpers` is 0.0 ms and 0 added modules, because `weft/__init__.py` imports `.client` one line earlier and psutil is independently required by `weft/core/resource_monitor.py`. A split is defensible on navigability grounds alone, but it is a wide, low-urgency mechanical change that would collide with Tasks 3–5 across the same tree. Deferred to its own plan. |
| 10 | Tautological queue tests | **Accepted** → Task 6a | Confirmed: the test asserts against the same object it passed in, proven by `test_task_has_basic_attributes` asserting `task.taskspec is taskspec`. Repairing it also surfaces that `_resolve_queue_names` ignores non-`inbox` input queues — recorded, not fixed here. |
| 11 | Three weak names | **Rejected** | The two long names encode discriminators the proposed shortenings drop. `_requeue_public_reserved_spawn_requests_before_yield` → `requeue_reserved_requests()` loses "public" (which distinguishes `weft.spawn.requests` from `weft.spawn.internal`) and the "before yield" precondition; `list_terminal_control_deleted_disposition_backfill_tasks` → `list_missing_dispositions()` loses "terminal", "control-deleted", and "backfill". Both renames are lossy in a codebase whose house style favors grep-ability and encoded preconditions — the review concedes this counterargument itself. `body()` is idiomatic as a record accessor; the mildly interesting thing at `store.py:407` is the silent `{}` on `JSONDecodeError`, which is a different concern and may well be intentional. No rename campaign. |

## 12. Completion Record

Completed 2026-07-29 through four focused implementation plans:

- [`2026-07-29-validation-capability-layering-plan.md`](./2026-07-29-validation-capability-layering-plan.md), landed in `e7b5942e8e6c1f3760678268a3f851735d1228f1`.
- [`2026-07-29-import-boundary-remediation-plan.md`](./2026-07-29-import-boundary-remediation-plan.md), landed in `a692c08becd6db2d8c0672828ce487dc10b08354`.
- [`2026-07-29-task-snapshot-reducer-plan.md`](./2026-07-29-task-snapshot-reducer-plan.md), landed in `05070d79193f6098e18a3b23d6aa935ca98242d2`.
- [`2026-07-29-deduplication-and-test-integrity-plan.md`](./2026-07-29-deduplication-and-test-integrity-plan.md), landed in `834839bfa1488858fbc797a669906f6aa7f82fd9`.

Each focused plan records its implementation review and verification evidence.
Together they cover the accepted work from this umbrella plan; the rejected and
deferred findings remain dispositioned in section 11 rather than open work.
