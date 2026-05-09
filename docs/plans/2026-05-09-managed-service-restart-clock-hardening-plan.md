# Managed Service Restart Clock Hardening Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [MANAGER.15]; docs/specifications/05-Message_Flow_and_State.md [MF-6]
Superseded by: none

## 1. Goal

Make manager-owned singleton service restarts converge deterministically after
terminal evidence by using the manager's local observation time for restart
backoff scheduling. Broker message timestamps remain ordering evidence, not
wall-clock scheduling input.

## 2. Source Documents

- `docs/specifications/07-System_Invariants.md` [MANAGER.15] requires
  manager-owned singleton services to reduce pending-spawn, tracked-child,
  task-log, and endpoint liveness evidence into one deterministic transition.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6] defines
  manager-owned internal spawn requests and service metadata.
- `docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md`
  shipped the pure reducer and calls out the serve restart/no-duplicate proof.

## 3. Context and Key Files

Files to modify:

- `weft/core/manager_services.py`: use reducer `now_ns` for restart backoff
  after terminal evidence instead of `ServiceCandidate.timestamp`.
- `tests/core/test_manager_services.py`: add pure reducer coverage for future
  broker timestamps not extending restart backoff.
- `docs/lessons.md`: record that broker timestamps are ordering evidence, not
  scheduling clocks.
- `docs/plans/README.md`: add this plan.

Current structure:

- `ManagedServiceCandidate.timestamp` comes from queue history and is valid for
  ordering observations from one broker.
- `reduce_managed_service_state()` compares `next_allowed_ns` to `now_ns`,
  which is supplied by the manager process.
- Mixing those two time sources can leave an ensure-style service waiting until
  a backend timestamp catches up to local process time.

## 4. Invariants and Constraints

- Preserve the pure reducer: no queue reads, process probes, sleeps, or side
  effects in `manager_services.py`.
- Preserve singleton ownership semantics: one canonical live owner, terminal
  proof wins per TID, pending spawn blocks duplicates.
- Do not change queue payloads, metadata keys, or task-log shapes.
- Do not widen test timeouts to mask the bug.

## 5. Tasks

1. Add a reducer regression.
   - Build an ensure service with `restart_backoff_ns > 0`, active TID, and
     terminal evidence whose broker timestamp is far in the future.
   - First reduction should schedule restart at `now_ns + backoff`, not
     `candidate.timestamp + backoff`.
   - Second reduction after that local backoff should return `start_now` even
     if the same future timestamp remains in evidence.

2. Fix the reducer.
   - Replace terminal timestamp-based backoff base with `now_ns`.
   - Keep terminal timestamp available for candidate ordering only.

3. Verify the serve path.
   - Run `tests/core/test_manager_services.py`.
   - Run the targeted CLI serve restart test under SQLite and Postgres.

## 6. Testing Plan

Commands:

```bash
uv run pytest tests/core/test_manager_services.py -q
uv run pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q
uv run --extra dev --extra docker --extra macos-sandbox bin/pytest-pg tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates
```

## 7. Verification and Gates

Done means the reducer test proves future broker timestamps do not stretch
restart backoff, and the real foreground serve restart test still converges.

## 8. Independent Review Loop

This is a narrow correction to an existing completed reducer plan. Do a final
self-review against the reducer invariants and record residual risk in the
handoff.

## 9. Out of Scope

- New service registry queues.
- New manager service CLI commands.
- Rewriting pending-spawn recovery.
- Changing broker timestamp generation.
