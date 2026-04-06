# Shared Principles

## Core Standards

- Specs are the source of truth for system behavior.
- Keep changes minimal, local, and aligned to the request.
- Verify behavior with concrete evidence before declaring completion.
- Do not silently assume missing context; read the relevant spec, plan, test,
  or implementation first.

## Collaboration Standards

- Follow explicit user corrections immediately.
- State critical assumptions before broad or irreversible changes.
- Report blockers with precise causes and the next missing input.

## Change Hygiene

- Do not revert unrelated work in a dirty tree.
- Avoid destructive commands unless explicitly requested.
- Preserve the current execution spine unless the task spec or plan explicitly
  changes it.
- Update all producers and consumers together when changing a contract.

## Verification Standards

- Match each requested change with evidence.
- Prefer the smallest test that proves the behavior, then expand as blast
  radius increases.
- Run dependent state-changing commands sequentially when order matters.

## Document Traceability

Specs and plans should form a navigable chain:

    spec <-> plan(s)

Rules:

- Plans link to the spec(s) they implement, or say why no spec exists.
- When a plan is derived from a spec, add or update a `## Plans` or
  `## Related Plans` section in the spec with a backlink.
- Durable corrections belong in `docs/lessons.md`.
