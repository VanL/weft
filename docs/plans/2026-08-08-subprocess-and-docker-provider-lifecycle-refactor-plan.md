# Subprocess And Docker Provider Lifecycle Refactor Feasibility Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]; docs/specifications/02-TaskSpec.md [TS-1.3]; docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1], [RM-5.2]; docs/specifications/07-System_Invariants.md [RES.1]-[RES.6], [EXEC.2]-[EXEC.4], [EXEC.9]; docs/specifications/08-Testing_Strategy.md [TS-1], [TS-3], [TS-3.1]; docs/specifications/13-Agent_Runtime.md [AR-7], [AR-9]
Superseded by: none

Class: 4 — this evaluates refactor feasibility for two live execution owners
that start external runtimes, publish runtime identity, stream output, enforce
cancellation, timeouts, and limits, map terminal outcomes, and guarantee
cleanup. Intended behavior is unchanged, but an incorrect split can misclassify
a completed process as timed out, publish an unusable runtime handle, lose output, skip
monitor or container cleanup, or let secondary callback failure replace the
primary result.

Plan type: feasibility and suppression disposition without a normative behavior
change.

Hardening: required. Both functions are on the durable execution spine and own
real process or container side effects.

## 1. Goal

Resolve the dedicated-plan deferrals for these `C901` suppressions without
manufacturing source churn:

- `weft/core/runners/subprocess_runner.py::run_monitored_subprocess`
  (`RUFF-SUP-036`)
- `extensions/weft_docker/weft_docker/agent_runner.py::DockerProviderCLIRunner.run_with_hooks`
  (`RUFF-SUP-202`)

The code already has the needed runtime model. `run_monitored_subprocess` owns a
single process, two stream readers, one optional monitor, and terminal priority.
The Docker runner owns a fresh one-shot container with one small polling loop.
This plan does not authorize a new state machine, lifecycle frame, event model,
generic reducer, callback strategy, or cross-runner execution framework.

The authoring feasibility audit proved that every currently defensible seam is
insufficient. The subprocess target starts at 25 and bottoms out at 20 after all
five permitted McCabe decisions move. The Docker target starts at 24 and bottoms
out at 14 after all ten permitted decisions move. Both remain above 10. Reaching
the gate would therefore require mechanisms already rejected for locality:
state-threading lifecycle helpers in subprocess, or movement of the Docker
callback/provider/cleanup boundaries whose clean reviews approved adjacency.

Primary success for this plan is an independently verified feasibility result
and explicit owner disposition for each group. The evidence-backed recommendation
is to retain both suppressions and make no production or test changes. If the
owner wants to broaden either mechanism budget despite that evidence, stop and
revise this plan before source work. Do not implement a candidate known in
advance to be incapable of the close gate.

## 2. Requested Outcomes

- [x] Preserve authoritative process completion before elapsed-time timeout in
  `run_monitored_subprocess`.
- [x] Preserve current subprocess priority and outcomes: cancellation, completed
  process plus closed streams, live-process timeout, resource limit, nonzero
  exit, and success.
- [x] Preserve incremental stdout/stderr draining, live chunk callbacks, final
  chunk delivery, exact worker/runtime identity, metrics, and diagnostics.
- [x] Preserve configured-monitor failure containment and its phase-specific
  fixed warnings. Do not move or widen the five `RUFF-SUP-311` catches.
- [x] Preserve ordinary subprocess callback containment and fatal callback
  cleanup/identity. Do not move or widen the two `RUFF-SUP-350` catches.
- [x] Preserve Docker preparation order: normalize the work item, resolve the
  provider/runtime/image/workdir, prepare runtime mounts and invocation, reject
  stdin, then create and start the container.
- [x] Preserve Docker runtime-handle publication only after the runtime-start
  probe succeeds, including the current handle fields and callback order.
- [x] Preserve container creation options, declared mapping-merge order,
  cancellation-before-timeout polling, log collection, outcome mapping, OOM
  override, and unconditional best-effort removal.
- [x] Keep Docker callback, provider-result, and cleanup catches governed by
  `RUFF-SUP-278` through `RUFF-SUP-281` at their existing owner boundaries.
- [x] Add no behavior or test solely to guard against a theoretical issue. This
  feasibility disposition requires no new source or test mechanism.
- [x] Add no shared abstraction between the subprocess and Docker runners. Their
  runtime APIs, stream behavior, monitoring, result parsing, and cleanup differ.
- [x] Obtain explicit owner disposition for `RUFF-SUP-036` and `RUFF-SUP-202`.
  The recommended disposition is retention with zero policy delta.
- [x] If the owner instead authorizes a wider mechanism budget, revise and
  independently re-review this plan before editing source.
- [x] Change no public API, TaskSpec field, runtime-handle schema, callback
  signature, outcome text, timeout value, monitor cadence, container option,
  provider result, log message, or cleanup policy.

## 3. Source Documents And Historical Context

Normative owners:

- `docs/specifications/01-Core_Components.md` [CC-3.2] owns runtime-handle
  authority and shape. [CC-3.4] owns monitoring at the runner boundary and
  runner diagnostics as operational evidence rather than lifecycle truth.
- `docs/specifications/02-TaskSpec.md` [TS-1.3] owns runner selection,
  runner-specific options, and the Docker-backed one-shot `provider_cli` lane.
- `docs/specifications/06-Resource_Management.md` [RM-5] owns runner-local
  monitoring, [RM-5.1] owns the default monitor and cadence, and [RM-5.2] owns
  work-execution timeout classification.
- `docs/specifications/07-System_Invariants.md` [RES.1]-[RES.6] own limit
  placement, metrics, and enforcement. [EXEC.2]-[EXEC.4] own active-loop
  timeout, runtime-handle publication, and terminal return codes. [EXEC.9]
  requires cleanup before terminal return.
- `docs/specifications/08-Testing_Strategy.md` [TS-1] owns real subprocess and
  runner proof. [TS-3] owns the complexity-10 simplify-or-register rule.
  [TS-3.1] owns exact suppression identity and atomic reconciliation.
- `docs/specifications/13-Agent_Runtime.md` [AR-7] owns the Docker-backed
  one-shot provider lane. [AR-9] maps shared provider preparation and runtime
  ownership.
- `docs/ruff-suppression-registry.md` records the current exceptions. It is an
  operational ledger, not a normative behavior source, and is read only for
  suppression disposition and close tasks.

Historical plans and lessons are rationale, not new requirements:

- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`
  deliberately deferred both functions because score-driven helper extraction
  would pass live lifecycle state around without a dedicated review.
- `docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`
  introduced the fresh-container one-shot provider path and its ordered
  preparation, result, and cleanup contract.
- `docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`
  keeps provider runtime requirements in core and Docker-specific lifecycle in
  the extension. This plan does not move that boundary.
- `docs/lessons.md` (2026-05-27, Runner Timeout Boundary) requires an
  authoritative completion probe before timeout classification.
- `docs/lessons.md` (2026-04-20, external runner cleanup) requires container
  cleanup in `finally`.
- `docs/lessons.md` (2026-06-17, optional runner SDK boundaries) requires SDK
  contract assertions to use the installed SDK surface when that surface is the
  subject. Deterministic container-state branch tests may use the existing
  owner fake, but may not claim to prove Docker SDK compatibility.

## 4. Context And Key Files

Required repository guidance for every task:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/lessons.md` and `docs/lessons.md`

Task 1, current branch, proof, and feasibility inventory:

- make no production or test edits
- read first: both complete target functions, every adjacent helper they call,
  all suppression rows named in Sections 1 and 2, and the exact tests in
  Section 10.2
- reproduce each live raw score and verify the exact McCabe contribution table
  in Section 9 against the current source

Task 2, owner retention disposition:

- make no source, test, registry, or policy-fixture edits unless the owner rejects
  the retention recommendation and a revised plan is approved
- read first: `run_monitored_subprocess`, `_cleanup_after_callback_failure`,
  `_stop_process_runtime`, `_kill_process_runtime`, `_start_stream_reader`,
  `_write_process_input`, `_drain_stream_queue`,
  `_drain_streams_until_closed`, `_last_monitor_metrics`, `_stop_monitor`, and
  `runner_diagnostics`
- read `RUFF-SUP-037`, `RUFF-SUP-311`, and `RUFF-SUP-350` before editing; those
  groups are fixed context, not collateral complexity budget
- read: `DockerProviderCLIRunner.__init__`, `run_with_hooks`,
  `_working_dir_mapping`, `_build_mounts`, `_container_executable`,
  `_container_env`, `_network_mode`, `_resolve_container_runtime`,
  `_docker_nano_cpus`, `_limit_int`, the Docker SDK loader/wait helper, and the
  provider preparation/result builders called by the target
- read `RUFF-SUP-203`, `RUFF-SUP-278`, `RUFF-SUP-279`, `RUFF-SUP-280`, and
  `RUFF-SUP-281` before editing. Mount normalization and every approved broad
  catch remain separate owned policy
- present the subprocess and Docker dispositions separately; the owner may
  retain one and request replanning for the other

Task 3, traceability-only reconciliation:

- modify this plan, `docs/plans/README.md`, and the Related Plans paragraphs in
  the six source specs named in the metadata block
- read first: `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1],
  `bin/ruff_suppression_index.py`, `tests/specs/test_ruff_policy.py`,
  `tests/specs/test_ruff_suppression_index.py`, and
  `tests/specs/test_plan_metadata.py`
- do not rewrite the generated index for the recommended zero-delta retention;
  run the checker in `--check` mode
- leave source directives, human registry rows, policy IDs/counts, raw inventory,
  and generated index byte-for-byte unchanged

Task 4, final hardening:

- modify only files needed to close a named gate or reviewer finding
- read first: the complete diff, the mechanism-evidence ledger, the Rework
  Queue, the Evidence Log, and the Deviation Log
- any new behavior or mechanism outside Section 9 requires renewed scope review

Comprehension questions before editing:

1. Why must a completed process beat an elapsed timeout at the subprocess wake
   boundary?
2. Which subprocess state must remain visible together to preserve stream,
   monitor, and terminal priority?
3. Why is a Docker runtime handle published only after the runtime-start probe,
   rather than immediately after `containers.create()` or `start()`?
4. Which Docker phases deliberately contain arbitrary callback/provider/cleanup
   exceptions, and why must those phase policies remain adjacent?
5. Why would a shared subprocess/container lifecycle abstraction be dishonest
   even though both functions return `RunnerOutcome`?

An implementer who cannot answer all five should not start the disposition.

## 5. Spec Baseline

Repository baseline at plan authoring:

- commit: `07c66fa29b5d3045b610ae0c0d04a11bdf202ab7`
- Ruff: `0.16.2`
- landing uses explicit file-list staging against this plan's owned delta

Raw complexity evidence:

```text
weft/core/runners/subprocess_runner.py:74:5: C901 `run_monitored_subprocess` is too complex (25 > 10)
extensions/weft_docker/weft_docker/agent_runner.py:105:9: C901 `run_with_hooks` is too complex (24 > 10)
```

Reproduce with:

```bash
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/runners/subprocess_runner.py \
  extensions/weft_docker/weft_docker/agent_runner.py
```

That command also reports `_start_stream_reader`, its nested `_reader`,
`_normalize_work_item_mounts`, and `_resolve_work_item_mounts`. Those symbols
have separate approved groups and are out of scope. Acceptance is by the two
named target symbols plus every new helper, not by requiring both files' raw
`C901` output to become empty.

At authoring, policy tests expect 232 groups, 375 directives, and 141 C901
directives. Other active efforts may change those totals before implementation.
This plan verifies the live values but applies no delta to either target group.

## 6. Spec And Traceability Strategy

Proposed Spec Delta: **none**. The current specs already define the intended
runner ownership, timeout, runtime-handle, provider, outcome, and cleanup
behavior. There is no promotion slice.

The recommended implementation is one zero-policy-delta disposition slice:
retain both approved source directives and all registry artifacts, record the
verified feasibility floors and owner decisions, and add traceability-only plan
backlinks. The backlinks record a completed design evaluation, not a behavior
change. No source candidate is created or restored because the feasibility gate
runs before source editing.

If the owner declines either retention recommendation, stop. Expand the exact
mechanism budget in a plan revision, state which existing locality judgment is
being reopened and why, and repeat clean plus outside-model review before source
work. Only such a revised plan may authorize a suppression-close slice.

If implementation changes precedence, timeout semantics, monitor cadence,
runtime-handle authority or fields, TaskSpec options, provider preparation,
outcome status/text, callback containment, diagnostics, or cleanup, stop.
Reclassify as Class 5, write an exact Proposed Spec Delta, obtain owner review,
and promote the spec before continuing.

Final traceability edits:

- add this plan to Related Plans in `docs/specifications/01-Core_Components.md`,
  `02-TaskSpec.md`, `06-Resource_Management.md`, `07-System_Invariants.md`,
  `08-Testing_Strategy.md`, and `13-Agent_Runtime.md`
- keep the [AR-9] implementation mapping unchanged because lifecycle ownership
  does not move in the retained-source disposition
- update source docstrings only if a private ownership seam materially moves;
  do not rewrite normative behavior for a behavior-preserving refactor

## 7. Current Architecture And Authority

The subprocess owner is one continuous observation loop:

```text
notify lifecycle callbacks
        |
start optional monitor
        |
start stdout/stderr readers and write stdin
        |
drain available stream chunks
        |
cancel? -> stop/reap/drain/metrics/monitor stop -> cancelled
        |
process complete and both streams sealed? -> finalization
        |
elapsed timeout and process still live? -> kill/drain/cleanup -> timeout
        |
monitor due while process live? -> check -> optional limit outcome
        |
bounded sleep -> next turn
        |
final drain/metrics/monitor stop -> error or ok outcome
```

The live locals are coupled for a real reason: two queue-closure flags, two
output accumulators, process liveness, monitor due time, last metrics, timeout,
and callbacks participate in the same turn. Passing them through a lifecycle
object merely moves that state; it does not create a cleaner authority.

The Docker owner is a sequence with one small poll loop:

```text
resolve and prepare provider/runtime/image/mounts/invocation
        |
build Docker create options
        |
create -> start -> wait until observable -> publish runtime handle
        |
notify start callbacks
        |
poll: cancel first, timeout second, otherwise wait for terminal state
        |
reload -> logs -> output callbacks -> provider result or terminal outcome
        |
OOM override
        |
finally remove container
```

This is not an unnamed state machine. The loop reads Docker's existing runtime
state and chooses among three current actions. Introducing local lifecycle
states or events would duplicate Docker state without solving an observed bug.

## 8. Non-Goals And Guardrails

- No state machine, reducer, transition table, lifecycle dataclass, mutable
  context/frame, protocol, event enum, or callback registry.
- No common base or helper shared by subprocess and Docker execution.
- No new retry, grace period, debounce, deadline, fallback, warning, diagnostic,
  cleanup phase, or process/container probe.
- No movement of `RUFF-SUP-037`, `RUFF-SUP-311`, `RUFF-SUP-350`, or
  `RUFF-SUP-203`.
- No movement or regrouping of `RUFF-SUP-278` through `RUFF-SUP-281` merely to
  lower `run_with_hooks` complexity. If those boundaries block the threshold,
  that is evidence to retain `RUFF-SUP-202`.
- No helper whose main purpose is one `if`, one return statement, or carrying a
  `noqa` to a different symbol.
- No helper tested by private call count or name. Tests stay on owner-observable
  outcomes and side effects.
- No fake that claims Docker SDK compatibility. SDK-facing type/argument proof
  uses the installed SDK; fake containers prove only Weft's owner branches.
- No exhaustive permutations of hypothetical runtime states. Cover the states
  the current code branches on and the precedence rules the specs name.
- No mapping-order claim where none exists. The current Docker environment
  merges are deliberately last-writer-wins in declared order:
  runtime requirements, prepared runtime, TaskSpec env, invocation env. Preserve
  and do not add a new order test when source is unchanged.
- No change to unrelated complexity groups in either file.

## 9. Pre-Source Feasibility Gate And Mechanism Budget

### 9.1 McCabe decision accounting

Run this gate before any source or test edit. The current approved-locality
budget has a hard lower bound above Ruff's threshold:

| Target | Raw score | Defensible decisions that could move | Maximum reduction | Best possible target score | Close feasible? |
|---|---:|---|---:|---:|---|
| `run_monitored_subprocess` | 25 | wait budget: timeout/process/monitor (3); completed mapping: missing/nonzero (2) | 5 | 20 | no |
| `DockerProviderCLIRunner.run_with_hooks` | 24 | create options: network/memory/CPU/fd (4); poll: loop/status/cancel/timeout (4); OOM mapping: OOM/memory-message (2) | 10 | 14 | no |

A clean reviewer independently verified both floors with Ruff against in-memory
versions that removed every counted decision. No repository file was written.
The implementer must reproduce the live raw scores and inspect that these exact
decision regions are unchanged. If they are unchanged, the table is sufficient
to route directly to retention. Do not implement the helpers to prove arithmetic
already established by the source and independent review.

If the live code changed enough that either floor is 10 or lower, stop and
revise this plan with the new exact seam, branch-to-proof matrix, and review.
Do not infer authorization from a lower score alone.

### 9.2 Why the remaining decisions stay local

Subprocess would still need ten more decisions removed after every defensible
pure/static seam. The remaining decisions select cancellation, timeout, limit,
stream-drain, monitor-cleanup, and callback behavior from shared live locals.
Extracting them requires a lifecycle frame, a wide state-threading helper, or a
second control model. No observed defect or duplication justifies that cost.

Docker would still need four more decisions removed. Those decisions live in
the already-approved start callback, output callback, provider-result, and
`finally` cleanup boundaries. `RUFF-SUP-278` through `RUFF-SUP-281` record clean
reviews that favored invocation/catch/warning and cleanup adjacency. Moving
them merely to reduce the enclosing score would reopen approved locality
without a correctness problem.

### 9.3 Mechanism-evidence ledger

| Considered mechanism | Current evidence | Needed now? | Disposition |
|---|---|---|---|
| pure subprocess wait-budget helper | one current scalar calculation; no readability defect | no; insufficient by 15 score points | reject for this effort |
| completed subprocess outcome builder | three visible terminal mappings; no duplication outside owner | no; insufficient with wait helper | reject for this effort |
| subprocess lifecycle frame or terminal helper | no state-passing defect; would carry queues, flags, monitor, process, and callbacks | no | prohibit |
| new subprocess reducer/state machine | current real loop already owns priority | no | prohibit |
| Docker create-options method | one SDK call's conditional mapping | no; insufficient with every other safe seam | reject for this effort |
| Docker terminal-poll method | one small existing loop | no missing transition or reuse | no; insufficient | reject for this effort |
| one-branch Docker OOM helper | only the current `if oom_killed` block | no; pure score move | prohibit |
| moved approved broad-catch phase | clean reviews favor current adjacency | no | prohibit |
| Docker lifecycle frame or event model | no missing transition or state owner | no | prohibit |
| shared cross-runner lifecycle helper | only outcome type is shared; control and cleanup differ | no | prohibit |
| new tests or generalized runtime fake | source behavior remains unchanged and current owner proof is adequate | no | prohibit |
| retry, extra timeout, fallback, warning, or cleanup | no reproduced defect | no | prohibit |

The plan reviewer and any future revision must enumerate every proposed
mechanism against this table. An empty baseline-evidence cell blocks source
work. “Future-proof,” “defensive,” “more robust,” and “another runner may need
it” are not evidence.

### 9.4 Complexity is a constraint, not the design oracle

The correct response to an infeasible, locality-preserving reduction is the
approved [TS-3] suppression, not a family of single-use helpers. Retention here
is a design disposition, not a waiver of correctness proof.

## 10. Testing Plan

### 10.1 Test policy

Run the existing owner tests to confirm that the retained source still has its
registered proof. Add no tests because this plan moves no production branch.

Tests assert owner-observable status, error, output, return code, metrics,
runtime handle, callback sequence where declared, stop/kill/remove effects, and
primary-exception preservation. They do not assert helper names, helper call
counts, context-object fields, exact poll counts, or incidental dictionary
iteration. Time branches use the current fake clock or deterministic fake
container state; do not add sleeps for test coordination.

The defensive subprocess `returncode is None` branch after a completed
process-and-sealed-stream break is not reproducible through a faithful real
`Popen` owner path. Do not build an inconsistent fake process solely to fire it.
Preserve the mapping by source comparison plus the existing real zero/nonzero
owner tests. If implementation claims that branch is
reachable, first reproduce the real path and revise this evidence decision.

### 10.2 Exact branch-to-proof matrix

Parameterized nodes run in full.

| Current subprocess branch or invariant | Exact proof |
|---|---|
| process completion beats elapsed timeout | `tests/core/test_subprocess_runner.py::test_completed_process_at_timeout_wake_boundary_returns_ok` |
| live stdout/stderr chunks arrive before exit and final chunks seal | `tests/tasks/test_runner.py::test_run_monitored_subprocess_emits_live_chunks_before_exit` |
| no late monitor limit after authoritative process exit | `tests/tasks/test_runner.py::test_run_monitored_subprocess_ignores_late_limit_after_process_exit` |
| supplied monitor starts, stops, and contributes metrics | `tests/tasks/test_runner.py::test_run_monitored_subprocess_uses_supplied_monitor` |
| ordinary callbacks warn without replacing success | `tests/core/test_subprocess_runner.py::test_start_callback_failures_are_logged_without_replacing_outcome` |
| fatal callback identity and every cleanup escalation | `test_start_callback_propagates_non_exception_failure_identity` plus the five `test_fatal_callback_cleanup_*` nodes in `tests/core/test_subprocess_runner.py` |
| monitor startup, poll, final snapshot/stop, cancellation, timeout, and limit failure policies | all six `test_monitor_*` nodes in `tests/core/test_subprocess_runner.py` |
| nonzero result mapping and stderr text | `tests/tasks/test_runner.py::test_task_runner_reports_command_failure` |
| real timeout kills descendants | `tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants` |
| ordinary success and immediate stdout/stderr collection | `test_task_runner_executes_command_successfully` and `test_task_runner_collects_immediate_command_stdout_and_stderr_tail` in `tests/tasks/test_runner.py` |

| Current Docker branch or invariant | Exact existing proof |
|---|---|
| provider/runtime/image preparation, cached image, success result, captured logs, callbacks, parse/build failure, primary start failure, cleanup warning | existing `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image` |
| cancellation kills the container and returns cancelled | existing `test_agent_runner_reports_cancel_requested_as_cancelled` |

No new characterization or mutation test is required for a zero-source-delta
retention. If the owner requests a wider mechanism budget, the revised plan must
add exact proof for every moved current branch, including a same-turn Docker
cancel-and-timeout precedence case if terminal polling moves.

## 11. Task Breakdown

### Task 1: Reconfirm live baseline and feasibility floors

Classification: Class 4 support task.

1. Re-read the exact source, spec, registry, and lesson owners in Sections 3-4.
2. Run the baseline commands in Section 17 and record raw scores.
3. Verify the exact McCabe contribution table in Section 9 against the live
   target code. Other work may have landed after authoring.
4. Run the existing branch-to-proof matrix. Add no source or test code.
5. If either live floor is 10 or lower, stop for plan revision rather than
   beginning a source candidate.

Exit gate: independently reproduced live scores and decision floors, with zero
source/test delta.

### Task 2: Obtain separate owner dispositions

Classification: Class 4 design disposition.

1. Present the subprocess evidence: score 25, defensible floor 20, and the
   additional prohibited state-threading mechanisms a close would require.
2. Present the Docker evidence separately: score 24, defensible floor 14, and
   the already-approved broad-boundary locality judgments a close would reopen.
3. Request retain or replan for each group. Do not treat approval of one as
   approval of the other.
4. On retain, record the decision and leave source, tests, and suppression
   artifacts unchanged.
5. On replan, stop this implementation. Revise the mechanism budget, proof
   matrix, and collateral suppression scope, then repeat clean and outside-model
   plan reviews before source work.

Exit gate: explicit owner disposition for both `RUFF-SUP-036` and
`RUFF-SUP-202`.

### Task 3: Traceability-only reconciliation

Classification: Class 3 policy/traceability update within the Class 4 plan.

1. Confirm source, tests, human registry, policy IDs/counts, raw inventory, and
   generated index have zero delta for both retained groups.
2. Add this plan to the Related Plans sections of all governing specs.
3. Keep implementation mappings unchanged because ownership did not move.
4. Mark this plan completed and update its README index row only after both
   owner dispositions are recorded.

Exit gate: source, human registry, policy fixture, raw inventory, generated
index, plan status, spec backlinks, and README index agree byte-for-byte with
the selected dispositions.

### Task 4: Final hardening and completion review

Classification: Class 4 closure.

1. Run all focused and repository gates in Section 17.
2. Obtain one final clean review of the complete documentation-only diff, both
   dispositions, mechanism ledger, proof fidelity, and zero suppression delta.
3. Resolve every Rework Queue and Deviation Log row.
4. Mark the plan completed and update the index only after implementation,
   reviews, and gates are complete.
5. Obtain owner authorization before committing. If authorization is not given,
   report the finished uncommitted file set instead of calling it landed.

## 12. Existing Failure And Cleanup Proof

The retention changes no failure policy or source boundary. The focused baseline
must still prove the registered priorities where direct tests already exist;
unchanged uncovered branches are checked by source comparison:

- subprocess ordinary callback failure does not replace execution; fatal
  callback identity survives guarded cleanup
- monitor failures do not replace cancel, timeout, limit, or completion
- process completion at the timeout wake boundary wins without kill
- cancellation and timeout stop/kill then drain output and stop monitoring
- Docker provider parse/build failure becomes the current error outcome
- container start failure remains primary when removal also fails
- Docker callback failures keep their fixed warnings and do not replace output
- Docker cancellation kill/outcome remains directly tested; timeout-before-log
  ordering remains unchanged by source comparison
- container removal remains in `finally` and runs after every post-create path

No new failure mode or probe is invented for this plan. A future revised source
candidate must identify the exact current operation and behavior it could change
before adding proof.

## 13. Observability, Privacy, And Ordering

This plan preserves current diagnostics. It does not claim all errors are
private, bounded, or single-line:

- subprocess nonzero-exit errors include current stderr text
- Docker provider parse/result errors use `str(exc)` verbatim and may expose
  provider detail or fail during stringification
- callback and cleanup warnings governed by the retained BLE groups are fixed
  and privacy-safe under their existing tests

Order is observable where explicitly declared: environment mappings are merged
last-writer-wins in their current declared order; container lifecycle is
create/start/wait/publish; cancellation is checked before timeout in the Docker
poll turn; subprocess completion is rechecked before timeout; output collection
precedes terminal result mapping; container removal is final cleanup. No test
should depend on incidental mapping key iteration, unordered callback collection,
or fake-container implementation order beyond those declared sequences.

## 14. Suppression Reconciliation

The recommended and only currently authorized delta is zero:

| Disposition | Group delta | Directive delta | Raw inventory delta |
|---|---:|---:|---:|
| retain `RUFF-SUP-036` | 0 | 0 | 0 |
| retain `RUFF-SUP-202` | 0 | 0 | 0 |
| both retained | 0 | 0 | 0 |

Verify the zero delta:

```bash
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_ruff_policy.py tests/specs/test_ruff_suppression_index.py
./.venv/bin/ruff check --extend-select RUF100 \
  weft/core/runners/subprocess_runner.py \
  extensions/weft_docker/weft_docker/agent_runner.py \
  tests/specs/test_ruff_policy.py
```

Recompute live counts only to verify that concurrent work is internally
consistent. Do not edit them for this plan. The source directives, human rows,
expected IDs/counts, raw inventory, and generated index remain exact and
unchanged for both groups.

Fixed collateral groups:

- `RUFF-SUP-037`: 2 C901 directives at stream-decoder owners
- `RUFF-SUP-311`: 5 BLE001 directives at subprocess monitor owners
- `RUFF-SUP-350`: 2 BLE001 directives at subprocess lifecycle callbacks
- `RUFF-SUP-203`: 2 C901 directives at Docker work-item mount owners
- `RUFF-SUP-278`: 2 BLE001 directives at Docker start callbacks
- `RUFF-SUP-279`: 2 BLE001 directives at Docker output callbacks
- `RUFF-SUP-280`: 1 BLE001 directive at provider parse/result construction
- `RUFF-SUP-281`: 1 BLE001 directive at final container removal

Any proposed movement or count change in these groups is evidence for plan
revision, not an authorized cleanup.

## 15. Review Loop And Rework Queue

### 15.1 Draft-plan review

Before implementation, obtain:

1. a clean subagent review focused on zero-context executability, logical
   locality, brittle tests, and invented mechanisms
2. a fresh outside-model review from a different model family focused on
   whether the plan overfits Ruff, builds around hypothetical problems, or
   fails to protect current process/container behavior

Both reviewers read this complete plan, both targets, the named specs,
suppression rows, lessons, and exact tests. Findings are reconciled and reviews
rerun until NET POSITIVE/PASS.

The clean subagent must explicitly answer:

- Does the plan invent a state machine, lifecycle frame, protocol, or helper
  family for an issue that has not actually occurred?
- Does any proposed test armor against a hypothetical state rather than a
  current branch or spec rule?
- Does the feasibility gate correctly reject otherwise plausible helpers that
  cannot meet the close gate without further score-driven mechanisms?
- Is retention honestly available if the threshold conflicts with locality?

Required mechanism audit:

| Proposed mechanism | Baseline evidence | Needed now? | Simpler current alternative | Verdict |
|---|---|---|---|---|

An empty baseline-evidence cell is blocking.

### 15.2 Source-refactor review rule

This plan authorizes no source refactor, so the per-refactor review gate does
not fire. If an owner requests a wider mechanism budget, the plan must be
revised and independently approved first. That revision must preserve the
standing rule: after each cohesive source refactor, a fresh Python expert
compares baseline and candidate for logical locality and comprehensibility;
the first NET NEGATIVE candidate enters a bounded Rework Queue rather than
being reverted immediately.

### 15.3 Rework Queue

| Candidate | Review verdict | Locality/comprehensibility defect | Invented mechanism, if any | Bounded rework | Status |
|---|---|---|---|---|---|

The queue is expected to remain empty because the feasibility gate precedes
source work. Any row means source work began without the required plan revision
and blocks completion.

## 16. Rollout, Rollback, And One-Way Doors

Rollout is two independent owner decisions followed by one traceability-only
documentation slice. The subprocess result is not authority for Docker.

Rollback is a documentation-unit revert of the plan backlinks, status, and
index row. There is no source, test, suppression, persistence, queue, protocol,
data, configuration, or public migration.

The only semantic one-way doors are explicitly out of scope: runtime-handle
schema, timeout precedence, TaskSpec options, provider behavior, and cleanup
policy. Encountering one stops implementation for Class 5 planning.

## 17. Verification Commands

Authoring baseline:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q -n 0 \
  tests/core/test_subprocess_runner.py \
  tests/tasks/test_runner.py::test_run_monitored_subprocess_emits_live_chunks_before_exit \
  tests/tasks/test_runner.py::test_run_monitored_subprocess_ignores_late_limit_after_process_exit \
  extensions/weft_docker/tests/test_agent_runner.py \
  extensions/weft_docker/tests/test_container_runtime_resolution.py
```

Live feasibility and collateral-suppression check:

```bash
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/runners/subprocess_runner.py \
  extensions/weft_docker/weft_docker/agent_runner.py
./.venv/bin/ruff check --extend-select RUF100 \
  weft/core/runners/subprocess_runner.py \
  extensions/weft_docker/weft_docker/agent_runner.py \
  tests/specs/test_ruff_policy.py
```

Raw C901 must reproduce target scores 25 and 24 unless a concurrent source
change triggers plan revision. The four other findings remain separately owned.

Focused policy and documentation gates:

```bash
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
git diff --check
```

Final repository gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
uv lock --check
git diff --check
```

`bin/check-doc-paths` may reproduce only a separately established baseline of
unrelated findings. Compare keyed findings, not only the aggregate count.

## 18. Completion Criteria

The plan is complete only when:

- both targets have explicit owner-approved retention dispositions, or this
  plan has stopped for a separately reviewed mechanism-budget revision
- both retained groups and every collateral group remain exact with zero policy
  delta
- current subprocess and Docker behavior is unchanged
- no source refactor or new test was introduced after the infeasible close gate
- each retained target records its score, defensible floor, locality constraint,
  and owner decision
- the final reviewer finds no mechanism without current evidence and no test
  that protects only a hypothetical issue
- the Rework Queue is empty or fully resolved
- every Deviation Log row is closed
- traceability and all final gates pass
- owner authorized the documentation commit, or the final handoff explicitly
  reports the complete uncommitted file set

## 19. Evidence Log

| Date | Evidence | Result |
|---|---|---|
| 2026-08-08 | Raw Ruff C901 scan | Confirmed subprocess score 25 and Docker score 24; confirmed four other file findings have separate groups. |
| 2026-08-08 | Focused owner baseline, serial | 34 tests passed across the exact subprocess, runner, Docker-agent, and container-runtime nodes named in Section 17. |
| 2026-08-08 | Source/spec/history audit | Confirmed no missing state model: subprocess already has one owner loop and mature helpers; Docker has one small runtime-status poll loop. |
| 2026-08-08 | Suppression policy checker | PASS at the authoring baseline. |
| 2026-08-08 | Clean draft-plan review, round 1 | BLOCKED: the authorized seams could only reduce subprocess 25→20 and Docker 24→14, so the original bounded experiments mandated foreknown churn; also corrected conditional mutation proof, monitor-test count, characterization wording, and commit authorization. |
| 2026-08-08 | Clean draft-plan re-review | NET POSITIVE after replacing source experiments with a pre-source feasibility gate, separate owner-retention decisions, zero test/source/policy delta, and traceability-only closure. The mechanism audit found no invented state model, frame, protocol, helper family, or generalized fake. |
| 2026-08-08 | Claude outside-model review | PASS. Independently counted both live McCabe scores and defensible floors, found no locality-preserving route to 10, and confirmed direct retention plus zero new mechanisms/tests. Corrected the cleanup-lesson date from 2026-04-14 to 2026-04-20. |
| 2026-08-10 | Live closeout feasibility and source comparison | Reproduced `run_monitored_subprocess=25` with a defensible floor of 20 and Docker `run_with_hooks=24` with a defensible floor of 14. Both target source files and their dedicated owner tests remain byte-identical to baseline `07c66fa29b5d3045b610ae0c0d04a11bdf202ab7`; no source mechanism or new effort-specific test was introduced. |
| 2026-08-10 | Owner disposition: `RUFF-SUP-036` | `RETAIN`. The owner declared all five efforts implemented and authorized review, closure, and commit. The permitted subprocess seams cannot reach 10 without state-threading or further score-driven extraction, so the existing owner-local suppression remains the more comprehensible result. |
| 2026-08-10 | Owner disposition: `RUFF-SUP-202` | `RETAIN`. The same owner instruction separately closes the Docker disposition. The permitted lifecycle seams bottom out at 14; moving approved callback/provider/cleanup boundaries would reduce locality, so the existing suppression remains. |
| 2026-08-10 | Focused owner and suppression verification | 34 focused subprocess/Docker owner cases passed. The suppression checker, 84 policy tests, and scoped `RUF100` passed. All target and collateral rows and directives are unchanged. Effort 3 introduced no raw-inventory, generated-index, or policy-count delta; the concurrent live inventory is internally consistent at 230 groups, 373 directives, and 139 C901 directives. |
| 2026-08-10 | Traceability-only reconciliation | Added this plan to Related Plans in specifications 01, 02, 06, 07, 08, and 13. Implementation mappings remain unchanged because no code ownership moved. |
| 2026-08-10 | Final clean documentation review | NET POSITIVE. The reviewer verified the separate owner-approved retention dispositions, exact feasibility floors, zero effort-specific source/test/suppression delta, all six backlinks, and absence of invented mechanisms or hypothetical tests. Plan metadata/spec hygiene and diff checks passed. |

Closeout recorded the reproduced floors, separate owner dispositions, zero
effort-specific suppression delta, traceability, and final review. No source or
test mechanism was introduced. Do not record transient worktree state here.

## 20. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

Every row must be closed before completion. Any behavior or contract deviation
requires Class 5 replanning and an exact spec proposal; it cannot be closed by a
code comment or test alone.
