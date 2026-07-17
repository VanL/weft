# Review Loops and Agent Bootstrap

Use this runbook for plan and completed-work reviews. Every plan requires a
self-driven fresh-eyes review. High-stakes or complicated plans require an
external reviewer in addition to self-review.

Here, "bootstrap" means discovering which reviewers are available in the
current environment, not Weft manager or task startup.

## When To Use It

Treat self-driven fresh-eyes review as always required for plans. The review
must be a distinct pass after drafting, and it must ask:

- Are there latent ambiguities?
- Are there bad ideas or unsafe tradeoffs?
- Could a zero-context engineer implement this confidently and correctly?
- Does the plan clearly separate what must change from what must not change?

Treat external review as required when:

- the work is risky or boundary-crossing
- the plan touches multiple subsystems or documentation layers
- the change introduces a new reusable workflow or contract
- a zero-context implementer could guess wrong and still produce plausible code
- the plan changes runtime behavior, cleanup, persistence, queue contracts, or
  manager/task scheduling

If an external review is requested from a different agent, expect it to take
5-10 minutes. Do not skip the review just because the wait is inconvenient.

## Bootstrap the Available Reviewers

Before choosing an external reviewer:

1. Check which other agent families or review paths are actually available in
   the current environment.
2. Prefer a different agent family than the author when one is usable.
3. If only same-family review is available, note that limitation in the plan or
   review notes.

Recommended candidates to check when present:

- Claude
- Codex
- Qwen
- Gemini
- Kimi

You do not need a permanent inventory file for every session, but you should
record reviewer availability when it materially affected the review choice.

## Planning Review Prompt

Recommended prompt:

> Read the plan at [path] — including its `## Proposed Spec Delta` and
> promotion strategy, if present — and review the associated code and
> documentation. Look for errors, bad ideas, and latent ambiguities.
> Watch out for performative overengineering — tests or processes that
> add ceremony without meaningfully addressing a real-world risk
> identified in the code.
>
> Check specifically for invariants: what must not be changed (where
> this repository keeps a standing-invariants registry, check the plan
> against it). If you need to propose a new invariant, or there is a
> meaningful risk that would be raised by implementing this plan,
> describe that risk with a directive to raise it for human review.
>
> You must answer PASS or BLOCKED, followed by your analysis of any
> blocking issues, based upon your answers to these two questions:
> 1. If asked, could you implement this plan as written confidently and
>    correctly?
> 2. Would implementing this plan meaningfully impair or degrade the
>    system, its security, or its robustness?

A BLOCKED verdict must trace to question 1 or question 2; anything else
the reviewer wants to say is a finding or a raise-for-human-review
directive, never a block.

Give the reviewer:

- the active plan
- the governing spec or specs (baseline identifier), if any, and the plan's
  `## Proposed Spec Delta` when present
- the relevant README or implementation-note paths
- the touched files or intended file list

## Self-Review Output

The plan author must record or report a self-review result before calling a
plan complete. Use this structure:

- findings, ordered by severity
- why each finding matters
- exact plan section, spec, or code path affected
- whether the plan was updated
- whether external review is still required

If the self-review finds no issues, say so directly and state any residual
risk.

## Completed-Work Review Prompt

For completed work, keep the stance similar but point at the touched files and
the governing plan/spec.

Ask the reviewer to focus on:

- bugs or regressions
- latent ambiguities
- missing verification
- missing doc maintenance
- drift from the plan or spec

## Scoped Change Review Prompt

For reviewing a bounded change (a fix, a revert, a revision to an
approved plan) rather than a whole plan. The brackets are the brief's
required-shape elements (see `skills/call-agent/SKILL.md` step 2): if
you cannot fill one, you have not decided the review's scope yet — decide
it before dispatching, not in follow-ups. Filling the brackets IS the
scope decision.

> You are reviewing a single change, not the subsystem it touches.
> Do not implement or modify anything.
>
> **Unit under review:** [the delta — files, diff, or plan section] at
> baseline [SHA]. For a plan revision: the delta from the reviewed
> baseline [SHA] plus its Revision Log rationale.
> **Goal of the change:** [one sentence].
> **Explicitly accepted risks — do not re-litigate:** [list, or "none
> declared"].
> **Standing constraints this change must not cross:** [key invariants,
> or the repo's invariants registry path, or "none registered"].
> **Pre-existing concerns** (concurrency, error shapes, validation,
> policy, lifecycle, style) are out-of-scope observations unless THIS
> change makes them worse.
>
> Output: a findings table — ID | severity (P1–P3/nit) | location |
> finding | **suggested** disposition. Severity is your claim about
> impact; whether anything blocks is decided at disposition, not by you.
> Scope expansions go in a separate "Observations (not actionable this
> pass)" section for the owner — never as blockers. Prefer removing
> unnecessary work over adding it. Verdict line: `no blocker` or
> `blocker: F<ids>`, naming only findings within the unit under review.

Round-2 variant (after dispositions — never before):

> Round-2 verification, scoped ONLY to these accepted findings and their
> fixes: [IDs, one line each]. Verify each fix; report any NEW defect the
> fixes introduced. Do not revisit declined or out-of-scope findings —
> they are closed by their disposition rows. Verdict: PASS / FAIL.

## Feedback Loop

After review:

1. Hand the findings back to the original authoring agent.
2. Require an explicit response to each point.
3. Update the plan, docs, or code for accepted findings.
4. If the author disagrees, record why the current path is still the best
   choice.

If the reviewer says they could not implement the plan confidently and
correctly, treat that as a blocker until the ambiguity is fixed or explicitly
recorded.

Review findings are claims, not facts: reproduce a finding before acting on
it, and reproduce your own "done/passing" assertions before making them. The
same discipline applies to status documents — a ledger that says "ship-ready"
is a claim about the past; the evidence is a rerun in the present. Verifier
error is real and its cost compounds, because a wrong finding acted on is a
defect introduced with confidence.

Do not report a high-stakes or complicated plan as implementation-ready while
external review is still pending. Report it as review-pending instead.

## Slice-Based Review

For larger work, do not wait until the very end.

Run review:

- after the plan is written
- after each meaningful slice another engineer could review coherently
- again before final completion if the work changed materially during execution

## Review Output Standard

Reviewer output should prioritize findings first.

Recommended structure:

- finding
- why it matters
- what file, section, or step is affected
- whether the reviewer could implement or sign off confidently after the fix

**Verdict vocabulary, by review type:**

- **Plan reviews (the Planning Review Prompt):** `PASS` / `BLOCKED`,
  derived from the two questions (implementable-confidently;
  would-not-degrade). A block traces to one of the two questions or it is
  not a block.
- **Scoped-change reviews (the Scoped Change Review Prompt):**
  `no blocker` / `blocker: F<ids>`, naming only findings within the unit
  under review; round-2 variants answer `PASS` / `FAIL` over accepted
  finding IDs only.
