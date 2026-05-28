# Service Collation Reporting Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13]
Superseded by: none

## Scope

Implement a narrow reporting change for Monitor-owned durable collation rows:
terminal Manager, built-in service, and manager-authored managed-service rows
must be classified and emitted separately from ordinary user task summaries.

This plan implements:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13]

It intentionally does not add a sixth cleanup policy. The work remains part of
`monitor_store.lifecycle`.

## Tasks

1. Add a common collation classification helper owned by
   `weft/core/monitor/store.py`.
   - Classify only from Weft-owned evidence: `role=manager`, built-in service
     roles, reserved service metadata, autostart metadata, and internal runtime
     class metadata.
   - Do not classify domain-specific metadata such as `runtime=internal` as a
     service marker. That would hide ordinary failed work.
   - Include `collation_kind` in every compact collation summary.
   - Include a `service` sub-summary only for manager/service rows.

2. Update Monitor summary emission in
   `weft/core/monitor/task_monitor.py`.
   - Emit ordinary user rows as `record_type=task_summary`.
   - Emit manager/service rows as `record_type=service_summary`.
   - Keep the full task summary under `task` for compatibility.
   - Add the service classification under `service` for direct reporting.

3. Update external JSONL emission in
   `weft/core/monitor/external_log.py`.
   - Keep the external `record_type=task_log_collated` compatibility contract.
   - Surface `collation_kind` and `service` at the top level when present.

4. Add focused tests.
   - Store tests prove user, manager, built-in service, and managed-service
     classification.
   - TaskMonitor tests prove service rows emit `service_summary`.
   - External sink tests prove service classification survives JSONL output.

5. Update the specs.
   - [MF-5] documents service-summary emission and conservative service
     classification.
   - [OBS.13] documents that service summaries are reporting shape only and do
     not change cleanup authority or policy identities.

## Verification

Run the smallest useful test set first:

```bash
./.venv/bin/python -m pytest -n0 tests/core/test_monitor_store.py tests/core/test_monitor_external_log.py tests/tasks/test_task_monitor.py -q
```

Then run local quality gates for touched runtime code:

```bash
./.venv/bin/ruff check weft/core/monitor/store.py weft/core/monitor/task_monitor.py weft/core/monitor/external_log.py tests/core/test_monitor_store.py tests/core/test_monitor_external_log.py tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```
