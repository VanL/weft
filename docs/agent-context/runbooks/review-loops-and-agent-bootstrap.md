# Review Loops and Agent Bootstrap

Use this runbook when a plan or completed change is large enough that author
blindness is a real risk.

## When To Use It

Treat independent review as required when:

- the work is risky or boundary-crossing
- the plan touches multiple subsystems or documentation layers
- the change introduces a new reusable workflow or contract
- a zero-context implementer could guess wrong and still produce plausible code

## Bootstrap the Available Reviewers

Before choosing a reviewer:

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

> Read the plan at [path]. Carefully examine the plan and the associated code.
> Look for errors, bad ideas, and latent ambiguities. Don't do any
> implementation, but answer carefully: Could you implement this confidently and
> correctly if asked?

Give the reviewer:

- the active plan
- the governing spec or specs, if any
- the relevant README or implementation-note paths
- the touched files or intended file list

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
