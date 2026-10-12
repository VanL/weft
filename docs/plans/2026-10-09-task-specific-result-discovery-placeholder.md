# Task-Specific Result Discovery Placeholder

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
Superseded by: [Result observation simplification](./2026-10-10-result-observation-simplification-plan.md)

Class: 5, inherited from the active spec-changing burst task. This is a
non-normative companion recording deferred design, with no additional runtime
change or independent risky trigger. Future implementation must be classified
and independently reviewed before edits.

Historical deferral: the direct-result spec plan established the discovery
contract, and the successor plan owns its revision and implementation. The
problem, open questions and next actions below record the earlier deferral;
they are not current implementation instructions. Backend search/index costs
remain separate SimpleBroker concerns.

No implementation or normative promotion is authorized by this
placeholder. It is not implementation-ready.

## Problem and Desired Outcome

Generic result discovery still decodes newly appended unrelated shared-log rows
for every waiting parent, even after advancing cursors remove repeated replay.
Investigate how a waiter can learn this task's result routes and completion
without reconstructing unrelated task history. Keep queues authoritative and
preserve generic historical/TID-only observation.

Created at the user's explicit request on October 9, alongside the narrower
[burst fix](./2026-10-09-burst-result-observation-plan.md). The active fix addresses
already-read replay; this placeholder tracks the remaining fresh-row multiplier.

## Open Design Questions

- Which existing owner should provide routing: accepted submission information,
  task-owned durable initialization, or an existing broker capability? Prefer
  the least new state and preserve canonical TaskSpec resolution.
- How do manually constructed handles, custom queues, pipelines, persistent and
  interactive execution, client close, historical import, and failure before
  initialization remain correct?
- Can any improvement stay inside Weft using public broker APIs? Flag broker
  requirements to the user for separate action; do not plan upstream edits here.

## Boundaries and Verification Needed Before Implementation

No new authoritative state store, cached completion truth, synthetic partial
TaskSpec protocol, or global session registry. Preserve immutable spec/io,
accepted TID/context binding, reserved ownership and lifecycle precedence.

Measure fresh-row/JSON work per waiter with unrelated traffic after warmup.
An eventual plan needs exact spec baseline/delta, canonical route parity, true
wake/failure coverage, independent simplicity review, and both-backend proof.
No design is chosen. Owner: a future explicitly scoped Weft task. Next action:
read existing routing and publication owners, compare minimal options, then
write and review an implementation plan before edits.

## Review and Deviation Record

Author self-review: this records a known residual cost without prescribing a
premature abstraction. No product behavior changes; the current burst plan has
its own Class 5 review gate. No deviations or normative spec promotion.
