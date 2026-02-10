# Weft Implementation Status

Last updated: 2026-01-24

This file tracks spec → code progress. Specs are the source of truth.
Reference docs live in `docs/specifications/`.

## Ordered Implementation Plan

1) **TaskSpec templates + TID assignment** (TS-1, MF-1, WA-2)
- Implement template/resolved TaskSpec flow.
- TID must be spawn-request message ID (SimpleBroker timestamp).
- Allow `tid=None` in templates; Manager expands `tid`, `io`, `state`, `spec.weft_context`.
- Update run/manager/consumer tests for TID handling.
- **Status**: completed (2026-01-24)
- **Notes**: templates allowed with `tid=None`; `Queue.write()` returns timestamp; `weft run` enqueues templates and uses spawn message ID; Manager ignores provided TIDs and rewrites legacy `T{old_tid}.` queues; interactive stdin seeded via spawn payload; added template validation tests.

2) **Reserved queue policy semantics + cleanup** (MF-2, MF-8)
- Enforce `keep/requeue/clear` on STOP/error; crash leaves reserved.
- Ensure success deletes reserved by message ID.
- Cleanup deletes empty queues but preserves outbox until consumed.
- **Status**: completed (2026-01-24)
- **Notes**: error paths now apply reserved policies with cleanup for non-KEEP; interactive STOP and error paths apply reserved policy + ack; manager applies error policy for invalid spawn requests; interactive final-output drain hardened to avoid losing output on fast exit.

3) **Autostart manifests (task/pipeline only)** (TS-1.2, WA-1)
- Support autostart manifests referencing stored task specs or pipelines only.
- Implement `once`/`ensure` policy semantics.
- **Status**: completed (2026-01-24)
- **Notes**: Manager now loads autostart manifests, resolves stored task specs, enqueues spawn requests, and supports `once`/`ensure` with restart/backoff tracking. Added ensure-restart test.

4) **Spec management commands** (CLI-6)
- `weft spec create/list/show/delete/validate/generate`.
- Spec storage under `.weft/tasks` and `.weft/pipelines`.
- **Status**: completed (2026-01-24)
- **Notes**: Added `weft spec` subcommands with task/pipeline storage and JSON output; `spec validate` reuses TaskSpec validation; added CLI tests.

5) **Task management + listing** (CLI-4)
- `weft list`, `weft task status/stop/kill/tid`.
- Hook into `weft.log.tasks`, `weft.state.tid_mappings`.
- **Status**: completed (2026-01-24)
- **Notes**: Added list/task CLI commands using log snapshots + tid mappings; supports status/stop/kill/tid and basic filters; added CLI tests.

6) **Pipeline execution** (CLI-1.1 + spec pipeline sections)
- Run stored pipeline specs via `weft run --pipeline`.
- Alias wiring for stage inbox/outbox.
- **Status**: completed (2026-01-24)
- **Notes**: Implemented sequential pipeline execution via stored/pathed pipeline specs; stage defaults merged; added CLI test. Queue alias wiring remains TODO.

7) **System commands + status filters** (CLI-2)
- `weft system tidy/dump/load` alignment with spec, task filtering in `status`.
- **Status**: completed (2026-01-24)
- **Notes**: Added `system` command group for tidy/dump/load; status watch now honors `--status` filter; system dumps exclude `weft.state.*` queues by default with load skipping runtime queues; added CLI tests for system dump/load and updated tidy test.

8) **Reporting interval (poll)** (CC-2.4)
- Implement periodic state reporting when `spec.reporting_interval == "poll"`.
- **Status**: completed (2026-01-24)
- **Notes**: `BaseTask` emits `poll_report` events at `polling_interval` when configured; covered by `tests/tasks/test_task_observability.py`.

9) **Completion + CLI polish** (CLI-7)
- Shell completion generation per spec.
- **Status**: completed (2026-01-24)
- **Notes**: Dropped dedicated `weft completion` command in favor of Typer's built-in `--install-completion`/`--show-completion` flags.

## Testing Expectations

- Prefer `WeftTestHarness` for integration-style tests.
- Add unit tests for TaskSpec validation and policy behavior.
- Add CLI tests for new commands and JSON outputs.
- Keep tests deterministic; avoid long sleeps.
