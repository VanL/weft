# Interactive Session Lifecycle Refactor Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-2.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5]; docs/specifications/08-Testing_Strategy.md [TS-1], [TS-3], [TS-3.1]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
Superseded by: none

Class: 4 — this plan changes the command-side owner of a live interactive
session and fixes three current defects across the task producer, queue client,
and command cleanup boundary. A wrong change can end a session on malformed
control data, hide task-owned terminal proof, lose prompt or piped output, or
leak live queue/watcher resources.

Plan type: behavior-preserving refactor plus bug fixes that restore existing
spec requirements. There is no intended behavior expansion and no normative
spec delta.

Hardening: required. The slice crosses the command/task boundary, consumes
task-local queues, owns a watcher thread and live queue handles, and participates
in terminal evidence and STOP-to-KILL escalation.

## 1. Goal

Resolve the dedicated-lifecycle-plan deferral for
`weft/commands/run.py::_run_interactive_session` (`RUFF-SUP-111`) without
turning interactive execution into a generalized framework.

The implementation may do only two kinds of work:

1. fix the three current, reproducible bugs in Section 7; and
2. refactor the existing command-side lifecycle into one explicit private
   owner plus prompt and piped mode functions, solely to make current behavior
   easier to read, test, and verify.

Primary success is a smaller and more transparent implementation whose
`_run_interactive_session` score is at most 10 and whose clean Python review is
`NET POSITIVE`. If the bounded candidate reduces logical locality or requires
more machinery than Section 10 permits, retain `RUFF-SUP-111` after the rework
procedure. Suppression removal is not the goal at the expense of
comprehensibility.

Any proposal to change the interactive protocol, public API, evidence policy,
cleanup priority, or terminal-envelope compatibility beyond the three proven
bugs goes to Section 18 for later analysis. It is not implementation work under
this plan.

## 2. Requested Outcomes

- [x] Make task-produced interactive terminal envelopes conform to [MF-3] by
  including `source="task"` and an integer `timestamp`.
- [x] Prove the repaired producer is accepted by the real shared strict reader,
  not only by assertions over literal keys.
- [x] Make `InteractiveStreamClient` use the existing strict terminal-envelope
  reader for terminal classification, so wrong-source, wrong-TID, and
  nonterminal-status payloads cannot complete a session.
- [x] Keep legacy stderr/control rendering behavior for payloads that are not
  accepted as terminal; this plan changes terminal classification only.
- [x] Move `InteractiveStreamClient.start()` inside the already-owned cleanup
  boundary so a start failure closes the client and global-log queue while
  re-raising the exact original exception.
- [x] Preserve queue-mediated line IO. Do not add PTY or terminal-emulation
  behavior.
- [x] Preserve prompt and piped input modes, initial-work-payload ownership,
  auto-close rules, STOP-to-KILL order, quit normalization, prompt-thread exit,
  terminal fallback order, output aggregation, and cleanup order.
- [x] Replace the large implicit closure frame with at most one private
  command-layer lifecycle owner in `weft/commands/run.py`.
- [x] Keep prompt and piped mode control flow separately readable and directly
  testable. Do not merge them into a generic event loop.
- [x] Add no state machine, reducer, event bus, protocol interface, retry layer,
  context-manager framework, or reusable interactive-session package.
- [x] Add no defensive behavior for a failure that cannot be demonstrated in
  current code or tied to an existing spec rule.
- [x] Keep `RUFF-SUP-044` on task-side finalization active. Adding the two
  required envelope fields is a bug fix, not authority to split that finalizer.
- [x] Keep the approved optional-monitor fallback (`RUFF-SUP-332`) behavior
  exact. If its catch moves with the lifecycle owner, reconcile only its exact
  symbol/site; its cardinality and rationale do not change.
- [x] After each cohesive refactor, use a clean Python expert to judge whether
  it is net positive or net negative, with explicit attention to logical
  locality and comprehensibility.
- [x] Put a negative refactor in the bounded Rework Queue. Do not immediately
  discard it or iterate without a limit.
- [x] Change no public CLI option, result tuple, exit code, queue name,
  persistence mode, stream envelope, control command, timeout, grace interval,
  diagnostic text, or task state transition except the three fixes listed in
  Section 7.

## 3. Source Documents And Historical Context

Normative owners:

- `docs/specifications/01-Core_Components.md` [CC-2.3] requires interactive
  command sessions to reuse ordinary task/runtime conventions instead of a
  second terminal subsystem. [CC-2.4] keeps durable task state canonical and
  requires STOP/KILL acknowledgement without duplicate terminal transitions.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] owns
  backend-neutral watcher behavior. The refactor must keep using
  `InteractiveStreamClient` and `MultiQueueWatcher`; it must not add
  backend-specific waiting or one listener per queue.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2] owns reservation,
  stream, and terminal publication order. [MF-3] requires typed task-local
  terminal envelopes with `type`, `source`, `tid`, `status`, and `timestamp`.
  [MF-5] owns terminal evidence priority and says interactive output is not a
  one-shot result-completion shortcut.
- `docs/specifications/08-Testing_Strategy.md` [TS-1] requires real queue and
  lifecycle proof where practical. [TS-3] requires simplification to McCabe 10
  or an approved exception. [TS-3.1] owns exact suppression identity and atomic
  reconciliation.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] defines
  `--interactive` as command-only, queue-mediated line IO. The command layer
  still owns prompt callbacks because there is no public `WeftClient.run()`
  interactive API.
- `docs/ruff-suppression-registry.md` is the operational suppression ledger. It
  is not a behavior source.

Historical context is rationale, not new behavior:

- `docs/plans/2026-07-29-structural-review-remediation-plan.md` rejected a
  shared result/follow/interactive observation abstraction, but identified
  `_run_interactive_session` as a separate floor-2 candidate needing its own
  plan.
- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`
  approved `RUFF-SUP-111` temporarily and rejected cosmetic callback
  extraction or helpers that merely pass the live closure frame around.
- `docs/plans/2026-08-05-ruff-stable-default-lint-expansion-plan.md` fixed the
  real PromptToolkit completion race and approved the open optional-monitor
  evidence boundary as `RUFF-SUP-332`. This plan must preserve both outcomes.
- `docs/lessons.md` records the current interactive contract: line-oriented
  task-local IO, `ctrl_out` terminal completion, task-local channels before the
  global log, and real terminal cleanup boundaries under load.

The implementation audit must verify historical claims against current code
and tests. It must not convert historical proposals into new requirements.

## 4. Context And Key Files

Required repository guidance for every task:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/lessons.md` and `docs/lessons.md`

Task 1, baseline and feasibility:

- read the complete `weft/commands/run.py::_run_interactive_session`, not only
  the prompt or piped half
- read `weft/commands/interactive.py::InteractiveStreamClient` in full
- read `weft/core/tasks/interactive.py` from session creation through
  `_interactive_shutdown()`
- read `weft/core/task_evidence.py::coerce_terminal_envelope`,
  `select_terminal_envelope`, and `peek_terminal_ctrl_out_evidence`
- read `weft/core/tasks/base.py::_send_terminal_envelope` as the canonical
  noninteractive producer shape
- read `weft/core/manager.py::_child_terminal_proof_visible` to understand why
  `source="task"` is operational proof, not decorative metadata
- reproduce the raw scores and Section 10 feasibility probe without editing
  production source

Task 2, proven bug fixes:

- files to modify:
  - `weft/core/tasks/interactive.py`
  - `weft/commands/interactive.py`
  - `weft/commands/run.py`
  - `tests/tasks/test_task_interactive.py`
  - `tests/commands/test_interactive_client.py`
  - `tests/commands/test_run.py`
- reuse `weft.core.task_evidence.coerce_terminal_envelope`; do not create a
  second validator in the command package
- use real broker queues for producer/consumer envelope behavior
- use the existing narrow fake-client seam only for the command-owned start
  failure and PromptToolkit boundary

Task 3, characterization before source refactoring:

- files to modify only if a missing current branch needs a characterization:
  - `tests/commands/test_run.py`
  - `tests/commands/test_interactive_client.py`
  - `tests/cli/test_cli_run.py`
- read the existing prompt completion test, real piped CLI test, control
  response tests, monitor failure test, and output/failure precedence tests
- add only the STOP-before-KILL and final output/cleanup proofs needed to move
  the current branches safely; do not build an exhaustive artificial model

Task 4, bounded lifecycle candidate:

- modify only:
  - `weft/commands/run.py`
  - `tests/commands/test_run.py`
- `weft/commands/interactive.py` remains the queue client; do not move prompt
  or command-result policy into it
- `weft/core/tasks/interactive.py` remains the task-side producer; do not move
  command-side waiting into it
- keep optional `prompt_toolkit` imports local with the existing optional-
  dependency comment

Task 6, suppression reconciliation:

- files to read first:
  - `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1]
  - `docs/ruff-suppression-registry.md`
  - `bin/ruff_suppression_index.py`
  - `tests/specs/test_ruff_policy.py`
  - `tests/specs/test_ruff_suppression_index.py`
- conditional files to modify:
  - `docs/ruff-suppression-registry.md`
  - `weft/commands/run.py`
  - `tests/specs/test_ruff_policy.py`
- retention changes only the durable human rationale/approval row for
  `RUFF-SUP-111`; source and derived counts remain exact
- removal changes the source directive, human row, raw inventory, generated
  index, and exact fixtures atomically

Task 7, traceability and closeout:

- add Related Plans backlinks to the five governing specs without changing
  normative text or implementation mappings
- update `docs/lessons.md` only if implementation or review exposes a repeated
  failure mode not already recorded
- modify no other files unless a named gate or accepted reviewer finding
  requires it

Comprehension questions before editing:

1. Why is an interactive final stream marker not by itself terminal proof, and
   why does a task-owned typed `ctrl_out` envelope matter to the manager and
   shared evidence readers?
2. In `:quit` handling, why may a STOP or KILL acknowledgement end the local
   escalation helper while the outer prompt path still performs a bounded
   terminal-completion wait?
3. Which resources exist before `InteractiveStreamClient.start()`, and which
   exact cleanup calls are skipped when that call currently raises?
4. Why may the command layer use global-log and Monitor state only as fallback
   evidence rather than making either the interactive client's primary channel?

If the implementer cannot answer these from source and specs, stop before
editing.

## 5. Spec Baseline And No Proposed Delta

- Baseline: `78432dcacc997dacde7941048716fb10bfe9dfd5` for
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/04-SimpleBroker_Integration.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/08-Testing_Strategy.md`, and
  `docs/specifications/10-CLI_Interface.md` at plan authoring time.
- Proposed spec delta: none.
- Promotion strategy: none. The three fixes restore rules already in [MF-3]
  and the rest of the work preserves current behavior.
- If implementation needs a new terminal-envelope compatibility rule, new
  evidence priority, new quit meaning, or new cleanup-failure priority, stop.
  Record a Deviation Log row and open a separate spec analysis/revision slice
  before changing behavior.

## 6. Current Architecture And Ownership

The current path is one queue-mediated lifecycle:

| Phase | Current owner | Current observable effect |
|---|---|---|
| task session execution | `InteractiveTaskMixin` and `CommandSession` | reserves inbox input, emits stdout to outbox, stderr/control/terminal to `ctrl_out`, writes task-log state |
| task terminal publication | `_interactive_finalize_session()` | preserves prior terminal authority, writes final stream envelopes, writes one interactive terminal envelope, ends streaming ownership, closes the runner session |
| queue consumption | `InteractiveStreamClient` | consumes outbox/`ctrl_out` on one `MultiQueueWatcher`, invokes callbacks, stores histories and control replies, sets completion |
| command observation | `_run_interactive_session()` | owns prompt/piped mode, local/global/Monitor evidence, user quit escalation, output collection, and client/log cleanup |
| shared terminal interpretation | `weft.core.task_evidence` | accepts strict typed envelopes, ranks task proof over manager `wrapper_lost`, and supplies public terminal snapshots |

`_run_interactive_session()` currently holds its live frame implicitly in eight
nested functions plus local mutable values: status/error, stdout chunks, log
cursor, client, log queue, queue names, prompt completion event, and quit flag.
This plan may make that ownership explicit. It may not relocate protocol policy
to a new package or create a second durable state model.

### 6.1 Current precedence and order that must stay visible

- Completion check order inside a wait turn is:
  1. `InteractiveStreamClient.wait(0)`;
  2. task-log terminal evidence;
  3. optional Monitor terminal evidence;
  4. deadline;
  5. one bounded client wait.
- Task-log evidence short-circuits Monitor access in the same turn.
- Optional Monitor access failure returns no evidence and remains silent under
  approved `RUFF-SUP-332`.
- `:quit` order is fixed: close input, short completion wait, STOP, STOP ack,
  short completion wait, KILL, KILL ack, final short completion wait. The outer
  prompt path then owns the longer terminal-completion bound.
- A user-requested `:quit` normalizes a resulting `cancelled` or `killed`
  status to command success. Other cancellations/kills remain terminal errors.
- Cleanup order is client stop, then global-log queue close. In piped mode,
  in-memory callback chunks/history are assembled before a final outbox drain.
- Piped final outbox data fills an empty result; it does not overwrite already
  captured output.
- No correctness assertion may depend on mapping or set iteration order. The
  STOP-before-KILL list is a declared protocol order and may be asserted.

## 7. Proven Bug Inventory

Only these behavior changes are admitted without a plan revision.

| ID | Current defect and evidence | Required correction | Explicit boundary |
|---|---|---|---|
| E5-B1 | `_interactive_terminal_envelope()` emits `type`, `tid`, `status`, and `event`, but omits [MF-3]'s required `source` and `timestamp`. A direct production-builder probe produced that payload and `coerce_terminal_envelope(...)` returned `None`. Manager task-proof inspection also requires `source="task"`. | Add literal `source="task"` and an integer `time.time_ns()` timestamp to the existing producer. Prove the real emitted queue row passes the shared strict reader. | Do not otherwise split `RUFF-SUP-044`, reorder final streams/terminal publication, or change event/error/return-code fields. |
| E5-B2 | `InteractiveStreamClient._handle_ctrl_message()` accepts any mapping with `type="terminal"` and a `status` key. A production probe with `status="running"`, no source, and no TID set completion and status `running`. | Reuse `coerce_terminal_envelope(message, tid=self._tid)` for terminal classification. Valid task/manager terminal envelopes still complete; wrong-source, wrong-TID, and nonterminal status do not. | Do not tighten the shared reader's timestamp compatibility or suppress ordinary nonterminal rendering in this slice. |
| E5-B3 | `client.start()` is called before the function's `try/finally`. A direct injected start failure re-raised by exact identity while both `client.stop_count` and `log_queue.close_count` remained zero. | Put start inside the existing owned cleanup region. Re-raise the exact start failure; stop the client once and close the log queue once. | No retry, fallback client, catch-and-wrap, cleanup aggregation, or new error message. Preserve current client-stop-before-log-close order. |

### 7.1 Clear-bug admission gate for implementation discoveries

A newly noticed issue may join this plan only if all five statements are true:

1. current production code deterministically demonstrates the defect or
   violates an exact cited active-spec rule;
2. a failing test or direct production-path probe demonstrates it before the
   fix;
3. the smallest correction stays in the files and lifecycle named here;
4. the correction does not choose a new protocol, public meaning, compatibility
   policy, or cleanup priority; and
5. an independent reviewer agrees that it is a current bug, not generalized
   hardening.

If any statement is false, add it to Deferred Design Analysis or open a
separate bug plan. Do not expand this plan because the adjacent code could be
made more robust.

## 8. Invariants And Constraints

- Queue-mediated line IO remains the only interactive transport. No PTY/TTY
  emulation enters core or command code.
- Task TID and TaskSpec `spec`/`io` remain unchanged after resolution.
- Inbox, outbox, `ctrl_in`, and `ctrl_out` names continue to honor explicit
  TaskSpec mappings with the same TID-derived fallbacks.
- The task still reserves and acknowledges interactive input through the
  existing `InteractiveTaskMixin` path.
- Stream envelope shapes, chunk indices, final markers, stderr routing, and
  terminal publication order remain unchanged except for adding required
  `source` and `timestamp` fields.
- Strict terminal classification accepts only the shared reader's current
  task/manager sources and terminal statuses for the matching TID.
- `InteractiveStreamClient` remains the only command-side owner of its inbox
  queue and `MultiQueueWatcher`.
- Prompt callbacks remain safe for the watcher thread and retain the fixed
  before-run/while-running PromptToolkit completion behavior.
- Global task log and Monitor store remain fallback completion evidence. They
  do not become interactive data channels.
- Optional Monitor failure remains silent and ordinary only; control-flow
  `BaseException` and interpretation defects remain outside the catch.
- STOP-to-KILL order, ack interpretation, quit normalization, deadlines, and
  messages remain byte-for-byte behaviorally unchanged.
- Client and log cleanup stay exact-once on ordinary return and on E5-B3's
  start failure. This plan does not define new behavior for cleanup methods that
  themselves raise.
- Prompt mode continues live rendering and returns no collected result. Piped
  mode retains current callback/history/final-outbox result assembly.
- No new dependency, public API, module, queue, thread, timer, or persistence
  surface is allowed.
- No new mapping/set order dependency is allowed. Tests use exact membership,
  `Counter`, or keyed assertions where order is not a protocol rule.
- Every new production function/method must be at most McCabe 10 without a new
  suppression.
- No drive-by cleanup in `_build_taskspec_dict`, other `run` modes,
  `InteractiveStreamClient.stop()`, shared task evidence, or task finalization.

## 9. Scope Challenge And Decision

The plan rejects two easy but wrong readings of the task.

First, this is not permission to unify one-shot result, realtime follow,
persistent result, and interactive observation. Those surfaces consume
different evidence and the earlier structural review already rejected that
abstraction.

Second, a score of 58 does not by itself justify a state machine. The command
owner has no undecided transition policy comparable to terminal handoff. Its
complexity comes from an implicit resource/callback frame plus two concrete UI
modes. A new event vocabulary or reducer would duplicate `InteractiveStreamClient`
completion and task-evidence classification.

The bounded candidate is justified only because it names ownership that
already exists and exposes current branches for direct tests. If the candidate
needs more than the mechanism budget below, retention is the correct outcome.

## 10. Bounded Refactor Design And Feasibility

### 10.1 Current measured feasibility

At the authoring baseline:

- raw Ruff reports `_run_interactive_session` at C901 58;
- moving the eight existing nested callback/wait bodies and both prompt/piped
  bodies behind named owners, while leaving final outcome/collection inline,
  leaves the outer owner at 16;
- also moving final outcome normalization and piped result collection to the
  same lifecycle owner yields outer C901 10;
- a structurally equivalent prompt function with its two nested completion
  callbacks moved to lifecycle methods scores 9;
- the piped function scores 4.

These are in-memory AST feasibility measurements, not permission to land a
mechanical extraction. They prove only that the allowed design can meet the
configured ceiling. Locality review decides whether it should.

### 10.2 Exact mechanism budget

The candidate may introduce exactly these production mechanisms in
`weft/commands/run.py`:

1. One private `_InteractiveRunLifecycle` class.
   - It is a single-use command-layer owner, not a public session abstraction.
   - It owns the current status/error values, stdout chunks, log cursor,
     `InteractiveStreamClient`, global-log queue, callbacks, completion polling,
     control escalation, outcome normalization, and final piped result
     assembly.
   - Its methods are direct moves of the current nested functions. They must
     keep the same order and constants.
   - It may not own task execution, TaskSpec mutation, manager behavior, shared
     task-evidence policy, or PromptToolkit types outside the prompt methods.
2. One `_run_interactive_prompt(...)` function.
   - It owns the existing PromptSession, completion event/thread, prompt loop,
     EOF/KeyboardInterrupt behavior, `:quit`/`:exit`, and final bounded wait.
   - It returns only whether quit normalization was requested.
3. One `_run_interactive_piped(...)` function.
   - It owns the existing stdin-send, `auto_close`, 0.2-second completion probe,
     close-input, and unbounded terminal wait branches.
   - It returns no policy object or generalized outcome.

The prompt thread target and `pre_run` callback may be trivial lambdas that
delegate to lifecycle methods. Do not introduce a second callback class or
protocol merely to avoid those closures.

The task producer fix and strict-reader reuse add no new mechanism.

### 10.3 Prohibited mechanisms

- no state enum, state machine, reducer, transition table, event dispatcher, or
  session protocol
- no shared result/interactive observer
- no new module or package split
- no base class, `Protocol`, factory registry, plugin point, or public context
  manager
- no generic resource stack, retry/fallback wrapper, cleanup-error collector,
  or exception translation layer
- no helper whose only purpose is to subtract one McCabe point
- no helper that takes most of the live frame as separate arguments; the one
  lifecycle owner exists to avoid that false seam
- no modification of `InteractiveStreamClient` beyond E5-B2 and the imports
  needed for that exact fix
- no modification of task-side finalization beyond E5-B1

### 10.4 Stop gates

Stop and revise the plan before continuing if:

- `_InteractiveRunLifecycle` starts representing task-side or manager state;
- prompt and piped modes are merged into a generic event loop;
- a new function exceeds C901 10 or needs a suppression;
- removing `RUFF-SUP-111` requires moving behavior to another already-complex
  owner;
- tests require a generalized fake client or simulate broker/process semantics
  that a real queue test can prove;
- the refactor changes a timeout, callback order, output choice, or diagnostic;
- a proposed fix is not admitted by Section 7.1;
- the approved `RUFF-SUP-332` catch widens, narrows, logs, or changes fallback;
  or
- rollback cannot return to the three standalone bug fixes without touching a
  public contract.

## 11. Testing Plan

### 11.1 E5-B1: canonical task-produced terminal envelope

Use `broker_env`, a real `Consumer`, and the existing interactive command
fixture in `tests/tasks/test_task_interactive.py`.

Strengthen the existing completion/failure proof or add one focused test that:

- drives the real interactive task to terminal completion;
- reads the actual terminal row from `ctrl_out`;
- asserts `source == "task"`, matching `tid`, terminal `status`, and integer
  `timestamp`;
- serializes that exact row through
  `weft.core.task_evidence.coerce_terminal_envelope(...)` and proves it is
  accepted; and
- keeps final stdout/stderr ordering assertions intact.

Red-first: the combined producer contract must fail against the baseline. The
shared reader rejects the missing `source`; the explicit field assertion also
proves the independently required integer `timestamp` is absent.

### 11.2 E5-B2: strict interactive-client terminal classification

Use the real SQLite-backed queues and `InteractiveStreamClient` in
`tests/commands/test_interactive_client.py`.

- Update existing valid terminal fixtures to include canonical `source`, `tid`,
  terminal `status`, and `timestamp`.
- Add a compact parameterized rejection proof for at least:
  - missing/unknown `source`;
  - a different TID; and
  - `status="running"`.
- Use one handler-observed synchronization event that is set by both the state
  callback and the existing stderr fallback callback. Wait for that event after
  each invalid row, then prove completion, client status, and state-callback
  history remain unset. This distinguishes a handled-and-rejected row from
  watcher lag.
- Then publish one valid terminal envelope and prove the same client completes
  with exactly that terminal status and state-callback payload. This keeps a
  stopped watcher or broken test setup from making the rejection pass
  vacuously.
- Assert the valid envelope TID and the client's requested TID are the same
  canonical string. Do not add command-side TID coercion to make the test pass.
- Exercise completion through the client's `ctrl_out` handler itself; task-log
  or Monitor fallback must not satisfy this proof.
- Do not assert that invalid control payloads produce no diagnostic rendering;
  only terminal classification changes here.

Red-first: the current client completes on at least the nonterminal-status
payload.

### 11.3 E5-B3: start-failure cleanup

Use the existing narrow fake client/log queue pattern in
`tests/commands/test_run.py`. The fake is appropriate because the contract is
the command owner's call order around an injected external start boundary.

Prove:

- `start()` raises a prebuilt custom ordinary exception;
- `_run_interactive_session()` re-raises the exact object;
- `client.stop()` is called exactly once;
- the already-open global-log queue closes exactly once; and
- no stdout/stderr is emitted.

Red-first: the baseline currently reports zero cleanup calls.

### 11.4 Refactor characterizations

Keep current production behavior tests, then add only missing proofs required
by the move:

- preserve the existing real PromptToolkit before-run and while-running
  completion cases;
- preserve the real piped CLI run through manager, task queues, output, and
  terminal state;
- add one narrow lifecycle-owner test showing STOP ack prevents KILL, and one
  showing absent completion/acks sends STOP then KILL in that declared order
  before using the final completion observation;
- preserve monitor failure fallback through the client channel with no output;
- preserve task-log-before-Monitor short-circuit using a narrow Monitor seam,
  only if the refactor would otherwise make that order ambiguous;
- preserve failure-over-final-stdout and control-stop terminal behavior in the
  real client/task tests; and
- preserve piped result choice: callback/history data wins, and final outbox
  fills only an empty result.

Characterizations begin green. Demonstrate sensitivity with temporary, fully
restored mutations:

- skip KILL after a failed STOP path and show the escalation test fails; and
- remove prompt `pre_run` completion handling and show the existing before-run
  test fails.

Do not call a green characterization red-first. Do not retain mutation code.

### 11.5 What not to mock

- Do not mock task reservation, task terminal publication, real outbox or
  `ctrl_out` behavior, or strict-reader acceptance.
- Do not replace the CLI piped execution proof with direct helper calls.
- Mock PromptToolkit input/application only through its existing pipe-input
  test seam.
- Mock client start and control outcomes only where the command-owned ordering
  itself is the contract.
- Do not introduce sleeps as assertions. Use existing completion events,
  bounded waits, and real queue observation.

## 12. Dependency-Ordered Tasks

### Task 1: lock the baseline and reproduce feasibility

Files:

- this plan's Evidence Log only if measured facts differ
- no production edits

Steps:

1. Record the current commit and spec baseline.
2. Run raw Ruff for `run.py`, `commands/interactive.py`, and
   `core/tasks/interactive.py` with `--ignore-noqa`.
3. Reproduce E5-B1, E5-B2, and E5-B3 with the direct production probes from
   Section 17.
4. Run the focused interactive suites serially.
5. Capture the before Backstitch report at
   `/tmp/weft-effort5-backstitch-before.json` using the final command's exact
   roots and options.
6. Reproduce the Section 10 structural score probe without writing a candidate
   into the repository.

Done when the baseline, three defects, raw score, and bounded feasibility are
independently reproducible.

### Task 2: fix only the three admitted bugs, red first

Files:

- `weft/core/tasks/interactive.py`
- `weft/commands/interactive.py`
- `weft/commands/run.py`
- `tests/tasks/test_task_interactive.py`
- `tests/commands/test_interactive_client.py`
- `tests/commands/test_run.py`

Steps:

1. Add the E5-B1 test and observe the strict-reader failure.
2. Add the E5-B2 rejection test and observe false completion.
3. Add the E5-B3 cleanup test and observe both cleanup counts remain zero.
4. Add only `source` and `timestamp` to the task producer.
5. Route client terminal classification through the shared strict reader.
6. Move client start inside the existing cleanup boundary without changing
   exception type, text, or cleanup order.
7. Update the touched producer and client docstrings to cite their exact
   governing sections, including [MF-3]. Do not add a new behavior claim.
8. Run the three focused test files, targeted Ruff, mypy, and diff check.
9. Send this cohesive bug-fix group to a clean Python reviewer. The reviewer
   must confirm each behavior change repairs current evidence and that no
   generalized validation or cleanup policy entered the slice.

Do not begin the structural candidate until these fixes are independently
reviewed as net positive. A bug-fix review finding that proposes broader
compatibility or lifecycle policy goes to Section 18 unless it proves another
Section 7.1 bug.

### Task 3: add the minimum refactor characterization floor

Files:

- `tests/commands/test_run.py`
- existing tests elsewhere only if one exact current branch cannot be proven in
  `test_run.py`

Steps:

1. Audit the Section 11.4 matrix against existing tests.
2. Add only the missing STOP/KILL and result/cleanup proofs.
3. Keep the characterization tests green against the bug-fixed pre-refactor
   source.
4. Run and restore the two named temporary mutations to prove sensitivity.
5. Run the full focused interactive suite serially.

No production refactor begins until this floor is green and sensitive.

### Task 4: implement the bounded lifecycle candidate

Files:

- `weft/commands/run.py`
- `tests/commands/test_run.py`

Steps:

1. Introduce only the Section 10.2 mechanisms.
2. Move existing closure state and bodies into `_InteractiveRunLifecycle`
   without changing branch order or constants.
3. Keep prompt and piped branches in their two named functions.
4. Keep `_send_interactive_control()` and `_request_interactive_exit()` as
   lifecycle-owner methods invoked by the prompt function. Do not move their
   client/completion state into `_run_interactive_prompt()` or pass that state
   back as a new argument bundle.
5. Keep `_run_interactive_session()` as the visible orchestration: construct
   the lifecycle owner, start inside its cleanup region, select one mode,
   obtain the current outcome/result, and close resources.
6. Give each materially changed lifecycle owner an exact governing spec
   reference in its docstring; do not change implementation ownership.
7. Keep the `RUFF-SUP-111` directive until review and reconciliation.
8. If the `RUFF-SUP-332` catch moves, keep its exact directive on the moved
   catch and make no semantic change.
9. Run focused tests after each cohesive move.
10. Run raw Ruff with `--ignore-noqa`. Every new owner must be at most 10; the
   target must be at most 10 before suppression removal is considered.
11. Run focused mypy, Ruff with the approved still-live directives, formatter
   check, and diff check.

If the score remains above 10 within this mechanism budget, stop and select
retention. Do not add another helper merely to reduce the score.

### Task 5: clean Python locality review and bounded rework

The clean reviewer receives:

- baseline source and candidate diff;
- Sections 6-11 of this plan;
- raw before/after scores;
- focused test and mutation evidence; and
- the exact E5-B1/B2/B3 behavior deltas already approved as bug fixes.

The reviewer must answer:

1. Is the refactor `NET POSITIVE` or `NET NEGATIVE`?
2. Did it reduce logical locality or make the lifecycle harder to scan?
3. Does `_InteractiveRunLifecycle` name one current owner, or is it an
   invented framework for hypothetical reuse?
4. Are prompt, piped, completion, escalation, outcome, and cleanup decisions
   still easy to find in one reading path?
5. Did any method or test invent behavior for a problem that has not occurred?
6. Is the design more transparent and testable without becoming brittle?
7. Would retaining `RUFF-SUP-111` be clearer than this candidate?

Verdict must be exactly `NET POSITIVE` or `NET NEGATIVE`, with concrete
findings.

- `NET POSITIVE`: proceed to Task 6.
- First `NET NEGATIVE`: keep the candidate active, add it to the Rework Queue,
  and save its exact diff outside the repository at
  `/tmp/weft-effort5-ruff-sup-111-attempt-1.patch` with SHA-256. Perform at most
  one reviewer-directed rework within Section 10, then use a different clean
  reviewer.
- Second `NET NEGATIVE`, or a requested mechanism outside Section 10: recommend
  retention to the owner. Preserve the final candidate patch and review
  evidence before restoring only the structural portion; keep the three bug
  fixes. Do not land a known-negative refactor to satisfy Ruff.

### Task 6: reconcile suppressions atomically

Files:

- `docs/ruff-suppression-registry.md`
- `weft/commands/run.py`
- `tests/specs/test_ruff_policy.py` only if `RUFF-SUP-111` is removed
- this plan

Removal path, only after `NET POSITIVE` and owner authorization:

1. Remove the exact `RUFF-SUP-111` source directive.
2. Remove its human registry row.
3. Decrement the global raw `C901` inventory, generated index, and exact policy
   fixtures by exactly one:
   - remove `RUFF-SUP-111` from `EXPECTED_GROUP_IDS`;
   - `EXPECTED_GROUP_COUNT`: 231 to 230 at the authoring baseline;
   - `EXPECTED_DIRECTIVE_COUNT`: 374 to 373;
   - `EXPECTED_C901_DIRECTIVE_COUNT`: 140 to 139.
4. Regenerate the delimited index and run the checker/policy tests.

The implementer must re-read live counts immediately before editing. If another
approved slice changed the baseline, apply a one-group/one-directive/one-C901
delta to the live values rather than forcing the authoring numbers.

If `RUFF-SUP-332` moved with the lifecycle method:

- move the exact source directive with the catch;
- regenerate the derived qualified symbol from
  `weft/commands/run.py::_run_interactive_session` to
  `weft/commands/run.py::_InteractiveRunLifecycle._poll_monitor_terminal`;
- change the human group row only if its prose literally names the old owner;
- keep the global raw inventory, `BLE001=1`, group count, directive count,
  invariant, proof, and approved rationale unchanged; and
- obtain a clean exact-site review that proves the same catch and firing test.

Retention path:

- keep source, human row, raw inventory, generated index, and exact fixtures;
- update only the `RUFF-SUP-111` human rejected-alternative/review text to
  record the completed dedicated review and owner disposition; and
- prove every count and source directive is unchanged.

Never leave source, human rows, raw inventory, generated index, or exact
fixtures partially reconciled.

### Task 7: traceability, final review, and owner-authorized landing

Files:

- the five governing specs, Related Plans sections only
- `docs/plans/README.md`
- `docs/lessons.md` only if required by an actual repeated correction
- all changed implementation/test/registry files

Steps:

1. Add the plan backlinks and verify the touched producer, client, and
   lifecycle-owner docstrings cite the exact governing sections, without
   changing normative behavior or implementation mappings.
2. Run focused, full, policy, metadata, traceability, formatter, and diff gates
   from current state.
3. Run a final different-family outside-model review over the complete plan,
   diff, specs, and evidence. It must explicitly check for general
   “improvement” work, invented mechanisms, brittle tests, and undeclared
   design changes.
4. Reconcile every finding or record a reasoned disposition.
5. Fill only completed Evidence, Review, Rework, and Deviation rows; do not
   record transient worktree state.
6. Mark this plan `completed` only when implementation, review, suppression
   disposition, traceability, and current-state gates are complete.
7. Obtain owner authorization before committing. If authorization is absent,
   report the exact uncommitted file set and evidence without claiming the
   completion gate is satisfied.
8. Stage by explicit file list. Never include unrelated concurrent changes.

## 13. Error, Cleanup, Privacy, And Order Matrix

| Condition | Required result | Cleanup/order |
|---|---|---|
| valid task terminal envelope | client state callback, terminal status/error, completion | task/local source and TID accepted by shared strict reader |
| invalid terminal-looking payload | no terminal completion | may continue through existing nonterminal rendering; no new diagnostic rule |
| optional Monitor read raises ordinary exception | no evidence from Monitor | silent fallback; client/log remain authoritative; `RUFF-SUP-332` exact |
| client start raises | exact original exception propagates | client stop once, then log queue close once |
| prompt completes before app run | prompt never hangs | completion event plus prompt-thread `pre_run` exit check |
| prompt completes while app runs | prompt exits safely | schedule the same exit check with `call_soon_threadsafe`; closed-loop `RuntimeError` remains narrow |
| user requests `:quit` | close, STOP, then KILL only as needed | declared sequence; outer final terminal wait remains |
| quit yields cancelled/killed | command result normalizes to completed | only when local `quit_requested` is true |
| piped output captured by callbacks/history | captured data is result | final outbox does not overwrite it |
| piped captured result empty | final outbox may fill result | queue always closes |
| client/log cleanup method itself raises | preserve current behavior | no new priority/aggregation contract in this plan |

Diagnostics and exception payloads keep their current privacy behavior. This
plan adds no logging, exception interpolation, traceback, or user-visible text.

## 14. Rollout, Rollback, And One-Way Doors

There is no schema, storage, queue-name, or public API one-way door.

Rollout is one atomic code/test/registry slice after review:

- producer and consumer canonical-envelope fixes land together;
- task-log and Monitor fallbacks preserve bounded mixed-version tolerance;
- the command refactor, if accepted, lands with its suppression disposition;
  and
- spec backlinks land in the same traceability closeout.

Rollback order:

1. Keep E5-B1/B2/B3 unless their direct regressions are disproven.
2. Revert only the `_InteractiveRunLifecycle` and prompt/piped extraction if the
   refactor causes a regression.
3. Restore `RUFF-SUP-111` source/registry/raw/index/fixtures as one atomic
   policy unit if structural rollback restores the complex owner.
4. If `RUFF-SUP-332` moved, restore its exact site/symbol with the structural
   rollback.

Do not roll back the producer without the client compatibility analysis: the
canonical fields are required by the active spec and shared reader.

## 15. Verification And Gates

Load the repository environment first:

```bash
. ./.envrc
```

Focused behavior:

```bash
./.venv/bin/python -m pytest -q -n0 \
  tests/commands/test_run.py -k interactive
./.venv/bin/python -m pytest -q -n0 \
  tests/commands/test_interactive_client.py
./.venv/bin/python -m pytest -q -n0 \
  tests/tasks/test_task_interactive.py
./.venv/bin/python -m pytest -q -n0 \
  tests/cli/test_cli_run.py::test_cli_run_interactive_command_streams \
  tests/cli/test_cli_run.py::test_cli_run_interactive_json_conflict \
  tests/cli/test_cli_run.py::test_cli_run_interactive_requires_command_target
```

Complexity and local static checks:

```bash
./.venv/bin/ruff check --select C901 --ignore-noqa \
  --output-format json \
  weft/commands/run.py \
  weft/commands/interactive.py \
  weft/core/tasks/interactive.py \
  > /tmp/weft-effort5-c901.json || test $? -eq 1
python3 - /tmp/weft-effort5-c901.json <<'PY'
from pathlib import Path
import json
import re
import sys

root = Path.cwd()
expected = {
    ("weft/commands/run.py", "render_run_execution_result", 13),
    ("weft/commands/run.py", "_execute_inline", 13),
    ("weft/commands/run.py", "execute_run", 16),
    ("weft/core/tasks/interactive.py", "_interactive_finalize_session", 12),
}
if "RUFF-SUP-111" in (root / "weft/commands/run.py").read_text(encoding="utf-8"):
    expected.add(("weft/commands/run.py", "_run_interactive_session", 58))

actual = set()
for issue in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")):
    match = re.fullmatch(r"`([^`]+)` is too complex \((\d+) > 10\)", issue["message"])
    if match is None:
        raise SystemExit(f"unexpected C901 message: {issue['message']!r}")
    relative = str(Path(issue["filename"]).resolve().relative_to(root.resolve()))
    actual.add((relative, match.group(1), int(match.group(2))))

if actual != expected:
    raise SystemExit(f"C901 mismatch: expected={sorted(expected)!r} actual={sorted(actual)!r}")
PY
./.venv/bin/ruff check \
  weft/commands/run.py \
  weft/commands/interactive.py \
  weft/core/tasks/interactive.py \
  tests/commands/test_run.py \
  tests/commands/test_interactive_client.py \
  tests/tasks/test_task_interactive.py
./.venv/bin/ruff format --check \
  weft/commands/run.py \
  weft/commands/interactive.py \
  weft/core/tasks/interactive.py \
  tests/commands/test_run.py \
  tests/commands/test_interactive_client.py \
  tests/tasks/test_task_interactive.py
./.venv/bin/mypy \
  weft/commands/run.py \
  weft/commands/interactive.py \
  weft/core/tasks/interactive.py \
  --config-file pyproject.toml
```

Suppression policy:

```bash
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n0 \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py
```

Plan/spec/process hygiene:

```bash
./.venv/bin/python -m pytest -q -n0 \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
```

Backstitch after-report and keyed touched-surface comparison:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  > /tmp/weft-effort5-backstitch-after.json || test $? -eq 1
python3 - \
  /tmp/weft-effort5-backstitch-before.json \
  /tmp/weft-effort5-backstitch-after.json <<'PY'
from collections import Counter
import json
import sys

touched = {
    "docs/plans/2026-08-10-interactive-session-lifecycle-refactor-plan.md",
    "docs/plans/README.md",
    "docs/lessons.md",
    "docs/ruff-suppression-registry.md",
    "docs/specifications/01-Core_Components.md",
    "docs/specifications/04-SimpleBroker_Integration.md",
    "docs/specifications/05-Message_Flow_and_State.md",
    "docs/specifications/08-Testing_Strategy.md",
    "docs/specifications/10-CLI_Interface.md",
    "tests/cli/test_cli_run.py",
    "tests/commands/test_interactive_client.py",
    "tests/commands/test_run.py",
    "tests/specs/test_ruff_policy.py",
    "tests/tasks/test_task_interactive.py",
    "weft/commands/interactive.py",
    "weft/commands/run.py",
    "weft/core/tasks/interactive.py",
}


def keyed_issues(path: str) -> Counter[tuple[object, ...]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return Counter(
        (
            issue.get("severity"),
            issue.get("code"),
            issue.get("path"),
            issue.get("section_id"),
            issue.get("symbol"),
            issue.get("message"),
        )
        for issue in payload["issues"]
        if issue.get("severity") in {"error", "warning"}
        and issue.get("path") in touched
    )


added = keyed_issues(sys.argv[2]) - keyed_issues(sys.argv[1])
for issue, count in sorted(added.items(), key=repr):
    print(count, issue)
raise SystemExit(bool(added))
PY
```

The before report must use the exact same roots/options. Existing repository
debt may remain or resolve; the gate permits no new keyed error/warning
multiplicity on touched paths. If the sibling Backstitch checkout is absent,
record a tooling blocker rather than claiming metadata tests substitute for it.

Full repository gates before completion:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy \
  weft \
  bin \
  integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
uv lock --check
git diff --check
```

`bin/check-doc-paths` has an established advisory baseline of unrelated
dangling claims. Compare the exact keyed output rather than requiring a false
zero. All other failures must be investigated; do not label one pre-existing
without reproducing it at the baseline commit.

Runtime success is observable as: real piped interactive CLI completion,
canonical task-owned `ctrl_out` terminal proof, correct public output/error,
and no leaked client/log resource in the injected start-failure path.

## 16. Independent Review Loop

Plan review is required before implementation. Use a clean reviewer with the
plan, governing spec sections, target functions, tests, registry rows, and raw
score evidence. The reviewer must answer `PASS` or `BLOCKED` and explicitly
evaluate:

- whether every behavior change is one of E5-B1/B2/B3;
- whether any mechanism addresses a theoretical issue rather than current
  code;
- whether one private lifecycle owner is the smallest honest seam;
- whether a state machine or generalized session abstraction was invented
  without a current need;
- whether the plan is overengineered, score-driven, or brittle;
- whether the tests assert observable contracts rather than helper layout;
- whether retention remains available if locality worsens; and
- whether a zero-context engineer can implement the plan without making a
  design choice.

Implementation reviews occur after Task 2 and Task 4. Task 5 defines the
mandatory locality verdict and rework policy. A different-family outside-model
review is required again before closeout.

Findings are claims. Reproduce each before changing the plan or code. Accepted
findings update the mutable task text; declined findings receive a reasoned
disposition. A material scope, invariant, authority, or blast-radius change
requires plan re-review.

## 17. Evidence Log

| Date | Evidence | Result |
|---|---|---|
| 2026-08-10 | `git rev-parse HEAD` | authoring/spec baseline `78432dcacc997dacde7941048716fb10bfe9dfd5` |
| 2026-08-10 | raw Ruff C901 with `--ignore-noqa` over `run.py` and task interactive owner | `_run_interactive_session=58`; `_interactive_finalize_session=12` |
| 2026-08-10 | production `_interactive_terminal_envelope()` passed to `coerce_terminal_envelope()` | emitted no `source`/`timestamp`; strict reader returned `None` |
| 2026-08-10 | production `InteractiveStreamClient._handle_ctrl_message()` with `{"type":"terminal","status":"running"}` | completion became true with status `running` |
| 2026-08-10 | injected exact `client.start()` failure through `_run_interactive_session()` | exact failure propagated; client stop and log close counts were both zero |
| 2026-08-10 | focused interactive tests across command client, command owner, task owner, and CLI | current baseline command exited zero |
| 2026-08-10 | in-memory structural feasibility probe; no repository writes | allowed outer candidate 10; prompt owner 9; piped owner 4 |
| 2026-08-10 | red-first E5-B1/B2/B3 tests and restored mutation probes | producer lacked `source`; four invalid envelopes completed; start failure skipped cleanup; removing prompt `pre_run` timed out; removing KILL lost the required escalation assertion; all restored tests pass |
| 2026-08-10 | clean bug-fix review after Task 2 | `NET POSITIVE`; no findings; all three baseline regressions reproduced and fixed without a policy expansion |
| 2026-08-10 | clean locality review after Task 4 | `NET POSITIVE`; the single private owner is honest, prompt/piped paths remain direct, and no new generalized mechanism was introduced |
| 2026-08-10 | exact raw Ruff C901 gate after refactor | lifecycle owner and prompt/piped functions are absent; only the declared unrelated scores `13`, `13`, `16`, and task finalizer `12` remain |
| 2026-08-10 | suppression index/checker and policy tests | `RUFF-SUP-111` removed atomically; `C901=139`, groups `230`, directives `373`; `RUFF-SUP-332` retains one `BLE001` at `_InteractiveRunLifecycle._poll_monitor_terminal`; 84 tests pass |
| 2026-08-10 | focused command/client/task/CLI tests from current state | command interactive `10`, client `10`, task interactive `8`, and CLI `3` tests pass |
| 2026-08-10 | plan/spec hygiene, DOM-15 fixture, doc paths, and Backstitch | metadata/spec tests and DOM-15 pass; all 8 doc-path advisories reproduce at baseline; no new touched-path Backstitch error/warning multiplicity |
| 2026-08-10 | full current-state gates | default pytest `3670 passed, 3 skipped`; all markers `3671 passed, 14 skipped`; PostgreSQL `3610 passed, 12 skipped`; full mypy, Ruff, lock, and diff checks pass |
| 2026-08-10 | full-tree Ruff formatter plus baseline reproduction | same five unrelated files fail on current tree and baseline; every touched Python file passes targeted format checking |
| 2026-08-10 | final Claude Code outside-model, tool-less review over plan/spec excerpts/isolated diff/evidence | `PASS`; `NET POSITIVE`; no blocking contract, scope, brittleness, or locality finding |

Append implementation evidence only after rerunning it from current state.
Do not put uncommitted/staged/branch status in this table.

## 18. Deferred Design Analysis

These are not implementation tasks and do not authorize behavior changes.

| Proposal noticed during audit | Why deferred | Evidence/decision needed before a future plan |
|---|---|---|
| Tighten `coerce_terminal_envelope()` to require integer payload `timestamp` | [MF-3] requires the producer field, but shared readers currently use broker message IDs as observation time and several existing fixtures omit payload timestamp. Tightening is a cross-surface compatibility decision, not needed to fix this producer. | Inventory every producer/reader and mixed-version behavior; decide whether broker timestamp fallback remains supported; propose exact spec delta if compatibility changes. |
| Make `InteractiveStreamClient` a public context manager or general session API | Only one command owner needs the cleanup correction. A reusable lifecycle API would anticipate callers that do not exist. | At least one second real caller or repeated ownership defect, plus public API/spec analysis. |
| Aggregate or reprioritize failures from `client.stop()` and log-queue close | Current code has a fixed cleanup order but no demonstrated dual-failure incident or normative priority. Changing which exception wins is behavior. | Reproducible dual-failure problem, required outcome priority, privacy/diagnostic contract, and cleanup spec. |
| Replace interactive lifecycle with a reducer/state machine | No current race requires a new decision model. Existing state lives in task evidence and the queue client; the refactor is ownership extraction. | A demonstrated ordering bug that cannot be expressed with the current owner, plus a complete state/event table and independent owner approval. |
| Add PTY/TTY transport or a public `WeftClient.run(interactive=...)` API | Active [CLI-1.1.1] explicitly defines line IO and says prompt mode is not a public client surface. | Separate product/spec plan with transport, platform, cancellation, and compatibility decisions. |
| Unify interactive observation with result/follow/persistent waits | The earlier structural review found different consumption and completion semantics; no wholesale duplication exists. | New concrete duplication or defect evidence across at least two owners, not score or file size. |

## 19. Rework Queue

| Candidate | Reviewer finding | Preserved patch and SHA-256 | Bounded rework | Final disposition |
|---|---|---|---|---|

Rows are added only after an actual `NET NEGATIVE` verdict. Do not pre-populate
the queue with hypothetical work.

## 20. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

The log starts empty. Any behavior beyond E5-B1/B2/B3 is a deviation and blocks
continuation until it is either removed or handled through an explicit spec
revision. No row may remain with a pending proposal at completion.

## 21. Plan Review Record

Reviewed plan body through Section 20: SHA-256
`70f285437b9657e8a9b0c88bed69337effb98c6309e13458a9d740a8b8821aca` at code/spec
baseline `78432dcacc997dacde7941048716fb10bfe9dfd5`. Adding this record changes the
file checksum but not the reviewed implementation instructions.

| Date | Reviewer | Verdict | Findings and disposition |
|---|---|---|---|
| 2026-08-10 | clean repository/Python reviewer | `PASS` after correction | Initial review blocked watcher-lag-vacuous rejection tests and missing exact code docstring traceability. Re-review blocked an ambiguous raw-C901 gate and then stale `RUFF-SUP-332` raw-inventory wording. The plan now synchronizes on handler observation, asserts exact valid terminal state, requires exact docstrings, machine-checks residual C901 symbols/scores, and preserves the global raw inventory for a same-cardinality site move. Final re-review found no blocker and judged the lifecycle seam honest, bounded, and implementable without a design choice. |
| 2026-08-10 | Claude Opus 4.8 outside-model review, read-only repository tools | `PASS` after final live-text re-read | Confirmed E5-B1/B2/B3 are current bugs; rejected a state machine; accepted the single lifecycle owner as the smallest honest shared-frame seam; and found the tests, suppression paths, rollback, and gates sound. Nonblocking ownership/TID/site precision findings were incorporated. Because one concurrent response quoted stale `RUFF-SUP-332` text, a final explicit re-read quoted the corrected paragraph and confirmed that only the source directive and derived qualified symbol move while raw inventory and all cardinalities remain unchanged. No deferred design item was pulled forward. |
| 2026-08-10 | clean Python bug-fix reviewer after Task 2 | `NET POSITIVE` | Reproduced all three baseline failures, confirmed watcher synchronization was nonvacuous, and found no generalized policy or compatibility expansion. No finding required rework. |
| 2026-08-10 | clean Python locality reviewer after Task 4 | `NET POSITIVE` | Found the private lifecycle class honest and the reading path coherent. The note that `close()` also snapshots callback/history output was retained as the existing finally-order behavior; splitting it would add a seam without changing a contract. |
| 2026-08-10 | Claude Code outside-model, tool-less final diff review | `PASS`; `NET POSITIVE` | Confirmed only E5-B1/B2/B3 change behavior; no theoretical mechanism, state machine, public API, or brittle helper-layout test was added. The legacy-render synchronization note is intentional because E5-B2 requires that rendering to remain live. Accepted and corrected the cosmetic planned `RUFF-SUP-332` symbol drift to `_poll_monitor_terminal`. No blocking finding or deviation remains. |

Implementation, review, suppression disposition, traceability, and current-state
gates are complete. The owner authorized the atomic implementation commit on
2026-08-10.
