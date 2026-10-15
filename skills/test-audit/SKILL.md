---
name: test-audit
description: "Invoke whenever writing, changing, reviewing, or sweeping tests. Authoring gate for new tests, rules for changing failing tests, and audit workflow for low-value, implementation-coupled, or duplicative tests and the test-only production seams they demand."
---

# Test Audit

Status: Active — promoted 2026-10-15 per the `[DOM-14]` promotion tier from
multi-repo test-surface audits (`simplebroker` `208c3a8` whole-test-surface
audit across "2026-10-06-test-audit-remediation-plan" and its four lane
appendices; `weft` "2026-10-07-test-audit-repairs-plan" and
`test_test_audit_policy.py`; originally adapted from `openclaw/openclaw`
`80930af`).

Three modes, one value bar. **Authoring** gates every new or changed test at
write time. **Audit** runs focused passes for tests that re-assert source,
duplicate stronger proof, couple behavior to implementation, or keep test-only
production seams alive. **Sweep** prunes one whole subsystem's test surface.
Optimize for confidence, not deletion count.

This skill owns *judgment* about test value and the repeatable audit/sweep
procedure. `docs/agent-context/runbooks/testing-patterns.md` owns the standing
testing rules and patterns, and the repository owns *mechanics*: read its
`AGENTS.md` and `docs/agent-context/runbooks/testing-patterns.md` first, and
use its canonical test runner, parallelism, fixture, and CI rules wherever this
skill says "run tests". Repository rules win on any conflict.

## Governing References

- `docs/agent-context/runbooks/testing-patterns.md` — standing test rules
  (Rules 1–10) and patterns (Patterns 1–12 in this repo's numbering)
- `docs/agent-context/engineering-principles.md` — §7.5 (independent review),
  §4.1 (regression tests), §9 (enumerable contracts), and §11 (governed
  suppression registries)
- `docs/agent-context/decision-hierarchy.md` and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` —
  verification completion gate and independent review workflow
- `skills/call-agent/SKILL.md` — Tier-1 cross-family independent review for
  deleted coverage and preservation checks

## Which Sections Apply

| Mode | When | Read |
|---|---|---|
| Authoring | adding or changing one or a few tests | Value bar through Retention bar |
| Audit | a focused pass over existing tests | everything in this file |
| Sweep | pruning a whole subsystem's test surface | this file, then `skills/test-audit/SWEEP.md` |

## Value Bar

Tests justify their maintenance cost by protecting behavior, a credible
regression, or an independently meaningful contract. Apply the same
contract-based judgment to new tests and existing ones.

The *owner* of a contract is the production code responsible for it; its
*boundary* is the smallest interface through which that code's behavior is
observable without substituting it. "Owner boundary" throughout this skill
means that interface.

The one question behind every rule here: **if the claimed production behavior
were removed or deliberately broken, would this test still pass?** If yes, it
does not establish protection against that regression; it may still protect
other cases. Assess the gap rather than declaring the whole test worthless. The
[sensitivity check](#exercise-production-behavior) is how to answer it when
reading is not enough.

## Shape of a Good Test

A test makes one coherent behavioral claim. Its name states the behavior; its
setup is only what the claim needs; its assertions check observable results.
Retries, lifecycles, idempotency, and state machines may require several
actions to establish that one claim. Assert semantic properties, not brittle
counts or incidental ordering, unless the count or order is the contract. A
contract includes its failure modes: what the code rejects, and how, is as
much the claim as what it accepts. A reader should be able to tell from the
test alone what production behavior is promised and what change would break
it.

Tests must own and clean up their mutable state, pending work, and resources,
including on failure (`docs/agent-context/runbooks/hardening-plans.md` §13).
They must not depend on test execution order or uncontrolled time. Use explicit
synchronization or module-owned clock seams when timing matters
(`docs/agent-context/runbooks/testing-patterns.md` Pattern 12), so passing runs
and sensitivity checks are reproducible.

## Authoring Gate

Before adding any test, answer four questions; a missing answer means do not
add it yet:

1. What observable behavior, invariant, or independent contract does it protect?
2. What credible regression makes it fail?
3. Why does existing coverage not already catch that failure? Prefer the
   smallest reliable boundary that proves the claim. Another layer needs its
   own distinct risk, such as wiring, boundary straddling, or real dependency
   semantics (`docs/agent-context/runbooks/testing-patterns.md` Rule 9). A
   broader test is not stronger merely because it traverses more code. Prefer
   extending a table-driven case or shared fixture over a near-duplicate test
   when the cases share a contract. Share setup when it improves clarity
   without coupling unrelated cases.
4. Does it need a production seam (export, flag, wrapper, injection hook,
   test-environment branch) that exists only for testing? Prefer the real
   boundary. A seam is justified when it controls a real dependency, such as
   time, randomness, or external failures, without bypassing the production
   behavior under test. Explain that need; do not expose private logic merely
   to assert its implementation.

Make the gate reasoning clear in the test and review context; explain
non-obvious choices explicitly. Use concise comments where they preserve
useful context, such as a historical edge case, protocol requirement, or
required concurrency interleaving. No fixed comment length or reporting
location is required.

Then check the test against every [junk pattern](#junk-patterns); a match fails
the gate unless the [retention bar](#retention-bar) names the contract it
independently guards. If a behavior-preserving refactor would require changing
the assertion or expected result, check for implementation coupling. Updating
an import or setup alone is not evidence of a bad test. Rewrite coupled
assertions unless they protect an independent contract in the retention bar.

Bug regression tests must fail on the pre-fix code for the intended reason and
pass after the owner-boundary repair. Demonstrate it with the
[sensitivity check](#exercise-production-behavior), using the pre-fix behavior
as the control; without that demonstration, its ability to catch the
regression is unverified. Start at the owner boundary; add another layer only
for a distinct risk, such as proving that the real system invokes the rule. Do
not replay the same scenario through mocks at every layer it crosses.

## Changing a Failing Test

When an existing test fails, decide first whether the test or the product is
wrong. Never update an expected value or loosen an assertion to match new
output until you have established, from the contract rather than the output,
that the new behavior is correct. Record which one you concluded and why.

Diagnose failures to root cause. Changes to parallelism, serialization, or
timeouts can help isolate a cause; they are not by themselves a repair
(`docs/agent-context/runbooks/testing-patterns.md` Pattern 1). A failure under
contention or load may expose a product defect, poor test isolation, or an
incorrect harness budget. Permit measured harness corrections for supported
environments while preserving the product's timing contract and the behavior
being asserted. Verify the correction with the repository's required runner
settings. Do not use retries or relaxed limits merely to hide an unexplained
failure.

When a test fails only because a suppression registry needs an entry (a
warning allowlist, lint ignore list, known-failure baseline, ratchet file, or
governed suppression registry per `docs/agent-context/engineering-principles.md`
§11), adding the entry is not the default. Make an explicit attempt to
refactor the code so the suppression is unnecessary. Then have an independent
judgment — a reviewer, or a fresh assessment not anchored on the attempt —
choose among the original with the suppression, the refactor, or a second
refactor attempt, weighing maintainability, simplicity, and locality of the
code. Record the choice and its reasoning.

## Exercise Production Behavior

Execute the actual production code responsible for the claim. Substitute
dependencies outside that responsibility; never replace the behavior being
verified. A fake store may prove how a service uses storage, but cannot prove
the real adapter's persistence or transaction semantics. For static contracts
allowed by the retention bar, inspect the actual production artifact instead.

Trace each assertion to a result or effect produced by that production path.
Setup checks, assertions against a stub itself or its configuration without
exercising the production behavior, and a test-local reconstruction of the
algorithm do not prove production behavior. For concurrency, lock contention,
or idle convergence claims, do not treat thread startup, `sleep`, or post-hoc
state as proof that the contended or idle decision path ran: require an
explicit dependency witness that the contended or idle branch was entered
before releasing the gate, while keeping the real operation owner unmocked.
Comparing real production output with dependency-supplied data is valid when
forwarding that data is the independently specified contract. Expected results
or asserted relationships must come from an independent contract or oracle, not
be copied from the implementation under test
(`docs/agent-context/runbooks/testing-patterns.md` Rule 10). Property and
metamorphic tests may compare production outputs across related inputs when the
relationship is independently specified. A round-trip test alone does not prove
interoperability: matching encoder and decoder bugs may cancel out; use
independent examples or a reference implementation for that claim. Reusable
test infrastructure may have its own tests when that infrastructure is
explicitly the subject; those tests do not count as coverage of the product it
supports.

**The sensitivity check.** Wherever this skill says to prove a test can fail,
this is the procedure, and it is defined only here:

- The control is either the pre-fix production behavior or one focused
  mutation of the production owner. Either is sufficient, except where a bug
  regression specifically requires the pre-fix behavior. Reuse recorded
  evidence only for the relevant test or parameterized case when its
  assertions, inputs, production path, and execution conditions remain
  materially unchanged. Evidence from another test of the same contract does
  not establish this test's sensitivity. If a relevant change invalidates the
  evidence, rerun that control, not unrelated validation.
- Run the test against the control and record the intended failure. An
  unrelated compile error or setup failure does not establish sensitivity.
  For a compile-only compatibility test, the specific consumer type or import
  error caused by breaking the promised API can be the intended failure.
- Restore the original exactly. Use an isolated checkout or a narrowly
  reversible change that preserves unrelated edits, the exact original file
  contents, and Git state, including existing uncommitted work. Do not broadly
  stash or discard working changes. Verify the diff is unchanged afterward.
- Confirm the restored candidate passes. Never commit a control.

## Negative Assertions

Prefer asserting the required result or state. Add an assertion that something
did not occur only when its absence is necessary to correctness, such as no
unauthorized write, duplicate charge, or callback after disposal. Name the
forbidden effect and why the positive assertions alone cannot establish the
contract; incidental absence of an internal call is not a contract.

Show that execution reached the relevant decision and had an opportunity to
produce the forbidden effect. Use a positive control or the sensitivity check
when reachability is uncertain. For asynchronous work, observe through
completion, controlled scheduler advancement, or the contract's time bound. An
immediate "not called" check or arbitrary sleep does not prove absence over
the required interval.

## Junk Patterns

The shared checklist for every mode: the authoring gate rejects a new test that
matches one, and audits hunt for existing tests that do.

**Does not prove production behavior**

- assertion-free coverage probes;
- assertions that compare a value to itself or to a copy made in the test;
- assertions that verify only test setup, stub configuration, or a test-local
  copy of production logic;
- expected values copied from the helper or renderer under test without an
  independently specified property or relationship;
- snapshots or golden files regenerated to make the suite pass, rather than
  reviewed as a change to a contract artifact;
- mocks that implement the asserted behavior, or one identical mock standing in
  for different APIs;
- fixtures that hand the code under test the result it is supposed to compute,
  or persistence asserted against a store the path never writes;
- thread startup, arbitrary `sleep`, or post-hoc state treated as proof of lock
  contention or idle convergence without witnessing entry into the contended or
  idle decision branch;
- exact source, import, or string greps (see the retention bar for the narrow
  exception).

**Duplicates stronger proof**

- duplicate invocations of the same contract;
- local replays of a shared helper's tests in each consumer;
- tests of a private helper's return value or call arguments when a real
  boundary already detects the same failures;
- copied fixtures, inventories, manifests, or export lists.

**Exists to preserve something other than a contract**

- tests whose only purpose is preserving test-only exports, globals, or
  wrappers;
- dead production code whose only callers are tests;
- tests that assert a declared capability or config value instead of the
  behavior it promises;
- tests that freeze temporary plans, task checklists, file layouts, or review
  rituals without an independent correctness requirement.

**Misleading**

- negative controls that pass for an unrelated reason, such as a denial from a
  different guard or a rejection the production path never reaches;
- negative assertions with no correctness requirement, or checks made before
  the forbidden effect could occur;
- names or fixtures that promise more than the input exercises, such as a
  "clears the cache" test asserting the cache was *not* cleared;
- tests skipped, marked expected-failure, or wrapped in retries without a
  linked defect and a removal condition; a flaky test is a bug report, not a
  retry candidate.

## Retention Bar

Keep a test when it independently enforces a promise made to someone outside
the module — a caller, a stored artifact, a wire format, an operator, a
migration path. Compiler or type-check coverage makes a test redundant only
when an existing required check already detects the same failure. A library
can compile while breaking consumers; a compile-only consumer fixture may be
the only compatibility proof. Also keep:

- call ordering when order is observable behavior;
- regressions with a credible failure mode;
- source inspection when it is the cheapest independent guard: it fails when
  the contract changes (the user-facing key, byte, or path) and survives an
  identifier-only refactor.

A retained test that fails on the baseline is a possible product bug:
reproduce it, and repair the owner when within scope or report the defect; do
not delete its proof to obtain a passing suite.

Static or slow is not a deletion reason. A test that resembles implementation
may still be the independent contract; prove otherwise before removing it.
An architecture or process label is not sufficient justification: identify
the requirement and a concrete failure it prevents. A package path promised
to consumers can be a contract; today's internal file layout usually is not.

## Candidate Evidence

Keep discovery read-only and report evidence before editing. For broad scope,
split the work into parallel read-only lanes along production owner boundaries
(not file prefixes), plus one cross-cutting junk-pattern pass. Outside a
subsystem sweep, prefer a few high-confidence candidates over a large
speculative inventory.

Before judging a candidate, read the complete test and its production owner.
Follow callers, callees, siblings, overlapping tests, CI routing, and history
as needed to establish its purpose and the consequences of changing it. When
the test claims dependency-backed behavior, inspect the relevant dependency
source or types directly; types alone do not establish runtime semantics.

Record enough evidence to justify each deletion before editing. For a small
change, a concise explanation can cover the fields below; use a ledger for
sweeps. Mark unavailable history or inapplicable fields as unknown or not
applicable, with the consequence for the decision. Do not invent certainty. If
uncertainty could hide the only proof of a contract, retain the test pending
investigation.

- exact test name and location;
- what failure it can actually detect;
- non-test callers of the covered production or support seam;
- stronger remaining owner-boundary proof, or why no proof is needed —
  "stronger" means it detects the specific failure being removed, not merely
  that it executes the same code or sits at a broader layer; compare inputs,
  assertions, and reachable failure paths, and when equivalence is uncertain
  apply the sensitivity check to the proposed keeper before deletion;
- relevant history and the reason the test or seam exists;
- production or test-support deletion unlocked;
- risk and the focused validation command.

## Edit Shape

Choose one coherent owner-boundary batch. Preserve justified dependency seams
under the authoring gate. Delete obsolete test-only exports, globals, wrappers,
and dead production paths instead of preserving aliases. Move retained
regressions to their canonical owners. Consolidate repeated package or
dependency assertions into one generic contract.

Judge the result by preserved confidence and reduced maintenance burden.
Line counts describe the change; they are not an optimization target. Do not
add replacement tests that restate the same implementation, and do not
convert uncertain candidates into cleanup to increase deletion counts.

## Validation

Do not edit source or tests while a test run is in flight in the same
checkout.

1. Run the smallest owner and sibling tests through the repository's canonical
   runner, at its default parallelism.
2. For removed source greps or plan assertions, run the executable script or
   dry-run that owns the real contract.
3. Run the repository's formatter and linter on changed files, then
   `git diff --check`.
4. Run whatever broader changed-files or CI gate repository policy requires
   (`docs/agent-context/decision-hierarchy.md`).
5. For audits that delete coverage, get an independent review of what was
   removed before claiming completion
   (`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`,
   `skills/call-agent/SKILL.md`): a reviewer compares the deleted tests against
   the remaining ones, looking for contracts that lost their only proof and for
   new assertions that cannot fail. Apply the sensitivity check to each restored
   contract.

Commit, push, or open a PR only when authorized, following repository policy.
Land one coherent batch at a time; after landing, rerun read-only discovery for
the next high-confidence batch.

## Handoff

Report:

- root cause and removed low-value categories;
- production owner simplifications;
- retained false positives and why they remain valuable;
- focused and full proof actually run, including every sensitivity-check
  control and its recorded failure;
- test-or-product decisions made on failing tests, and any suppression-registry
  choices with their reasoning;
- commit/PR state and named follow-ups.

Sweeps add the items in `skills/test-audit/SWEEP.md`.
