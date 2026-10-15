# Subsystem Sweeps

A sweep prunes one subsystem's whole test surface. Use this workflow for that
scope, not for routine test edits. Every rule in `skills/test-audit/SKILL.md`
applies to every lane. Steps may overlap across independent lanes; preserve the
dependencies: record a baseline before editing a lane, establish candidate
evidence and keeper coverage before deletion, and finish validation and
preservation review before claiming completion. Serialize shared harness
changes through one owner.

## Steps

1. **Baseline.** Record the subsystem's test and support line counts and every
   test file's pass/fail state at a pinned commit. Keep baseline failures in
   their own list; they are often real bugs, not stale tests. *Done when every
   in-scope test file has a recorded result.*
2. **Lanes.** Split the surface into lanes along production owner boundaries,
   not file prefixes. Include the subsystem's cases at shared boundaries and
   its harness tests. *Done when every test file belongs to exactly one lane.*
3. **Ledger.** Per lane, a read-only pass reads every test in full (including
   parameter tables) plus its production owners, and marks each declaration
   (a parameterized test is one declaration unless its rows need different
   marks):
   - `R` retain — name the contract and the bug it catches;
   - `F` fix — keep the contract, repair the assertion (e.g. a vacuous
     negative or false-green contention wait);
   - `C` consolidate — name the owner that absorbs the assertion first;
   - `D` delete — name the proof that remains, or why no contract exists.

   Judge a test by its assertions, not its name. *Done when every declaration
   has a mark and an evidence line.*
4. **Layer plan.** A second read-only pass over the ledger looks for redundant
   *layers* — e.g. several suites replaying the same shared logic through a
   mock, around a stronger real-boundary suite. Name the keeper suite per
   contract; prefer the smallest reliable boundary that detects the relevant
   failures, using the actual production owner and substituting dependencies
   outside the claim. Preserve distinct wiring and dependency coverage.
   *Done when each lane names retired files, keepers, assertions to carry over,
   and test-only seams unlocked.*
5. **Cutover.** Edit lane by lane. Remove obsolete test-only seams each lane
   unlocks. Update CI routing and test inventories. Record durable
   test-ownership rules learned from real mistakes in the subsystem's agent
   docs. Do not turn the sweep's ledger, lane assignments, or temporary plan
   into permanent test contracts. *Done when each lane's keepers pass.*
6. **Preservation review.** Independent reviewers (`skills/call-agent/SKILL.md`),
   one per boundary group, compare deleted coverage against keepers, looking
   for contracts that lost their only proof and for new assertions that cannot
   fail. Apply the sensitivity check from `skills/test-audit/SKILL.md` to each
   restored contract. *Done when every gap is restored or rejected with
   evidence, and every restored contract has a recorded failing control and
   passing candidate.*
7. **Product defects.** Investigate baseline failures that survive into
   keepers; they may expose product, test, or environment defects. Fix
   confirmed product defects at their owner when within scope, separately from
   pruning, with a failing pre-fix control and passing candidate. Record
   out-of-scope defects as follow-ups without deleting their proof. *Done when
   each failure has an evidenced disposition and each fix has control and
   candidate proof.*

When integrating upstream changes, follow the repository's Git policy. If
upstream modified a file the sweep deleted, reassess the deletion against the
new behavior; retain or restore the file, or carry its contracts into a keeper
as the evidence warrants. Preserve every newly added contract and regression.
Rerun the whole subsystem suite on the integrated result.

## Handoff

In addition to the `skills/test-audit/SKILL.md` handoff, report:

- baseline and final test/support line counts, with production counted
  separately (`git diff --numstat`), as descriptive context;
- lanes, retired layers, and keepers;
- preservation gaps found and their failing controls;
- product defects with control and candidate proof.
