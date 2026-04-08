# Task Lifecycle Stop/Drain Audit Plan

## Goal

Audit the load-bearing task lifecycle and shutdown paths that have been failing
under recent stress, with special attention to `STOP`, `KILL`, manager drain,
manager reuse, preserve-cleanup, and broker-backed liveness/state inference.
This is an audit plan, not an implementation plan. The immediate output is a
high-confidence findings document that separates real product bugs from test
artifacts, names the true invariants, and reduces the risk of another broad,
incorrect patch series.

The audit must explain:

- which failures are real bugs,
- which failures are test harness or test-design issues,
- which failures are old latent bugs exposed by new stress,
- which failures are regressions introduced by recent fixes,
- and which system assumptions are actually safe versus merely convenient.

Do not treat this audit as an excuse to redesign unrelated parts of Weft,
runner plugins, or the CLI surface. The purpose is to understand the current
system correctly enough that the next fix plan can be narrow and correct.

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2], [CC-3]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1.1], [TS-1.2], [TS-1.3]
- [`docs/specifications/03-Worker_Architecture.md`](../specifications/03-Worker_Architecture.md) [WA-1], [WA-3], [WA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-3], [MF-5], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [STATE.1], [STATE.2], [QUEUE.4], [QUEUE.6], [WORKER.3], [WORKER.7]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1], [CLI-1.2], [CLI-1.3]

Existing plans and current context:

- [`docs/plans/active-control-main-thread-plan.md`](./active-control-main-thread-plan.md)
- [`docs/plans/runner-extension-point-plan.md`](./runner-extension-point-plan.md)
- [`docs/plans/release-helper-plan.md`](./release-helper-plan.md)
- [`docs/lessons.md`](../lessons.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Relevant external code to read, not rewrite:

- [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)
- [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)

Source spec note:

- There is no single specification today that fully defines preserve-cleanup
  semantics in the test harness. That is part of why the audit is needed.

## Fixed Audit Outputs

Use fixed sibling files so the audit does not sprawl across ad hoc notes:

- Audit log:
  [`docs/plans/task-lifecycle-stop-drain-audit-log.md`](./task-lifecycle-stop-drain-audit-log.md)
- Findings document:
  [`docs/plans/task-lifecycle-stop-drain-findings.md`](./task-lifecycle-stop-drain-findings.md)

Rules:

- Do not append experiment notes to this plan once the audit starts.
- Use the audit log for commands, observations, and disproved theories.
- Use the findings document for severity-ordered conclusions and next-fix
  recommendations.
- If either file is missing when the audit starts, create it before running the
  first repro.

## Audit Priority and Budget

Treat this as a prioritized audit, not an unlimited investigation.

Core first-pass order:

1. Tasks 1 through 6 are the mandatory first-pass audit.
2. Task 7 is required only if current evidence still suggests platform-specific
   behavior rather than a host-path logic bug.
3. Task 8 is lower priority than Tasks 3 through 6 and should happen only after
   the core lifecycle/harness findings are stable enough that CI gating is the
   remaining uncertainty.
4. Task 9 must still land even if Tasks 7 or 8 are deferred.

Time-budget rule:

- Do not spend more than one focused audit pass on optional or runner-neutral
  questions before a written findings draft exists.
- If time or context runs short, stop after Tasks 1 through 6 and write a
  partial findings draft that explicitly lists deferred work.
- Optional follow-up work is never a substitute for writing down the host/harness
  findings already proven.

## Symptom Inventory

The audit must start from concrete symptoms, not theories.

### Confirmed Symptom Classes

1. Task lifecycle hangs or timeouts.
   - CLI commands that should return after task completion instead time out.
   - Examples previously seen in CI:
     - [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
     - [`tests/cli/test_cli_result_all.py`](../../tests/cli/test_cli_result_all.py)
     - [`tests/cli/test_cli_list_task.py`](../../tests/cli/test_cli_list_task.py)

2. Runtime disappears before terminal durable state lands.
   - Snapshot or liveness surfaces still show `running`, but runtime state is
     already `missing`.
   - This was previously visible in host stop/kill parity tests and some task
     command tests.

3. SQLite broker integrity failures during or after cleanup.
   - `PRAGMA integrity_check` returned failures such as:
     - `wrong # of entries in index idx_messages_ts_unique`
     - `Rowid ... out of order`
   - This is a real corruption signal, not merely a flaky timeout.

4. Preserve-cleanup failures in the test harness.
   - Cleanup with `preserve_database=True` either:
     - reports supposedly live tasks forever,
     - or returns while the database is still malformed,
     - or both.

5. Windows and cross-platform stop/kill instability.
   - Prior CI failures included hangs in `psutil`-backed wait paths and
     different behavior across Linux, macOS, and Windows.

### Specific Test Names Seen Failing

These names should be treated as symptom probes, not as isolated one-off bugs:

- [`tests/tasks/test_command_runner_parity.py`](../../tests/tasks/test_command_runner_parity.py)
  - `test_command_runners_stop_cancel_equivalently[host]`
  - `test_command_runners_kill_mark_killed_equivalently[host]`
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
  - `test_cli_run_parallel_no_wait_adopts_active_manager`
  - `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
  - `test_pipeline_run_sequential`
- [`tests/cli/test_cli_result_all.py`](../../tests/cli/test_cli_result_all.py)
  - `test_result_all_peek_preserves_messages`
  - `test_result_all_json_output`
- [`tests/cli/test_cli_list_task.py`](../../tests/cli/test_cli_list_task.py)
  - `test_list_and_task_status`
- [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)

### What Has Already Been Observed Locally

These are not assumptions; they are prior observations from the current
debugging pass and should be treated as context for the audit:

- The failures cluster around shutdown/control/cleanup rather than runner
  extensibility itself.
- The same failure family is easier to reproduce under:
  - manager reuse,
  - parallel `--no-wait` launches,
  - repeated preserve-cleanup loops,
  - and full-suite or release-gate runs.
- Some earlier “live task” reports were false positives caused by stale
  `tid_mappings`, PID reuse, or manager tids being misclassified as ordinary
  tasks.
- Some earlier preserve-cleanup corruption correlated with reading the worker
  registry while managers were still updating or pruning it during shutdown.

## Stressors and Why They Expose These Bugs Now

The audit must not hand-wave “CI flakiness.” Name the stressors explicitly.

### New or Stronger Stressors

1. `WEFT_MANAGER_REUSE_ENABLED=1`
   - This creates real manager-election, leadership-yield, and drain behavior.
   - Older tests often exercised only single-manager startup paths.

2. Parallel `weft run --no-wait`
   - Parallel launch causes multiple near-simultaneous managers, more process
     churn, and more `tid_mappings` churn.

3. Repeated preserve-cleanup loops
   - The same path exercised 20 times is much better at surfacing stale PID
     reuse and cleanup races than one-shot tests.

4. Release gate / full-suite load
   - CI and release runs create more process churn, more broker history, and
     more realistic race windows than targeted developer runs.

5. Durable runtime metadata
   - With append-only `weft.state.tid_mappings` and runner/runtime metadata,
     old assumptions like “live PID means live task” become unsafe.

### The Audit Must Explicitly Answer

- Which failures are caused by new stress exposing old bugs?
- Which failures are new regressions from recent fixes?
- Which failures only appear under a preserve-cleanup contract?
- Which failures are cross-platform differences versus logic bugs?

## Working Assumptions and Known Facts

The audit should use these as working assumptions unless disproved.

1. `weft.log.tasks` is the durable public truth for terminal task state.
   - If a task has no terminal event there, it is not durably done, even if its
     runtime has exited.

2. `weft.state.tid_mappings` is runtime metadata, not durable completion truth.
   - It is append-only and can contain stale runtime information.

3. PID liveness alone is not enough to infer task liveness.
   - PID reuse is real.
   - Manager tids, task tids, and worker tids must be distinguished.

4. Preserve-cleanup is a different contract from destructive cleanup.
   - `preserve_database=True` must not quietly reuse forceful teardown behavior
     just because it is convenient.

5. WeftTestHarness is part of the production-facing test surface.
   - If the harness infers liveness incorrectly or tears down unsafely, that is
     a real testing infrastructure bug and can invalidate conclusions.

6. Real broker-backed tests are preferred over mocks.
   - If the audit cannot explain behavior on the real path, it is incomplete.

7. Liveness truth and outcome truth are different contracts.
   - Liveness truth answers: “is the runtime still alive right now?”
   - Outcome truth answers: “has Weft durably published the terminal state?”
   - The audit must not collapse these into one concept.

8. Runner-native liveness sources may be better than host-PID inference.
   - For Docker, the Docker daemon / SDK is a better liveness authority than
     best-effort host PID inference.
   - For a future macOS sandbox runner, the right liveness authority may be a
     runner-owned handle or platform API rather than generic pid-tree logic.
   - The audit should treat this as a serious design alternative, not a side
     note.

## Theories, Confirmed and Disproved

The audit should begin with these hypotheses so the next developer does not
repeat dead ends.

### Confirmed or Strongly Supported

1. This is not one isolated test failure.
   - The failures cluster around lifecycle ownership, drain, and cleanup.

2. Some failures are real product bugs, not just bad tests.
   - SQLite integrity-check failures prove that at least part of the failure
     family is real.

3. Some failures are harness bugs.
   - Preserve-cleanup logic and live-task inference can be wrong independently
     of core task execution.

4. `STOP` is a stressor, not the whole root cause.
   - The deeper class is lifecycle ownership plus liveness inference.

### Disproved or Partially Disproved

1. “This is mainly a Docker or sandbox runner problem.”
   - Disproved. The main failures are in the host runner and core lifecycle.

2. “The runner extension point itself is the bug.”
   - Disproved as the primary explanation. The extension work exposed some
     assumptions, but the failure family is in host task/control/cleanup paths.

3. “All live-task reports reflect real live user tasks.”
   - Disproved. Some were manager tids or stale mappings.

4. “All preserve-cleanup failures are just flakiness.”
   - Disproved. Real DB integrity failures occurred.

5. “The corruption is definitely in the core broker or only in SimpleBroker.”
   - Not established. Some corruption correlated with how Weft used broker
     surfaces during shutdown, especially registry reads/writes.

### Still Unproven and Must Be Audited Carefully

1. Whether the current active-control path in
   [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py) remains a
   primary cause of current failures.
   - Audit the main-thread polling path in `_poll_active_control_once()` and
     deferred STOP/KILL finalization in `_finalize_deferred_active_control()`;
     do not assume a background broker poller still exists.

2. Whether preserve-cleanup should ever send task-level `STOP`, or should only
   signal managers and wait.

3. Which remaining full-suite or release-gate failures are product bugs versus
   audit-surface or harness issues.

4. Whether the current host-process-centric liveness contract is the wrong
   abstraction boundary for non-host runners and even for some host cleanup
   paths.

## Context and Key Files

### Files to Read First

- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/runners/host.py`](../../weft/core/runners/host.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
- [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
- [`weft/commands/worker.py`](../../weft/commands/worker.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/status.py`](../../weft/commands/status.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/tasks/test_command_runner_parity.py`](../../tests/tasks/test_command_runner_parity.py)
- [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)

### Files That May Need Audit-Only Changes

Prefer read-only auditing first. Only modify code during the audit if one of
these is necessary:

- a deterministic repro is missing and must be added,
- a diagnostic assertion or trace is required to disprove a theory,
- or the findings document requires a minimal permanent regression test.

If you do need changes during the audit, prefer:

- `tests/helpers/weft_harness.py`
- `tests/test_harness_registration.py`
- a targeted failing test file in `tests/cli/`, `tests/tasks/`, or
  `tests/commands/`

Do not patch core production code as part of the audit unless the audit has
already produced a written finding that names the bug and the smallest safe
fix shape. If you cross that line, stop and convert the work into a separate
implementation plan.

### Style and Guidance

- Follow [`AGENTS.md`](../../AGENTS.md) exactly.
- Use existing helpers; do not invent new abstractions “for the audit.”
- Prefer fast local search, `pytest -q -n 0`, and real broker-backed tests.
- Use `apply_patch` for edits.
- Do not add new dependencies.

### Shared Helpers and Canonical Paths

- `tests/helpers/weft_harness.py` is the canonical broker/process harness.
- `tests/conftest.py::_register_cli_outputs(...)` is the canonical place where
  CLI output is harvested into harness tracking.
- `weft/core/tasks/base.py::_build_tid_mapping_payload()` is the canonical
  `tid_mappings` writer.
- `weft/core/manager.py` owns manager lifecycle, child drain, and registry
  publication.
- `weft/commands/tasks.py` owns CLI stop/kill fallback behavior.
- `weft/commands/worker.py` owns worker registry lookup and stop behavior.

### Current Structure Summary

- `Consumer` owns task execution and terminal state publication.
- `BaseTask` owns queue/control plumbing and `tid_mappings`.
- `Manager` is itself a task that spawns child tasks and publishes worker
  registry records.
- The test harness tracks tids and pids by scraping CLI output and queue state.
- `preserve_database=True` cleanup is trying to leave the DB intact while still
  ensuring processes are gone.

### Comprehension Questions

Before auditing, the implementer must be able to answer:

1. Which queue/log proves a task has durably completed?
   - Expected answer: `weft.log.tasks`, not `tid_mappings`.

2. Which file currently owns manager drain and leadership-yield logic?
   - Expected answer: `weft/core/manager.py`.

3. Which file currently infers test-time liveness from tids/pids?
   - Expected answer: `tests/helpers/weft_harness.py`.

4. Why is PID liveness alone insufficient?
   - Expected answer: append-only mappings plus PID reuse plus manager/task role
     confusion.

These are self-check gates, not optional trivia.

- If you cannot answer one from the cited code/specs, stop and finish the
  required reading before running repros.
- Do not start patching tests or code until these answers are clear enough to
  write down in the audit log.

## Invariants and Constraints

- TID format and immutability must remain intact.
- State transitions must remain forward-only.
- Reserved queue policy must remain correct.
- `spec` and `io` remain immutable after TaskSpec creation.
- The audit must not create a second durable execution path.
- Do not silently redefine terminal state to mean “runtime missing.”
- Do not treat manager tids and task tids as interchangeable.
- Do not replace real broker-backed proofs with mocks.
- Do not broaden the runner plugin contract as part of the audit.
- Do not change the public CLI surface during the audit.
- No new dependency.
- No drive-by refactor.
- No “cleanup redesign” outside the specific preserve/stop lifecycle under
  audit.

## Hidden Couplings That Must Be Audited

1. CLI stop/kill behavior is coupled to `tid_mappings`, runtime handles, and
   task-log timing.

2. Manager shutdown is coupled to:
   - child drain,
   - leadership-yield behavior,
   - registry publication and pruning,
   - and broker-visible liveness.

3. Preserve-cleanup is coupled to:
   - harness-registered tids/pids,
   - append-only `tid_mappings`,
   - worker registry reads,
   - and task-log terminal events.

4. Full-suite and release-gate behavior are coupled to:
   - xdist worker churn,
   - PID reuse,
   - manager reuse,
   - and long append-only histories.

5. Current host-centric liveness assumptions may leak into runner-neutral code.
   - A runner-native control plane may be the right place to answer
     “is this runtime alive?” for Docker and future non-host sandboxes.
   - The audit must identify where core or harness code still assumes that
     liveness can be inferred from host PIDs alone.

## Tasks

Core audit tasks are numbered `1` through `9` below.

Optional follow-up tasks, if needed, are listed later under
`Optional Follow-Up Tasks` and are not part of that core count.

### 1. Build a Symptom Matrix Before Touching Code

- Outcome:
  A written matrix exists showing each known failure, where it was seen, what
  it means, and which code surface it implicates.
- Files to modify:
  - `docs/plans/task-lifecycle-stop-drain-audit-log.md`
- Read first:
  - the failing test files named above
  - `docs/lessons.md`
- Required work:
  - List each known failing test or symptom.
  - Note whether it is:
    - timeout/hang,
    - runtime-missing-before-terminal-state,
    - DB corruption,
    - harness preserve-cleanup failure,
    - or cross-platform stop/kill instability.
  - Map each symptom to likely ownership layers:
    - harness,
    - CLI,
    - manager,
    - consumer/base,
    - runner/session,
    - or broker usage.
- Stop if:
  - you are about to patch code before the symptom matrix exists.
- Done when:
  - another engineer can read the matrix and see the failure family clearly.

### 2. Lock Real Repros and Separate Them From Flake Candidates

- Outcome:
  The audit has a minimal repro set that distinguishes real bugs from weak
  probes.
- Files to touch:
  - only tests if a repro must be stabilized
- Read first:
  - `tests/cli/test_cli_run.py`
  - `tests/tasks/test_command_runner_parity.py`
  - `tests/commands/test_task_commands.py`
  - `tests/helpers/weft_harness.py`
- Required work:
  - Keep the following as primary repros unless you can prove a better one:
    - `tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
    - host stop/kill parity tests
    - the CLI timeout/status tests named in the symptom inventory
  - Prefer repeated local runs with `-n 0`.
  - If a repro is too broad, shrink it without changing what it proves.
- Do not mock:
  - `WeftTestHarness`
  - broker queues
  - manager lifecycle
  - `run_cli(...)`
- Stop if:
  - you need to fake queue state to make the repro exist.
- Done when:
  - each retained repro proves one concrete symptom class on the real path.

### 3. Audit Terminal-State Ownership End to End

- Outcome:
  A written explanation exists for who is allowed to publish terminal state and
  where that rule is currently violated or merely assumed.
- Files to read:
  - `weft/core/tasks/base.py`
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/runner.py`
  - `weft/core/runners/host.py`
  - `weft/core/tasks/sessions.py`
  - `weft/core/tasks/interactive.py`
- Required work:
  - Trace `STOP`, `KILL`, timeout, and normal completion.
  - For each path, answer:
    - who observes the stop/kill request?
    - who terminates the runtime?
    - who writes the terminal state/log event?
    - who applies reserved-queue policy?
    - what happens if the runtime exits before the terminal event lands?
  - Compare the actual code to the invariants above.
- Stop if:
  - you find more than one code path that can durably publish terminal state
    for the same lifecycle event. That is a blocker, not a mere note.
- Done when:
  - the audit can point to one owner per transition or name the competing
    owners precisely.

### 4. Audit Manager Drain, Leadership Yield, and Registry Semantics

- Outcome:
  The audit states exactly what the worker registry means, what it does not
  mean, and how manager drain updates it.
- Files to read:
  - `weft/core/manager.py`
  - `weft/commands/worker.py`
  - `tests/core/test_manager.py`
- Required work:
  - Trace:
    - startup,
    - leadership election,
    - leadership yield,
    - STOP drain,
    - and final registry record publication/pruning.
  - Answer:
    - Is the registry a liveness surface, a discovery surface, or both?
    - Is it safe to read while managers are stopping?
    - Which code assumes that a missing registry record means the process is
      gone?
  - Treat registry reads during shutdown as suspect until proven safe.
- Stop if:
  - the audit starts assuming registry state is durable truth without checking
    the manager code that writes it.
- Done when:
  - registry semantics are explicit enough that preserve-cleanup and worker
    commands can be judged against them.

### 5. Audit `tid_mappings`, Runtime Handles, and Liveness Inference

- Outcome:
  The audit names which fields are durable metadata, which are role markers,
  and which are unsafe as liveness truth.
- Files to read:
  - `weft/core/tasks/base.py`
  - `weft/commands/tasks.py`
  - `weft/commands/status.py`
  - `tests/helpers/weft_harness.py`
  - `tests/conftest.py`
- Required work:
  - Trace how `tid_mappings` are written, updated, and read.
  - Confirm:
    - append-only behavior,
    - role tagging,
    - runtime-handle updates,
    - PID fields,
    - and any deduplication logic.
  - Answer:
    - Which consumers treat `tid_mappings` as liveness truth?
    - Which consumers correctly treat it as metadata only?
    - How does PID reuse invalidate naive liveness checks?
- Stop if:
  - any audit note says “mapping says live, therefore task is live” without
    referencing task-log terminal state.
- Done when:
  - the audit can explain stale mapping and PID-reuse failure modes clearly.

### 6. Audit Harness Cleanup Contracts Separately From Product Lifecycle

- Outcome:
  The audit cleanly separates:
  - destructive cleanup,
  - preserve cleanup,
  - and “wait for stable quiescence” behavior.
- Files to read:
  - `tests/helpers/weft_harness.py`
  - `tests/test_harness_registration.py`
  - `tests/cli/test_cli_run.py`
- Required work:
  - Explain what `cleanup()` means today in each mode.
  - Determine whether preserve cleanup should:
    - signal only managers,
    - signal managers then lingering tasks,
    - or something else.
  - Explicitly judge whether registry reads, task STOP fanout, and force-kill
    belong in preserve mode.
  - Name the exact document target that should own preserve-cleanup semantics
    after the audit.
    - Default target: `docs/specifications/08-Testing_Strategy.md`
    - If the audit concludes that preserve-cleanup changes product lifecycle
      semantics rather than test-infrastructure semantics, name the alternate
      target file explicitly and explain why.
  - If needed, add or refine harness-only regression tests that prove the audit
    conclusion.
- Stop if:
  - preserve cleanup starts drifting into “make everything go away somehow.”
- Done when:
  - preserve cleanup’s contract is explicit and test-backed,
  - and the findings name the exact follow-up documentation target.

### 7. Audit Cross-Platform Stop/Kill Behavior

- Outcome:
  The audit names which behavior is expected to differ by platform and which
  should not.
- Files to read:
  - `weft/helpers.py`
  - `weft/commands/tasks.py`
  - `weft/commands/worker.py`
  - any psutil-backed helper used by stop/kill waits
- Required work:
  - Check Linux, macOS, and Windows assumptions separately.
  - Focus on:
    - `pid_is_live(...)`
    - process-tree termination
    - wait loops
    - signal semantics
  - Record where platform-specific behavior is acceptable and where the code is
    incorrectly relying on Unix assumptions.
- Done when:
  - the audit can say “this is platform variance” versus “this is a bug.”

### 8. Confirm CI/Test Runtime Gating

- Outcome:
  The audit records whether test runtime gating matches intent.
- Files to read:
  - `.github/workflows/test.yml`
  - `.github/workflows/release-gate.yml`
  - `.github/workflows/release.yml`
  - test markers and skips
  - docker runner tests
  - macOS sandbox tests
- Required work:
  - Verify whether Docker availability is established before Docker tests run.
  - Verify whether macOS sandbox tests only run on macOS or merely self-skip.
  - Record whether that setup is sufficient or risky.
- Done when:
  - another engineer can answer those questions without rediscovering them.

### 9. Write Findings Before Planning Any Fix

- Outcome:
  A findings document exists with:
  - confirmed bugs,
  - likely bugs,
  - disproved theories,
  - and recommended next fixes ordered by blast radius.
- Files to modify:
  - `docs/plans/task-lifecycle-stop-drain-findings.md`
- Required work:
  - Findings must be presented first, ordered by severity.
  - Each finding must name:
    - why it matters,
    - what exact files or lifecycle edges are involved,
    - how it was reproduced,
    - and whether the proof is broker-backed.
  - Include a short deferred-work section that lists any skipped core task
    (`7` or `8`) and why it was deferred.
  - Include a preserve-cleanup follow-up note that names the exact document
    target chosen in Task 6.
- Stop if:
  - you are about to fix code without written findings.
- Done when:
  - a zero-context engineer could take the findings and write a safe, narrow
    implementation plan.

## Optional Follow-Up Tasks

Only use this section if the core audit remains blocked after Tasks 1 through 8.

### A. Runner-Native Liveness and Control Questions

Use this only if unresolved findings still depend on runner-neutral ambiguity
that the host/harness audit could not answer.

- Outcome:
  The audit records whether runner-native surfaces are relevant to the current
  failure family and, if so, frames the narrowest follow-up planning question.
- Files to read:
  - `weft/ext.py`
  - `weft/_runner_plugins.py`
  - `weft/core/tasks/base.py`
  - `weft/commands/tasks.py`
  - `weft/commands/status.py`
  - `weft/core/runners/host.py`
  - `extensions/weft_docker/weft_docker/plugin.py`
  - `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
  - `docs/plans/runner-extension-point-plan.md`
- Required work:
  - For host, Docker, and macOS sandbox, record the current source of:
    - runtime liveness
    - graceful stop / force kill authority
    - public status description
  - Name where the current code already uses runner-native surfaces and where
    it still falls back to generic host-PID logic.
  - If runner-neutral ambiguity is still blocking the audit, phrase the result
    as a follow-up planning question rather than a redesign proposal.
- Stop if:
  - the audit starts prescribing a new contract or implementation rather than
    describing current behavior and the remaining question.
- Done when:
  - the findings can say either:
    - runner-native analysis was not required for this failure family, or
    - one specific follow-up planning question remains.

## Testing Plan

### Test Execution Rules

Run all commands from the repo root:

- `/Users/van/Developer/weft`

Before running anything:

1. `cd /Users/van/Developer/weft`
2. Confirm you are not inside a deleted temp directory:
   - `pwd`
   - if `pwd` fails or points into `/tmp/weft-harness-*`, `cd /Users/van/Developer/weft`
3. Use the project virtualenv through `uv run`.
4. Default to serial test execution with `-n 0`.

Why serial first:

- These failures are lifecycle- and cleanup-sensitive.
- Starting with xdist hides causality and makes triage worse.
- Use parallel test execution only after the serial proof is understood.

Default backend:

- Do not set `BROKER_TEST_BACKEND` unless you are intentionally auditing the
  Postgres backend.
- The default local audit path is SQLite-backed and that is where the current
  failure family has been concentrated.

If you are checking a single test repeatedly:

- run it with `-q -n 0`
- do not add `-x` unless you are running a multi-test sequence
- if behavior is order-sensitive, keep the exact test order from this plan

If you need a clean shell between experiments:

- start a new shell
- `cd /Users/van/Developer/weft`
- rerun the exact command

If a test run leaves you in a missing temp directory, recover with:

```bash
cd /Users/van/Developer/weft
```

If you suspect stale state from a prior failed experiment:

- confirm `git status --short`
- rerun from the repo root
- do not manually delete broker files inside `tests/helpers/weft_harness.py`
  temp roots unless the experiment specifically calls for it

### Test Order

Use this order during the audit:

1. smallest harness-only proofs
2. targeted lifecycle repros
3. broader CLI slices
4. full local verification
5. release-gate proof

Do not jump directly to `bin/release.py` if the targeted repros are still red.

### Harness and Mapping Unit Proofs

Use these first when checking liveness inference and preserve-cleanup logic in
the harness itself:

```bash
cd /Users/van/Developer/weft
uv run pytest tests/test_harness_registration.py -q -n 0
uv run pytest tests/tasks/test_task_observability.py::test_tid_mapping_includes_metadata_role -q -n 0
```

What these prove:

- harness registration behavior
- manager-vs-task classification
- `tid_mappings` role propagation

### Primary Preserve-Cleanup Repro

This is the main harness/manager-reuse stress test:

```bash
cd /Users/van/Developer/weft
uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0
```

Run it repeatedly if needed:

```bash
cd /Users/van/Developer/weft
for i in {1..5}; do
  uv run pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0 || break
done
```

What this proves:

- parallel `--no-wait` launch under manager reuse
- preserve cleanup
- SQLite integrity before and after cleanup

### Ordered Interaction Repro

Use this when a failure appears order-sensitive:

```bash
cd /Users/van/Developer/weft
uv run pytest \
  tests/cli/test_cli_run.py::test_cli_run_parallel_no_wait_adopts_active_manager \
  tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse \
  -q -n 0
```

What this proves:

- whether the preserve-cleanup failure depends on an earlier parallel-manager
  test running first

### Host Stop/Kill Contract Proofs

Use these to audit real task control, not just harness cleanup:

```bash
cd /Users/van/Developer/weft
uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0
```

If you only need the stop/kill parity cases:

```bash
cd /Users/van/Developer/weft
uv run pytest \
  tests/tasks/test_command_runner_parity.py::test_command_runners_stop_cancel_equivalently[host] \
  tests/tasks/test_command_runner_parity.py::test_command_runners_kill_mark_killed_equivalently[host] \
  -q -n 0
```

What these prove:

- host runner stop behavior
- host runner kill behavior
- runtime disappearance versus terminal durable state

### CLI Timeout and Status Slice

Use this group for the previously failing CLI symptoms:

```bash
cd /Users/van/Developer/weft
uv run pytest \
  tests/cli/test_cli_pipeline.py \
  tests/cli/test_cli_result_all.py \
  tests/cli/test_cli_list_task.py \
  tests/commands/test_task_commands.py \
  -q -n 0
```

What this proves:

- pipeline completion
- result visibility
- status/list terminal-state behavior
- CLI stop/kill command correctness

### Manager Slice

Use this when the theory implicates manager leadership yield, registry, or
drain:

```bash
cd /Users/van/Developer/weft
uv run pytest tests/core/test_manager.py -q -n 0
```

### CLI Slice

Use this before full-suite runs if you want broader user-surface confidence:

```bash
cd /Users/van/Developer/weft
uv run pytest tests/cli -q -n 0
```

### Full Local Verification

Only run this after the targeted repros above are green or fully explained:

```bash
cd /Users/van/Developer/weft
uv run pytest -x -q -n 0
uv run mypy weft
uv run ruff check weft tests extensions/weft_docker extensions/weft_macos_sandbox
```

Notes:

- Prefer `-x` for the first full-suite pass so you get the first real failure
  quickly.
- After the first-failure run is green, run the full suite without `-x` if you
  need completion-level confidence.

### Release-Gate Proof

Use this only after the targeted and full local gates are green:

```bash
cd /Users/van/Developer/weft
uv run python bin/release.py --dry-run --retag
```

If the audit specifically needs the real release path after local proof:

```bash
cd /Users/van/Developer/weft
uv run python bin/release.py --retag
```

Do not use the real release path as your first failure-discovery tool.

### Real-Path Tests to Prefer

- `uv run pytest tests/cli/test_cli_run.py -q -n 0`
- `uv run pytest tests/tasks/test_command_runner_parity.py -q -n 0`
- `uv run pytest tests/commands/test_task_commands.py -q -n 0`
- `uv run pytest tests/cli/test_cli_pipeline.py tests/cli/test_cli_result_all.py tests/cli/test_cli_list_task.py -q -n 0`
- `uv run pytest tests/core/test_manager.py -q -n 0`
- `uv run pytest tests/test_harness_registration.py -q -n 0`

### Full Verification

Use these only after the targeted repros and findings are stable:

- `uv run pytest -q -n 0`
- `uv run mypy weft`
- `uv run ruff check weft tests extensions/weft_docker extensions/weft_macos_sandbox`
- `uv run python bin/release.py --dry-run --retag`

### What Not to Mock

Do not mock:

- `WeftTestHarness`
- SimpleBroker queues
- manager lifecycle
- `run_cli(...)`
- task-log observation
- `tid_mappings`

Mock only if the boundary is truly external to Weft and not the behavior under
audit.

## Audit Log Requirements

Keep a running log in the fixed sibling markdown file below. For each
experiment, record:

Use:

- [`docs/plans/task-lifecycle-stop-drain-audit-log.md`](./task-lifecycle-stop-drain-audit-log.md)

- date/time
- exact command
- expected result
- actual result
- whether it supports or disproves a theory
- next step

This is mandatory. The recent failure family is too subtle to rely on memory.

At minimum, the log must preserve:

- which repros were red,
- which repros became green after which theory or code/test adjustment,
- which theories were disproved,
- and which remaining failures are still unexplained.

## Review Loop

Independent review is strongly recommended after the findings draft is written.
If another agent family or reviewer is available, use the review prompt from
[`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md).

If no independent reviewer is available, record that limitation in the
findings.

## Out of Scope

- Broad runner plugin redesign
- New dependency adoption
- CLI redesign
- Spec rewrite unrelated to lifecycle/control
- Refactors not required to explain the failure family
- Release-process redesign, except insofar as release gates are used as an
  audit proof surface

## Done Criteria

The audit is done only when all of these are true:

- the symptom matrix exists,
- the repro set is explicit,
- confirmed bugs are separated from test/harness issues,
- disproved theories are written down,
- hidden couplings are explicit,
- preserve-cleanup semantics are clearly described,
- the preserve-cleanup follow-up documentation target is named,
- CI/runtime gating questions are answered,
- and there is a written findings document suitable for a separate narrow fix
  plan.

Optional Follow-Up Task `A` may remain deferred. It is only required if the
core host/harness audit proves that runner-neutral liveness or control
ambiguity is still blocking a confident findings write-up.

If Optional Follow-Up Task `A` is executed, the findings must also state whether
it changed the core audit conclusion or only narrowed a future planning
question.

If you cannot yet explain why a failure happens, the audit is not done.
