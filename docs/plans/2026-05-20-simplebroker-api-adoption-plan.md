# SimpleBroker API Adoption Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-4]
Superseded by: none

## Scope

Adopt the released SimpleBroker APIs that changed queue listing semantics and
added cheaper queue-name enumeration plus multi-queue deletion.

Spec references:

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/10-CLI_Interface.md` [CLI-4]

## Boundaries

- SimpleBroker remains the owner of queue mechanics. Weft should not reach
  around it with backend-specific queue SQL.
- `list_queues()` returns queue names. Code that needs counts must use
  `list_queue_stats()`.
- The TaskMonitor PONG path must stay cached. It must not scan queues or touch
  the Monitor store to answer `PING`.
- Default supervised cleanup may delete only standard terminal task-local
  `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and eligible `T{tid}.reserved` queues.
  It must not delete manager/global/custom control queues, task inboxes, or
  task outboxes.

## Implementation

1. Fix dump/load and tests that still destructure `list_queues()` as
   `(queue, count)`.
2. Use names-only `list_queues()` where Weft only needs queue names, including
   `weft result --all` and TaskMonitor runtime cleanup discovery.
3. Replace TaskMonitor whole-queue cleanup loops with
   `broker.delete_from_queues(...)` for standard control queues and eligible
   reserved queues.
4. Keep retained task-log exact deletion on proven message IDs. The new
   `find_message_ids(...)` API is useful for targeted known-TID repair or
   inspection, but it is substring-based and returns IDs only, so it is not the
   lifecycle authority for the supervised Monitor cleanup path.

## Verification

- Command dump/load tests cover the changed list contract.
- TaskMonitor tests cover successful control cleanup, retryable delete errors,
  active reserved queues, reserved deletion, fairness, and deadline behavior.
- Ruff and mypy should pass for touched runtime modules.
