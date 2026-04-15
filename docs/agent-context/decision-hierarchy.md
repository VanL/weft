# Decision Hierarchy

Use this order whenever instructions or context seem inconsistent:

1. Explicit user instruction in the current thread.
2. Safety and repository constraints:
   dirty-tree discipline, no destructive commands, do-not-revert-others.
3. Task source-of-truth documents:
   relevant spec files, invariants, and user-facing README behavior when
   applicable.
4. Canonical repo context in `docs/agent-context/`.
5. Relevant plans in `docs/plans/` as execution guidance. Plans are
   non-normative for product behavior, but the current approved plan may be
   authoritative for scope, sequencing, rollback, and review expectations for
   the active slice. Plans may also reflect exploration, partial
   implementation, or superseded thinking.
6. Root agent files such as `AGENTS.md` and `CLAUDE.md`.
7. Existing code patterns.
8. Agent inference.

Normative rule:

- Specs are the source of truth for Weft behavior.
- Plans are execution aids. They should cite specs, and the current approved
  plan may control how the active slice is carried out, but plans do not
  override specs on product behavior and may be stale.
- For in-flight spec changes, the current approved plan may describe the
  intended delta before the spec edit lands. The slice is not complete until
  the spec is updated and becomes the steady-state source of truth again.

## Required Preflight Before Edits

- List the requested outcomes as a checklist.
- Identify the source-of-truth files for the task.
- Call out invariants that must not move.
- Record assumptions that could change correctness.
- Decide which commands can run in parallel and which must run in sequence.

## Conflict Handling

- If user correction conflicts with agent inference, stop and re-derive.
- If specs and code disagree, follow the hierarchy above and call out the
  mismatch.
- If uncertainty remains on a high-impact change, ask once and narrowly.

## Completion Gate

Every requested item should have at least one evidence line:

- file path and what changed
- command executed and result
- observed queue, task-state, or CLI behavior
