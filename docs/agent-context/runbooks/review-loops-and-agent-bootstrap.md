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

> Read the plan at [path] and its `## Proposed Spec Delta` (if present),
> including the named promotion strategy. Carefully examine the plan, the
> proposed spec text, and the associated code. Look for errors, bad ideas, and
> latent ambiguities. Don't do any implementation, but answer carefully: Could
> you implement this confidently and correctly against the delta as promoted,
> if asked?

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
