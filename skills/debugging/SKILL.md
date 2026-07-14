# Debugging: Root Cause Before Fixes

Status: Active (adopted from agent-guidance @ 2f7eff6 via
`docs/plans/2026-07-14-agent-guidance-propagation-plan.md`). Distilled from the root-cause-first discipline in
superpowers:systematic-debugging, adapted to this repository's proof and
replanning gates. Either may be invoked; this skill is self-sufficient
without the external suite.

## Purpose

Stop symptom-patching. Every fix flows from a demonstrated root cause and
lands with a proof that the failure cannot silently return.

## When To Use

- Any bug report, test failure, or unexpected behavior — before proposing
  a fix. Crashes, corruption, and regressions are bugs whether or not a
  governing spec exists (`Source spec: None — bug fix` is a valid plan
  line). Diagnose first; if the diagnosis reveals the *intended* behavior
  was never decided, that is additionally a spec gap (write the missing spec per
  `docs/specifications/README.md`) — classify
  it after root cause, not instead of diagnosis.

## Governing Spec References

- the completion gate in `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/engineering-principles.md` §4.1 (failing test
  first — this repo's numbering) and the read-before-inference rule in
  `docs/agent-context/principles.md`
- `docs/agent-context/runbooks/testing-patterns.md` Rule 5 (the only
  sanctioned substitute-proof path) and Pattern 3 (name the regression)
- `docs/lessons.md` Golden Rule 11 (handle the error path)

## Read First

- The governing spec section for the affected behavior, if one exists
- The nearest existing test for the affected path
- `docs/agent-context/runbooks/testing-patterns.md` (harness selection,
  failure patterns)

## Blast Radius

- The fix itself, its regression test, and any contract consumers the
  root cause implicates (Golden Rule 5: update all consumers together)
- If the root cause is a spec-vs-code disagreement, the deviation path in
  `docs/agent-context/decision-hierarchy.md` applies — do not
  silently "fix" code to match your inference

## Workflow

1. **Reproduce first.** Run the failing case and capture the actual
   output. No reproduction → no fix. If reproduction is genuinely
   impractical (nondeterministic race, production-only state), name the
   substitute evidence explicitly: a characterization probe, a log trace,
   or an inspection argument — and say why reproduction was impractical.
2. **Read before inferring**: the spec, the implementation, the
   closest test, the active plan. A fix based on a mental model of code
   you have not read is a guess.
3. **Isolate the root cause.** Narrow until you can state, in one
   sentence, the mechanism: "X happens because Y does Z under condition
   W." Narrow by method, not vibes: trace backward from the failure site
   to the first bad value; diff a good case against the bad case (input,
   environment, version); form a falsifiable hypothesis and change one
   variable per experiment; collect evidence at component boundaries to
   decide which side owns the defect. A fix proposed before the
   one-sentence mechanism exists is a symptom patch.
4. **Check the boundary, not just the site.** Ask whether the root cause
   is an instance of a class: a missing canonicalization (Golden Rule 1),
   a partial contract update (Rule 5), an unhandled error path (Rule 11).
   Fix the class where cheap; at minimum name it.
5. **Write the failing test, watch it fail** (§4.1 here), then fix, then watch
   it pass. Generate fixtures through production code paths. If the test
   is hard to write, that is design information — record it, don't skip.
   The only sanctioned alternative is testing-patterns Rule 5: name what
   replaced red-green and why. This is a separate decision from step 1's
   reproduction exception — a failure you could not reproduce live may
   still get a deterministic regression test (e.g. by forcing the race
   condition in the harness); claim Rule 5 only when the *regression
   proof* itself is impractical, and say so explicitly.
6. **Stop-and-replan gates.** Stop and report instead of continuing when:
   the root cause implicates the spec (deviation path); the fix wants to
   add a second path instead of extending the canonical one (§1); or two
   fix attempts have failed — the third attempt without new information
   is thrashing.

## Output Standard

- The one-sentence root-cause statement, in the fix's commit message or
  plan note
- A regression test that failed before the fix and passes after (or a
  testing-patterns Rule 5 substitute: the named replacement proof and an
  explicit statement of why a failing test was impractical)
- Consumers updated in the same change when a contract moved
- A lessons entry when the root cause exposed a reusable class of failure

## Maintenance Notes

- If a debugging session repeatedly needed a step this skill omits, add
  it while context is fresh (post-use improvement check,
  `skills/README.md`).
- If the substitute-evidence escape hatch gets used often, that is a
  signal the affected area needs a testability investment, not a looser
  skill.
