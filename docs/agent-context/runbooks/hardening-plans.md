# Hardening Plans

Use this companion runbook when a draft plan already has the normal sections
but still feels too loose, too optimistic, or too easy to implement
incorrectly.

For risky or boundary-crossing Weft work, this runbook is required, not
optional.

Role split:

- `writing-plans.md` defines the required sections and minimum blockers
- this runbook defines how to tighten the draft when boundary mistakes are the
  real risk

## When This Runbook Is Mandatory

Treat hardening as mandatory when any of these are true:

- execution touches the durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- the same core behavior must run through more than one context such as CLI,
  manager, worker, task, or watcher code
- a public contract, CLI shape, queue name, TaskSpec field, result payload, or
  persisted format is changing
- rollback depends on backward compatibility or rollout order
- new persistence, temp-file, queue-cleanup, or deferred-processing lifecycle
  is introduced
- the change affects runtime-only `weft.state.*` queues or history reads
- the change includes a one-way door or destructive edge

## Core Failure Mode

The most common planning failure is describing what to build without
describing what must not change.

For Weft, boundary mistakes usually hide in:

- queue contracts and state transitions
- template vs resolved `TaskSpec` rules
- manager or task bootstrap sequencing
- append-only history reads
- cleanup and retry behavior around deferred work

If those stay implicit, the plan will drift even when the implementer is
competent.

## Hardening Checklist

Before treating a risky Weft plan as review-ready, check that it covers these
when relevant:

1. invariants are named before or alongside the tasks
2. hidden couplings and stage-crossing state are called out explicitly
3. wrapper logic is separated from core work when the same behavior spans CLI,
   manager, worker, or task contexts
4. each meaningful task includes a stop-and-re-evaluate gate
5. out-of-scope work is named explicitly
6. the testing plan says what should not be mocked
7. tests are aimed at contracts and externally visible behavior, not only
   internals
8. error paths distinguish fatal failures from best-effort failures
9. success can be observed after deployment or runtime exercise, not only
   through local tests
10. the plan describes the file, queue, or behavior that exists today, not
    only the file to edit
11. rollout sequencing is stated when order matters
12. rollback is stated before execution begins
13. one-way doors are identified and held to a higher review bar
14. deferred-processing paths account for queue, temp-file, or input lifecycle
15. required reading includes comprehension questions for complex areas

## 1. State Invariants Before Tasks

Name the invariants that matter enough to cause regressions.

Common Weft invariants:

- TID format and immutability stay intact
- state transitions remain forward-only
- reserved-queue policy remains correct
- `spec` and `io` stay immutable after resolved `TaskSpec` creation
- runtime-only `weft.state.*` queues do not quietly become persisted features
- the current durable spine stays the only execution path unless the spec says
  otherwise

## 2. Identify Hidden Couplings Before Decomposition

Trace the end-to-end flow and ask what assumes the current shape or timing.

Common Weft hidden couplings:

- CLI startup to manager registry state
- manager lifecycle to `weft.state.managers` and PID liveness
- task completion to outbox visibility and completion-event timing
- append-only queue histories to generator-based readers
- template TaskSpecs to resolved TaskSpecs and downstream consumers

Name these before task breakdown so the implementer does not discover them
mid-change.

## 3. Separate Wrapper Logic From Core Work

If the same logic spans more than one context, bias toward one shared core path
with thin wrappers.

Examples:

- CLI normalization vs manager/runtime logic
- task adapter wrappers vs the runner behavior they call
- public command output vs internal event generation

Do not create a second durable path unless the spec explicitly requires it.

## 4. Add Stop-and-Re-Evaluate Gates

Tasks should say when to stop because the implementation is drifting.

Useful stop gates:

- a second execution path is appearing
- queue history reads are being rewritten around fixed limits
- the change is mutating resolved `TaskSpec` state that should stay frozen
- the code wants a broad mock harness because the real path is inconvenient
- a contract the plan said must stay stable is about to change

## 5. State Out of Scope Explicitly

Out-of-scope notes prevent opportunistic refactors such as:

- queue cleanup redesigns not required for the fix
- adjacent CLI polish unrelated to the behavior under review
- speculative persistence changes
- renaming or reorganizing modules without a spec need

## 6. Specify What Not To Mock

Weak Weft plans often fail because the tests mock away the real proof.

A hardened plan should say when to keep these real:

- `WeftTestHarness`
- broker-backed queues
- manager and task lifecycle
- reservation semantics
- outbox and task-log behavior

Mock only boundaries that are external, slow, or nondeterministic.

## 7. Test the Contract, Not Only the Internals

Prefer tests that prove:

- public CLI or queue-visible behavior
- durable side effects
- state transitions and retry behavior
- compatibility behavior around existing contracts

Use internal assertions only as supporting evidence unless the internal seam is
itself the contract.

## 8. Make Error-Path Priorities Explicit

Ask which failures are fatal and which are best-effort.

Typical Weft examples:

- corrupt state transition or reservation behavior is fatal
- optional observability output is usually best-effort
- auxiliary cleanup failure must not silently rewrite the core outcome

## 9. Define Observable Success

Local tests are not the whole proof.

When relevant, say how success will be observed:

- expected queue messages or registry state
- absence of a known race or hang
- correct terminal task state plus result visibility
- bounded queue depth or cleanup behavior after restart

## 10. Describe the Current Structure

Do not only name the file. Describe:

- which class or function currently owns the behavior
- which queue or state record carries the behavior today
- where the public output or contract is shaped
- which helper already implements the canonical path

This reduces cold-reading mistakes in large Weft modules.

## 11. Write the Rollback Before the Detailed Tasks

If rollback cannot be described, the coupling is not understood well enough.

For Weft, that usually means answering:

- which queue or contract must remain backward-compatible during rollout
- whether old and new readers can coexist safely
- whether rollback depends on preserving the current durable spine

## 12. Think Through Rollout Sequencing and One-Way Doors

When order matters, the plan should say:

- what ships first
- what remains backward-compatible during rollout
- what can be reverted independently

One-way doors include destructive cleanup, incompatible payload or queue-name
changes, and identifier or storage format changes.

## 13. Handle Deferred-Processing Lifecycles

Any deferred or queue-backed path introduces lifecycle questions:

- where does the input live before processing?
- what cleans it up?
- what happens on restart?
- can the same work be claimed twice?

If the plan introduces deferred work and does not answer these, it is still too
loose.

## 14. Required Reading Should Check Comprehension

For hard changes, required reading should include one or two questions such as:

- which queue or log currently proves completion?
- where is the canonical TaskSpec resolution path?
- which layer owns the public CLI or result shape?

If the implementer cannot answer those, they are not ready to edit.

## When To Stop and Re-Plan

Stop and revise the plan if:

- hidden couplings emerge that the plan did not mention
- the change crosses a one-way door the plan treated as ordinary
- rollback cannot be described cleanly
- the plan starts depending on a second execution path
- the testing seam becomes "mock everything" just to make progress
