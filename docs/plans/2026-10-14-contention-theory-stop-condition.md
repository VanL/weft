# Contention failures require theory re-derivation

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0]
Superseded by: none

Class: 5+P. Strengthens the investigation and acceptance contract after an owner correction. Spec-authoring, promotion strategy D. Hardening: N/A; no runtime, lifecycle, storage, configuration, or release change.

## Goal and outcomes

Make the urge to reduce contention to make tests pass an immediate stop condition. The investigator must locate the gap between observed behavior and their possession of Weft's program theory before choosing a correction.

- [x] State the causal principle in program theory [WT-7] and strengthen [ALT-WT-017].
- [x] Promote the operating rule into Testing Strategy [TS-0]; make engineering principle 4 and the testing runbook point to it.
- [x] Record the owner correction in lessons; qualify the runbook's automatic timeout-increase advice.
- [x] Review the semantics independently and run the existing documentation gates at their normal concurrency.

## Baseline and current structure

Diff base: `9b7ada69a21474346b44700d31157819819e62a6`. This change revises [TS-0]. Its pre-edit file SHA256 is `a6b7378835f1ac047d1eaabace958cfcedee8daa2d6ca058435a840fe83fe503`.

Program theory already rejects reduced-concurrency acceptance, but [ALT-WT-017] permits debugging without requiring a stop or causal question. Testing-patterns Pattern 1 permits narrow serialization; Pattern 10 advises raising shared waits when failures cluster. These can be misread as permission to remove the pressure that exposes the fault. The owner explicitly rejected that inference on 2026-10-14.

Read program theory [WT-0], [WT-2], [WT-7], [ALT-WT-017]; Testing Strategy [TS-0]; engineering principles 4, 4.1, 9; testing-patterns Patterns 1 and 10; review-loops-and-agent-bootstrap; and the September 14 inline-fixture lesson. The theory's draft status does not weaken this explicit owner instruction and is not changed by this slice.

## Proposed Spec Delta

Insert under [TS-0], before Staged CI and Release Proof:

> ### Contention is a theory-possession stop condition
>
> If an investigator wants to reduce contention to make tests pass, they must
> stop corrective work and acceptance reruns immediately. Treat the failure as
> evidence of a gap in their possession of the program theory, not as a reason
> to weaken the load. This applies to reducing workers, serializing tests, or
> otherwise relieving pressure to obtain green. It does not by itself prove
> that the runtime implementation is the faulty layer.
>
> Preserve the original command, backend, concurrency, load, and failure
> evidence. Re-read the relevant program theory and governing contract; state
> what ownership, resource lifetime, progress, or cost behavior the model
> predicted, and identify where the observed behavior contradicts it. Locate
> the causal flaw or false premise in the implementation, harness, test
> contract, substrate, environment assumptions, or investigator's model before
> choosing a correction. Labels such as "contention", "flaky", "pre-existing",
> or "shared resource" are not causal explanations. Adding resources or time
> cannot substitute for that explanation either.
>
> Only after this stop and re-derivation may a lower-load experiment test a
> stated falsifiable hypothesis. Label it diagnostic; a pass does not resolve
> the original failure or count as acceptance. Verify the justified correction
> under the original load and standard concurrency, using the governing
> expectation if a faulty oracle was corrected. A predeclared shared-resource
> test protocol is not a workaround; introducing or expanding one to obtain a
> pass triggers the same stop.
>
> Owner: the investigator; reviewers check the causal explanation and rerun
> evidence. Boundary: load-sensitive investigation and acceptance, not a
> promise of success under unbounded load. Required action: keep the original
> failure open until the conceptual gap and correction are evidenced.
>
> Theory: [WT-7] and [ALT-WT-017] in `docs/program-theory.md`. Operational
> guidance: engineering principle 4.3 and testing-patterns Pattern 1.

## Scope, invariants, and sequence

Edit only this plan, the plan index, program theory, Testing Strategy, engineering principles, testing-patterns, and lessons. Preserve every prior worktree edit. Do not resume the stopped implementation or investigate its failure in this slice; do not alter code, test workers, timeouts, CI, or dependencies. Keep one normative rule in [TS-0], with concise pointers elsewhere.

1. Review this exact delta independently before promotion; record and resolve findings.
2. Apply the spec rule and its theory/runbook/lesson counterparts. Record the promoted spec fingerprint. Rollback, if directed, removes only this slice's text; no runtime rollback or one-way door exists.
3. Fresh-eyes and independent completed-work review, then verification. Review scenarios: a full-suite failure passing alone; a shared-resource lock added after failure; a bad deadline oracle proved by evidence; and intentional overload beyond a stated resource envelope. Each must stop and identify the conceptual gap without claiming every failure is a runtime bug or accepting weaker-load green.

## Verification and evidence

The pre-change evidence is the missing stop requirement in [ALT-WT-017] and the unconditional timeout advice in Pattern 10. No executable product behavior changes. The correction is demonstrated by semantic scenario review plus the existing metadata/spec-hygiene and path checks. No prose-pinning test or runtime behavior claim is added.

Run after `. ./.envrc`: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py` (default concurrency), `./bin/check-doc-paths` (advisory), and `git diff --check`. Review this fork's delta separately from inherited implementation edits. No full product test run is needed for documentation-only guidance.

## Review, promotion, and deviation record

Independent same-family review of the proposed rule: PASS. The accepted clarification preserves original stress conditions without preserving a proved-wrong oracle. Exact-text verification precedes promotion. Different-family review was not used; the available collaboration reviewer is from the author's family.

## Deviation Log

None.


## Promotion and verification evidence

2026-10-14: promoted the reviewed rule under [TS-0], strategy D. Post-promotion
spec SHA256: `d3bb4aa444e0ec41e2ee892a160db855224db86786976cc2ca2af62cf19dc2af`.
Program theory [WT-7]/[ALT-WT-017], engineering principle 4.3, testing-patterns
Patterns 1/10, and the dated lesson now state the same diagnostic/acceptance
boundary. Existing theory adoption status was preserved.

Fresh-eyes review found and resolved the unconditional timeout-increase advice
in Pattern 10 and the corrected-oracle ambiguity. Independent same-family
review passed the proposal, exact spec text, and final cross-document delta.
The four semantic scenarios above passed that review. No runtime diagnosis or
claim about the parent implementation's correctness was made.

Verification: the default-concurrency metadata/spec-hygiene command passed all
six tests (1.57 seconds); `bin/check-doc-paths` passed before and after the edit;
`git diff --check` passed. Only documentation was edited. Land by explicit
file-list staging of this fork's documentation delta; preserve inherited edits.
