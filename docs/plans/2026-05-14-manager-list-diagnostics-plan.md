# Manager Liveness And List Diagnostics Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.14]
Superseded by: none

## 1. Goal

Improve manager liveness checks so a canonical manager row with host-PID proof that is unreachable from the current PID namespace can be rescued by keyed PING/PONG before being pruned or ignored. If the incumbent remains fresh but namespace-ambiguous, `ensure_manager()` must not start another manager while there is no pending public spawn backlog; if backlog stays pending beyond the short ambiguity grace window, startup may launch another manager so work can progress. Add an explicit operator diagnostic mode for `weft manager list` that reports current manager service-owner records without silently reducing the view to only accepted active owners.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-3]: manager service-owner rows, liveness proof, and lowest-live-TID selection.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-7]: manager bootstrap and runtime-handle proof boundaries.
- `docs/specifications/07-System_Invariants.md` [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.14]: manager heartbeat, advisory leadership, and stale/ambiguous proof behavior.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`: registry state is an append-only runtime snapshot; generator reads are required for correctness-critical history.

## 3. Context and Key Files

Files to modify:
- `weft/core/manager_runtime.py`
- `weft/commands/manager.py`
- `weft/cli/app.py`
- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/commands/test_manager_commands.py`
- `tests/cli/test_cli_manager.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`

Current structure:
- `weft/core/manager_runtime.py::_list_manager_records()` reads `weft.state.services`, prunes stale active rows by default, and returns the latest projected manager row per TID.
- `weft/core/manager_runtime.py::_select_active_manager_from_snapshot()` selects one active canonical manager from the already normalized snapshot.
- `weft/commands/manager.py::list_command()` shapes the command output; `weft/cli/app.py::manager_list_command()` owns Typer flags.

Comprehension checks before editing:
- Which path currently deletes stale active registry rows? Answer: `_snapshot_registry(..., prune_stale=True)`.
- Which row set is safe for diagnostics? Answer: latest normalized manager service-owner rows from `weft.state.services` with `prune_stale=False`; do not scan OS processes outside runtime-handle or PONG proof.

## 4. Invariants and Constraints

- Do not change manager election, bootstrap selection, or leadership-yield rules.
- Do not make `manager list` behave like a process table. It remains a view over `weft.state.services`.
- Keep existing `weft manager list` and `--json` output compatible by default.
- Add the diagnostic shape only behind an explicit flag.
- Runtime-only `weft.state.services` stays runtime-only and out of dump/load behavior.
- Use existing registry projection and liveness helpers in `manager_runtime.py`; do not create a second registry parser.
- Diagnostic PONG probing is allowed only in the explicit diagnostic mode and must remain bounded through the existing keyed probe helper.
- Do not add a new dependency or background monitor.

## 5. Tasks

1. Improve host-PID liveness fallback.
   - Outcome: an otherwise canonical manager row whose host-PID handle looks stale to the current process can still be accepted when keyed PING/PONG proves dispatch eligibility.
   - Files to touch: `weft/core/manager_runtime.py`, `weft/core/manager.py`, `tests/core/test_manager.py`, `tests/commands/test_manager_commands.py`.
   - Reuse `_manager_record_has_matched_pong()` and `Manager._manager_pong_dispatch_proof()`.
   - Stop if the implementation treats a missing PONG as proof that a running dispatch-capable manager should yield to an unknown row.

2. Make startup conservative around namespace-ambiguous incumbents.
   - Outcome: `ensure_manager()` returns a fresh active canonical incumbent instead of starting a new manager when host-PID proof is unreachable from the current container namespace and no public spawn backlog is pending.
   - Files to touch: `weft/core/manager_runtime.py`, `tests/commands/test_manager_commands.py`.
   - Stop if queue-first submission rollback starts deleting work merely because liveness is uncertain.

3. Add backlog-based escalation for unresolved ambiguity.
   - Outcome: if public spawn backlog is pending and the namespace-ambiguous incumbent remains unproved past `MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS`, `ensure_manager()` may start a helper manager.
   - Files to touch: `weft/_constants.py`, `weft/core/manager_runtime.py`, `tests/commands/test_manager_commands.py`.
   - Stop if the escalation ignores backlog or uses the long external-supervisor TTL as the only recovery clock.

4. Add a diagnostic reducer in `weft/core/manager_runtime.py`.
   - Outcome: a new helper returns latest manager records with fields such as `liveness`, `proof_source`, `dispatch_eligible`, and `canonical`.
   - Reuse `_snapshot_registry(prune_stale=False)`, `_manager_handle_from_record()`, `_live_host_processes_from_handle()`, `runtime_liveness_from_registered_probe()`, `_manager_record_has_matched_pong()`, and `is_canonical_manager_record()`.
   - Stop if the implementation starts scanning arbitrary host processes outside the runtime handle.

5. Add command and CLI plumbing.
   - Outcome: `weft manager list --diagnostic` uses the diagnostic reducer; default list remains unchanged.
   - Files: `weft/commands/manager.py`, `weft/cli/app.py`.
   - Keep table output compact; JSON carries the full diagnostic fields.

6. Add tests.
   - Command tests should use real broker-backed `Queue` writes to `weft.state.services`.
   - CLI tests should prove the new flag is accepted and default output remains compatible.
   - Do not mock SimpleBroker queues or manager registry replay.

7. Update specs and plan index.
   - Add the CLI flag to `docs/specifications/10-CLI_Interface.md`.
   - Add the plan backlink to `docs/specifications/03-Manager_Architecture.md`.
   - Add this plan to `docs/plans/README.md`.

## 6. Testing Plan

Targeted tests:

```bash
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py -k diagnostic -q
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py -k diagnostic -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k host_pid -q
```

Final local gates for this slice:

```bash
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py -q
./.venv/bin/ruff check weft tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py
```

Observable proof:
- A stale active host-pid row appears in diagnostic output with `liveness="stale"` and is not selected canonical.
- A lower live host-pid row is marked `canonical=true` when multiple live active manager rows exist.
- A host-pid row that is unreachable by PID but answers a keyed manager PONG remains selectable.
- A fresh host-pid incumbent that is unreachable from a container namespace and does not answer PONG blocks `ensure_manager()` from launching a competing manager when no public spawn backlog is pending.
- A pending public spawn backlog can escalate past the short namespace-ambiguity grace window and let `ensure_manager()` start a helper manager.
- Existing non-diagnostic list behavior still omits stale active rows.

## 7. Verification and Gates

Per-task verification is the targeted command and CLI tests above. The final gate is the two touched test modules plus ruff on touched Python files. Full mypy is not required for this output-only slice unless type changes leak beyond the touched command/runtime modules.

Rollout is backward-compatible because the default command shape stays unchanged. Rollback is code-level: remove the diagnostic flag and helper; `weft.state.services` contents are unchanged.

## 8. Independent Review Loop

External review would be appropriate for a broader election or bootstrap change. This slice is intentionally limited to an explicit read-model diagnostic and does not alter runtime behavior. A self-review is required before marking the plan completed.

## 9. Out of Scope

- No changes to manager election or startup selection.
- No process-table fallback for managers absent from `weft.state.services`.
- No new cleanup or pruning policy.
- No Docker-specific probing in core.

## 10. Fresh-Eyes Review

Self-review result: the plan is narrow enough to implement safely. The main ambiguity is whether diagnostic output should include stopped rows by default; preserve existing `--all` semantics so stopped/superseded rows require `--all`, while stale active rows are visible in diagnostic mode because that is the incident class being debugged.
