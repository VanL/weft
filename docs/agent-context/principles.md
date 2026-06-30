# Shared Principles

## Core Standards

- Specs are the source of truth for system behavior.
- Plans are non-normative implementation documents for product behavior. The
  current approved plan may still be authoritative for execution details of the
  active slice, such as scope, ordering, rollout, rollback, and review loops.
  Plans may also be exploratory, partially implemented, or stale, so do not
  use them to override the specs.
- If the active slice includes an intended spec change that has not landed yet,
  the plan may temporarily define that delta. The work is not done until the
  spec is updated and the normative boundary is restored.
- Keep changes minimal, local, and aligned to the request.
- Verify behavior with concrete evidence before declaring completion.
- Do not silently assume missing context; read the relevant spec, plan, test,
  or implementation first.
- Optimize for agent usability, not just human readability.
- On risky work, be over-prescriptive: name invariants, hidden couplings,
  anti-mocking guidance, rollback or rollout sequencing, and post-deploy
  signals instead of expecting the implementer to infer them.

Agent-usable guidance should make these explicit when relevant:

- owner
- boundary
- verification
- required action

Example:

- weak: "update the startup flow"
- stronger: "owner: `weft/cli/run.py` manager wait path; boundary: no
  second startup path; verification: targeted manager-startup regression;
  required action: extend the existing registry wait logic"

## Collaboration Standards

- Follow explicit user corrections immediately.
- State critical assumptions before broad or irreversible changes.
- Report blockers with precise causes and the next missing input.
- Returning mid-task with falsified premises, newly discovered issues, or
  reasonable uncertainty is a success path, not a failure. Bring the
  evidence and a corrected scope, then wait for direction
  (see `docs/lessons.md`, 2026-06-11).
- If something seems clear to a human but confusing to an agent, say so and
  propose a specific change that would make it easier to use correctly.

## Change Hygiene

- Do not revert unrelated work in a dirty tree.
- Avoid destructive commands unless explicitly requested.
- Agents suggest dependencies; humans add them. Do not introduce a new
  dependency on your own — propose it with a justification (purpose, why the
  standard library or an already-vendored dependency won't do, cost of taking
  it on) and let the human decide whether it enters the manifest.
- Preserve the current execution spine unless the task spec or plan explicitly
  changes it.
- Update all producers and consumers together when changing a contract.

## Verification Standards

- Match each requested change with evidence.
- Prefer the smallest test that proves the behavior, then expand as blast
  radius increases.
- Run dependent state-changing commands sequentially when order matters.
- For risky changes, proof includes rollout or rollback assumptions and
  intended post-deploy observation, not only local test results.
- Every plan requires a separate fresh-eyes self-review before it is reported
  complete or implementation-ready. The review must look for latent ambiguity,
  bad ideas, and whether the plan can be implemented correctly by a
  zero-context engineer.
- High-stakes or complicated plans require external review when another
  reviewer can be used. If the reviewer is a different agent, expect a
  5-10 minute turnaround and do not skip the review because of that delay.

## Document Traceability

Specs, plans, and code should form a navigable chain:

    spec section <-> plan(s) <-> code

Rules:

- Specs stay authoritative for behavior; plans must align to specs, not the
  other way around.
- Plans link to the exact spec file(s) and section code(s) they implement, or
  say plainly why no spec exists.
- When a plan is derived from a spec, add or update a `## Plans` or
  `## Related Plans` section in the spec with a backlink.
- When the work is risky, the plan should also say what must not change and how
  review will happen.
- When implementation notes exist near the touched spec section
  (`_Implementation snapshot_`, `_Implementation status_`, or
  `_Implementation mapping_`), update them in the same change.
- Touched code modules should keep docstrings or equivalent nearby comments that
  point back to the relevant spec sections; add function-level `Spec:` notes
  when module-level ownership would otherwise stay ambiguous.
- Durable corrections belong in `docs/lessons.md`.
