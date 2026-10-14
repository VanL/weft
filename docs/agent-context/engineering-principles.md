# Weft Engineering Principles

These are the repo-specific engineering rules that come up repeatedly when
changing Weft. They are short on purpose: each principle should be actionable
during a single implementation session.

## 1. Extend the Existing Durable Spine

The canonical execution path is:

`TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`

If a change touches execution, timeout handling, reservation semantics, result
delivery, or observability, start by extending that path. Do not introduce a
second execution path or side channel unless the relevant spec explicitly
requires it.

## 1.5 Keep Weft as the Substrate

Weft is the durable task-runner substrate, not the higher-level agent
management or orchestration layer.

When a feature starts trying to predict agent readiness, hide orchestration
policy in `weft run`, or add proactive health-management behavior before real
execution, stop and re-check the boundary. In Weft, the right default is to
make task submission and execution reliable, observable, and easy to validate
explicitly. Higher-level intelligence belongs above this layer.

Agent support and limited runners still belong here. An agent is a task. A
dangerous task in a restricted container is still a task. Runtime-specific
knowledge is acceptable when it directly serves execution, isolation,
observability, or clear failure on the normal durable spine.

## 2. Queues Are the Canonical State

Weft is evented through queues, not through an auxiliary database layer.
Observable task behavior should be proven through queue messages, task logs,
reserved-queue outcomes, and task outboxes.

When a feature needs durable state, first ask whether the existing queue model
already expresses it. Avoid ad hoc state stores that compete with queue truth.

## 3. Canonicalize at Boundaries, Then Stay Strict

Normalize CLI or TaskSpec input once, at the boundary, through one shared
validation path. After that, internal code should operate on the canonical
form.

When a contract changes, update forward. Do not leave permanent runtime
fallback readers that accept multiple incompatible shapes unless the spec
explicitly requires compatibility behavior.

For TaskSpec specifically, keep the template/resolved boundary explicit:
templates may stay partial, but resolved tasks must go through the shared
resolution path before construction and then remain frozen on `tid`, `spec`,
and `io`.

Thread resolved configuration and context objects explicitly into internal
consumers — never re-resolve defaults from ambient environment variables, CWD,
or module globals inside an inner helper after an outer entry point has already
bound a config or context. When a convenience wrapper delegates to a shared
runner, test the wrapper with a non-default resolved configuration and assert the
inner runner observes it.

## 4. Real Broker and Process Tests Beat Mock-Heavy Tests

For core lifecycle behavior, use the real system:

- `WeftTestHarness` for isolated end-to-end and CLI flows
- `broker_env` for real SQLite-backed queues
- `task_factory` or real `Consumer` / `TaskRunner` code paths

Avoid mock-only tests for:

- queue reservations
- manager/task lifecycle
- timeout/resource behavior
- task state transitions
- final result delivery

Mock only boundaries that are genuinely external or nondeterministic. When
simulated clocks or platform hooks are needed in tests, patch module-owned
callables (`_monotonic`, `_sleep`, `_getpid`) rather than shared standard-library
modules (`time.monotonic`, `os.getpid`, `sys.platform`) that leak across threads
and parallel workers.

## 4.1 Prove the Problem with a Failing Test First

Where principle 4 is about *what* to keep real, this is about *when and why* you
write the test.

Write a failing test that proves the problem exists, watch it fail, then make it
pass. If you cannot write the failing test, you do not understand the problem
well enough to fix it. If something is hard to test, that is information about
the design, not permission to skip the test. Generate fixtures through
production code paths (`task_factory`, real `Consumer` / `TaskRunner` flows), not
synthesis. When a migration or compatibility path claims to support artifacts
written by an earlier release, capture at least one fixture from that release
lineage (or its exact schema/wire contract) and assert both the unmigrated read
path and the upgrade path against it; when asserting wire, serialization, or
hash contracts, compare against an independent literal rather than recomputing
the expected value through the same helper (`f(x) == f(x)` is a tautology that
passes when the wire format breaks).

The sanctioned exit is `docs/agent-context/runbooks/testing-patterns.md` Rule 5, and it
is loud, concrete, and falsifiable — never silent. A valid substitute
**always demonstrates the post-change correction**, and additionally
either demonstrates the pre-change failure (or its root cause) or
states why pre-change observation is impossible — never neither half.
Category labels are not reasons: a docs-only change with a
reproducible check available (a link check, a grep gate, a
traceability run) has its failing test and must use it; a broken
verification harness is a blocker to fix, not an exception to claim.
Invoking Rule 5 without the demonstration is skipping the test, and
skipping the test is not permitted at any task class.

## 4.2 Update All Consumers in the Same Change

When you rename a queue, tighten a TaskSpec or result schema, or change any
shared contract, update every producer and consumer in the same change. A
partial rename passes isolated checks and fails at runtime; the synchronized
update is the fix, not a follow-up. This is the change-side companion to
principle 3's "update forward" rule.

Before declaring any surface "unused" or "dead" and removing it, run a
repository-wide search across `examples/`, standalone scripts, templates, and
vendored or bootstrap consumers — not only the main package and `tests/`.

## 4.3 Contention Is a Theory-Possession Stop Condition

If you want to reduce contention to make tests pass — or if a change requires
repeated rounds of contention, timeout, or retry-budget tuning — stop corrective
work and acceptance reruns immediately. Preserve the failing command and load,
re-read program theory [WT-7] and [ALT-WT-017], and identify the causal gap
between the model's prediction and the evidence before choosing a correction.
"Flaky", "shared resource", and "pre-existing" are not explanations.

Follow the canonical rule in `docs/specifications/08-Testing_Strategy.md`
[TS-0] and the procedure in testing-patterns Pattern 1. A later, explicitly
hypothesis-driven lower-load run is diagnostic only. Acceptance must establish
the justified contract under the original pressure and standard concurrency.

## 5. Read Specs and Code Before Inference

Do not infer behavior from module names or mental models alone. Read:

1. the program theory when product scope, concepts, or ownership are at issue,
2. the relevant spec,
3. the current implementation and its rationale,
4. the closest existing test,
5. the active plan or implementation note when one exists, then decide what to
   change.

This is especially important for Weft because details such as reserved queue
policy, runtime-only queues, and TaskSpec immutability are easy to get almost
right while still breaking invariants.

## 6. Keep Traceability Bidirectional

Treat documentation traceability as part of the implementation, not as optional
cleanup.

- Theory-changing plans cite the governing theory and decision records;
  behavior-changing plans cite the exact spec section(s) and reference code(s)
  the work implements.
- When implementing a plan, update the touched spec with a backlink to that
  plan and refresh any nearby implementation snapshot/status/mapping notes that
  describe current code ownership or behavior.
- When changing code at a spec-owned boundary, keep module and function
  docstrings pointing back to the governing spec sections so the code-to-spec
  path stays explicit in both directions.
- Program theory can constrain a contract or architecture, but it cannot
  silently replace exact behavior or concrete realization rationale.
- When a plan changes intended behavior, exact proposed spec sections live
  in the plan for review; the **spec-promotion slice** applies them to
  `docs/specifications/` per a named strategy (in-file text-first, atomic,
  new file under an in-flight classification, or spec-authoring only). Prose
  status and backstitch's machine classification are different mechanisms.
  After promotion, `docs/specifications/` is the single governing contract —
  not plan appendix text. Spec text
  states contracts as rules (the property every instance satisfies), not
  only examples; a codifying delta is verified rule-vs-code before
  promotion. See `docs/agent-context/runbooks/writing-plans.md` §4b–4d.

## 7. Boundary-First Risky Plans

For risky or boundary-crossing work, name what must not change before breaking
the work into tasks.

For Weft this usually means spelling out:

- whether the change must stay on the existing
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine
- which queue names, state transitions, or result payloads must remain stable
- whether the template/resolved `TaskSpec` boundary is changing
- whether runtime-only `weft.state.*` queues must stay out of persistence or
  history features
- what should stay real in tests instead of being mocked away
- what rollback, rollout order, or post-deploy observation is required

If a risky plan cannot say these clearly, it is not ready for implementation.

## 7.5 Use Independent Review to Reduce Author Blindness

Self-review catches local mistakes, but the authoring agent is still biased
toward its own assumptions. Non-trivial plans and implementations should get a
review from a different agent family via `skills/call-agent/SKILL.md` (Tier 1).
Native harness subagents inherit the authoring model family and share its blind
spots; they are permitted for review only as a Tier-2 fallback after two
bounded, logged Tier-1 attempts fail, and must be recorded as `same-family
fallback (degraded)` rather than presented as cross-family review (see
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`).

For review to be more than ceremony, the reviewer must verify three things
against the live tree rather than taking the plan's prose on trust:

1. **Causal premise in code** — trace the plan's root-cause or architectural
   claim through the actual functions and callers it names. If the code already
   handles the case or the bug is in a different layer, reject the premise.
2. **Falsifiable proof** — check that each planned test or verification command
   would actually fail if the corresponding slice were reverted or broken by a
   one-line mutation, and that it exercises the real boundary rather than a
   tautological helper call (`f(x) == f(x)`).
3. **Empirical identifiers by command** — verify file paths, symbols, spec
   codes, commit SHAs, and numeric counts by inspection, not plausibility.

Every review finding must receive an explicit disposition (`accept`, `modify`,
or `reject` with rationale), and any finding marked `fixed in plan` must modify
the operative plan body (tasks, invariants, file list, or verification block) in
the same revision — recording a fix only inside the review log while leaving the
task steps unchanged is an unapplied disposition. When a review finding or new
proposal recommends reversing a standing invariant, spec rule, or refused
alternative (`[ALT-*]`), surface `RECONSIDERATION REQUESTED` and stop for human
decision rather than absorbing it as a normal edit. And remember that **status
documents are claims, not evidence**: a reviewer or implementer who reads a
ledger or plan table and reports its status as fact without checking the files or
running the command has committed a verifier error.

## Secondary Rules

- Use `build_context()` and `WeftContext` instead of re-implementing project
  discovery or broker-target resolution.
- Use generator-based queue history reads for append-only queues instead of
  correctness-critical `peek_many(limit=...)` calls.
- Reuse task/watcher queue handles on live runtime paths instead of opening new
  queue connections casually.
- Check blast radius before editing shared contracts such as queue names,
  TaskSpec schema, CLI output shape, or result payloads.
- Document plans for zero-context engineers: files to read, files to change,
  invariants to protect, and exact verification commands.
- Prefer explicit spec-section references such as `[MF-2]` or `[CLI-1.1.1]`
  over broad document-only references when tying a change to docs.
- Prefer explicit rejection over silently ignoring unsupported fields or modes.
- Keep runtime executability checks opt-in. Do not turn `weft run`,
  manager submission, or ordinary task startup into a speculative "can this
  binary run here" gate; explicit validation or diagnostic surfaces own that,
  and the normal execution path should attempt the real run and report the
  concrete startup failure. Hidden probes pull Weft upward into agent
  management, which is outside this layer's job.
- Keep future-proofing out unless the current spec requires it.

## 8. Fit Test For Agent And Runner Features

When adding agent or runner features, ask:

1. Is this about running, constraining, observing, controlling, or composing a
   task?
2. Does it stay on the canonical durable spine?
3. Is any runtime-specific knowledge required for execution, or is it really
   ecosystem management?
4. If it is convenience-only, can it remain explicit, optional, and
   non-authoritative?
5. Does it silently write policy, cache truth, or create a second control
   plane?

Good fits:

- agent runtimes that preserve queue and task semantics
- restricted runners for dangerous work
- explicit runtime preparation for a real slow path
- explicit discovery helpers that report what is available

Bad fits:

- hidden preflight in `weft run`
- background provider probing or image prep
- health caches treated as startup truth
- automatic mutation of project config without an explicit documented contract
- provider-specific management features that grow faster than the core task
  model

## 9. Enumerable Contracts Get Executable Gates

Any list a document asserts — exit codes, queue names, config keys, CLI
flags, control message types, listed edge cases — must be mirrored by a
machine check that enumerates it: a firing test per element, a no-op
prevention test per behavior-affecting key.

Prose binds only what gets checked. Given identical written guidance, agents
comply uniformly with automated gates and unevenly with everything else — so
a contract element without a gate is a contract element that will silently
diverge. A declared element with no firing test is an untested contract and a
verification failure, not a style nit. (See testing-patterns Pattern 9 and
`docs/agent-context/runbooks/adversarial-acceptance-probes.md`.) The same rule
applies to numeric counts and completeness claims (`all`, `every`, `full`,
`zero`): derive them from a command output in the current session, and when a
plan or gate says "full suite", make "full" true across every test root rather
than weakening the wording to "core".

This is the gate-side companion to principle 4.2: updating all consumers keeps
a contract synchronized; enumerating it in a machine check keeps it honest.

The principle applies to the guidance corpus itself, not only to product
contracts. Documentation gates in this repo:

- `tests/specs/test_plan_metadata.py` — the plan-status contract (closed
  vocabulary, normalized metadata block, index/file agreement).
- `tests/specs/test_spec_hygiene.py` — spec/code traceability hygiene.
- `bin/check-dom15-fixtures` — the task-classification fixture table.
- `bin/coalesce-check` — the coalescing layer's retrieval contract: every
  run-log SHA and `git show <sha>:<path>` cue resolves, and pins absent
  from published history are reported as `local-only pin`.
- `bin/check-doc-paths` — every backticked repo-relative path claim in
  prose across the guidance surfaces resolves (or is listed in
  `DECLARED_FUTURE_ARTIFACTS` with stale-entry fail-closed enforcement),
  plus `--self-test` positive and negative probes.

**Gates do not gate gates.** A gate script verifies its own negative and
positive detection paths via an embedded `--self-test` flag or a dedicated test
in `tests/specs/`, never by spawning a meta-gate to watch the gate. That is
the recursion floor that keeps governance share bounded.

## 10. Variation Is Declared; Deficiency Is Gated

Plans bend on contact with reality, and different pressures produce
legitimately different designs. Do not build guardrails that force
convergence; build floors that catch deficiency on any path:

- record the baseline (spec version, contract SHA) the work was built against
- log deviations from that baseline where a reviewer will find them —
  deviation is legitimate, undeclared deviation is not
- hold every result, regardless of design, to the invariant floors: no
  crash or traceback reaches a user, exit codes and error messages tell the
  truth, the advertised default invocation works, declared contracts have
  firing tests, and the work's own status claims survive a rerun

Divergence between attempts is often productive — harvest it. Deficiency is
the failure mode, and it is orthogonal to which design was chosen. (See
`docs/agent-context/runbooks/adversarial-acceptance-probes.md` for the floor
probes and `docs/agent-context/runbooks/writing-plans.md` for the deviation
log.)

## 11. Cohesion Over File Size (Floors, Not Line Counts)

Large cohesive files are deliberate, not neglected debt. Do not propose or
perform a file split on size grounds alone, and do not treat file size by
itself as a review finding. This is the principle behind the "line count =
god class" warning in AGENTS.md §1.1: generic heuristics point the wrong
direction here.

Why: agents navigate by grep and read by offset; a big well-named file is a
pre-joined index. Every module boundary is a place an agent must correctly
guess that relevant code lives elsewhere — agents miss at boundaries far more
often than they miss things in front of them (lazy imports and indirection
are invisible walls). Splitting genuinely coupled code manufactures false
seams, and false seams breed parallel-implementation drift.

Two floors (violating a floor IS a finding, however small the file):

1. Every implicit coupling gets an explicit marker at the edit point — a
   blast-radius comment, invariant note, or an enforcing helper for groups
   that must change together. An agent should never need to already know the
   file to edit it safely. (This is the file-local form of the Secondary
   Rules blast-radius check.)
2. Every state machine gets a name and a contract test — live runtime
   coupling (queue reservation timing, reducer decision ordering, control
   signal precedence) must be a named unit with its own firing test.
   Unnamed state machines cannot be contract-tested and silently diverge.

Distinguish the two kinds of coupling:

- **Structural coupling** — a wide flat method surface sharing one schema —
  is safe at any size under floor 1. `weft/core/manager.py` (~6,900 lines)
  and `weft/core/monitor/task_monitor.py` (~6,200 lines) are this: many
  methods over one queue/state contract, kept together on purpose.
- **Behavioral coupling** — pieces interacting through live state — is where
  floor 2 applies, and extraction is justified to create the testable
  boundary, not to shrink the file. `weft/core/task_lifecycle.py` plus
  `weft/core/state_machines.py` (contract-tested in
  `tests/core/test_state_machines.py`) is the repo's model case: the task
  status state machine was extracted into a named, table-tested unit while
  the manager stayed large.

Static complexity scores (`C901`, branch/statement counts) are **visibility
signals**, not automatic refactor commands. When a function crosses a
complexity threshold, either extract a genuinely reusable domain phase or
record a governed suppression under `docs/specifications/08-Testing_Strategy.md`
`[TS-3.1]` (checked by `bin/ruff_suppression_index.py` and
`tests/specs/test_ruff_policy.py`) with a rationale explaining why the dispatch
or validation table belongs together — do not scatter single-caller private
helpers across a module just to make a counter drop. Governance tools and
policy checkers may never exempt or suppress their own policy check on
themselves (registered lint exceptions under
`docs/specifications/08-Testing_Strategy.md` `[TS-3.1]` remain governed by the
suppression-index gate), and tests for linter or policy gates must run against
the repository's real configuration (not `--isolated`).

Cost to price in: a hot god file serializes parallel agent write-slices —
keep fan-out write scopes disjoint or sequence them.

## 12. Coalesce on Events, Not Time; Every Fold Keeps a Cue

Ledgers and plan directories are moment streams: raw, dated, append-only
in spirit. Left alone they grow until agents stop reading them. The fix is
periodic coalescing — distill cold entries into rules, harvest and retire
completed plans, promote recurring workflows to skills — governed by three
rules borrowed from tiered-memory design:

- **Trigger on accumulation, not on the calendar.** Repos have different
  pulse rates; event counts derived from a watermark scale automatically.
  A stored counter is state that drifts; a derived count is always honest.
- **Keep the recent tier verbatim.** Never summarize young, hot, or
  still-cited entries — that destroys exactly the detail the next session
  needs. Fold only what is cold and stable.
- **A summary that cannot lead back to its constituents is broken, even if
  it reads well.** Every fold leaves a date range and commit SHA in the
  surviving line. Git is the archive; the cue is what makes the archive
  reachable.

Promotion and decay are citation-driven, not vibes-driven: an entry cited
by later plans and reviews is promotion evidence; an uncited entry whose
subject has churned is decay evidence. Presence in the always-read context
is not evidence of usefulness — only explicit citation in work products
counts. Golden rules and safety invariants carry an importance floor and
never decay. Contradiction is resolved by editing the rule in place — with
a revision marker `(revised YYYY-MM-DD; was: <gist>)` when the meaning
changes, so citations to the rule stay interpretable across history — and
the old version lives in git.

## Warning Signs

Sessions usually go sideways when one of these happens:

- a second code path appears instead of extending the canonical one
- a change relies on intuition rather than reading the relevant spec/test/code
- the failing test is skipped on a non-trivial bug
- a concurrency or contention failure is "fixed" by lowering `-n`, padding a
  sleep/timeout, or adding a retry loop without naming the causal gap in the
  owning contract
- a same-family native subagent is used to "independently" validate a flawed
  plan premise without attempting cross-family review
- a change is labeled "pre-existing" without proof
- a regression is called "pre-existing" without running it on the base branch
  to prove it
- a later stage starts re-deriving facts that an earlier stage already owns
- a file is split on size grounds, or a cohesive dispatch function is broken
  into single-caller helpers just to satisfy a static complexity score

## The Meta-Principle: Compound Knowledge

Every rule above is an instance of one idea: each unit of engineering work
should make the next one easier. A shared boundary converter means the next
agent does not re-derive the canonical form. A blast-radius note means the next
change knows its impact zone. A failing test means the next debugging session
starts from known-good. A lesson written down means the next session does not
repeat the mistake. Treat the guidance docs, `docs/lessons.md`, and explicit
plan boundaries as compound knowledge — maintain them so Weft gets easier to
work on correctly over time.
