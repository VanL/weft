# Status Lists Unproven Managers as Unknown

Status: draft
Source specs: docs/specifications/10-CLI_Interface.md [CLI-1.2.1]; docs/specifications/03-Manager_Architecture.md [MA-1] item 4 and the `weft.state.services` custody paragraph; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class 5 with hardening: the change edits normative spec text ([CLI-1.2.1],
[MA-1], [PY-2]) and the `weft status` CLI/JSON output (a public CLI contract,
the risky trigger in `writing-plans.md` "When Hardening Is Mandatory").
Plan type: implementation with spec revision. Promotion strategy B (atomic).

## Goal

`weft status` stopped listing live Managers whose runtime liveness cannot be
proven inside the reading process. Commit `b6d850c1` made status read-only
(no PING), which is correct, but the registry disposition it reused still
drops an active, unexpired row whose liveness is `unknown`. Status must keep
such a row, label it `liveness: "unknown"`, and still write nothing. Active
manager selection and leadership do not change.

## Source documents

- `docs/program-theory.md` [WT-2a.2] (queues are the only durable truth; a
  status read writes nothing), [WT-2a.13] (a missing snapshot, an expired
  window, or a failed probe is `unknown`, never proof of death), [WT-4] 4.3
  "Availability and death are different questions".
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 4: "inconclusive
  or missing probes are `unknown` evidence, not leadership authority"; the
  custody paragraph ending "Younger unknown rows keep the bounded keyed-PING
  rescue described above".
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]: "Bulk reads use no
  per-task control/outbox peeks or active PING".
- `docs/specifications/14-Python_API_Surfaces.md` [PY-2]: `ManagerSnapshot`
  liveness fields, populated before this plan only by diagnostic mode.
- `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`
  (completed; landed in `b6d850c1`). It made status passive and did not
  consider unknown active rows. This plan repairs that gap; it does not
  reverse the passive-status decision.

## Root cause (verified 2026-11-09 on 08c2f87b)

1. `weft/commands/system.py::_collect_manager_records` called
   `manager_runtime.list_manager_records(..., probe_stale=False)`.
2. `weft/core/manager_runtime.py::_manager_registry_disposition` keeps an
   active row only when its liveness is `live`, when `probe_stale` is set and a
   matched PONG rescues it, or when host-PID visibility is namespace-ambiguous.
   Before `b6d850c1` it ignored `probe_stale` and always PINGed, so status
   listed such a row after a PONG (and wrote a PING to do so).
3. `manager_registry_record_liveness` returns `unknown` for an
   `external-supervisor` handle whose liveness provider is not registered in the
   reading process (`weft/liveness/registry.py::probe_registered_runtime`
   returns `extension_probe_unavailable`).
4. The test harness Manager (`tests/helpers/weft_harness.py::ensure_foreground_manager`)
   registers a `weft-test-harness` provider only in the pytest process. A
   `weft status` subprocess sees `unknown` and drops the row; `weft manager
   list` PINGs, gets a PONG and keeps it. Reproduced on 08c2f87b:
   `tests/cli/test_cli_run.py::test_cli_run_parallel_no_wait_adopts_active_manager`
   fails with `phase: manager_convergence_timeout`, `manager_count: 0` on every
   status attempt while `manager list` shows one active Manager.
5. Production: a containerized Manager registers `external-supervisor` with
   `liveness_provider: "docker"` (`weft/core/manager.py::_manager_runtime_handle`).
   The Docker probe registers only when the Docker runner plugin loads
   (`extensions/weft_docker/weft_docker/plugin.py::get_runner_plugin`), so a
   plain `weft status` hid a live containerized Manager. A non-Docker container
   runtime has no provider at all.

## Context and key files

Files modified:

- `weft/core/manager_runtime.py`: `_manager_registry_disposition`,
  `_snapshot_registry`, `list_manager_records` (observer mode).
- `weft/commands/system.py`: `_collect_manager_records` (observer mode).
- `weft/cli/app.py`: `_render_status_snapshot` (plain-text `liveness:` line).
- `tests/commands/test_status.py`: new command-seam test.
- `tests/cli/test_cli_status_rendering.py`: plain-text rendering test.
- `tests/core/test_client.py::test_system_and_manager_namespaces_expose_shared_runtime_state`:
  it compared the status Manager snapshot field-for-field with
  `client.managers.status`. Status now fills `liveness` and manager status does
  not ([PY-2] delta), so the comparison normalizes `liveness` on both sides and
  pins `liveness == "live"` on the status row.
- Specs: `docs/specifications/10-CLI_Interface.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/14-Python_API_Surfaces.md`.
- `docs/plans/README.md` (index row, file count).
- `CHANGELOG.md` (`[Unreleased]` / Fixed entry).

Read first: `weft/core/manager_runtime.py` (`_manager_registry_disposition`,
`_snapshot_registry`, `manager_registry_record_liveness`,
`list_manager_records`, `select_active_manager`);
`weft/commands/system.py::system_status`, `_selected_active_manager_tid`,
`_collect_internal_service_snapshots`;
`weft/commands/manager.py::_manager_snapshot`.

Current structure:

- `ManagerSnapshot.liveness` already exists and already appears as `null` in
  status JSON (`_status_json_payload` serializes every dataclass field).
  `_manager_snapshot` copies `record["liveness"]` when present. No schema
  change is needed; only the value is filled.
- Status `managers` feed two other readers: `_selected_active_manager_tid`
  (recomputes liveness and requires `live` or a `_pong_live_at` stamp, so an
  unknown row is never selected) and `_collect_internal_service_snapshots`
  (`desired` is true when any listed row is `active`; it also peeks each
  listed Manager's `internal_reserved` queue, read-only).
- Status was the only caller passing `probe_stale` to `list_manager_records`;
  that keyword (added by `b6d850c1` for status) is replaced by `observe`.

Shared path, do not duplicate: `manager_registry_record_liveness` is the one
liveness reduction. Observer mode reuses the value the disposition already
computed and stamps it on the kept row, as `_manager_record_has_matched_pong`
already stamps `_pong_live_at`.

Comprehension questions (answer before the first edit):

1. Why does `weft manager list` show the harness Manager while `weft status`
   does not? Expected: `list` probes unknown rows with a keyed PING and keeps a
   matched PONG; status passes no probe, and the disposition then omits an
   unknown row.
2. Why must active-manager selection stay unchanged? Expected: unknown
   evidence is not leadership authority ([MA-1]); the status-selected TID
   drives superseded-manager reconciliation of other Managers' task snapshots,
   so an unproven row must not become the selected owner.

Answers recorded by the implementer on 2026-11-09: both as expected, verified
in code (`_select_active_manager_from_snapshot` requires `live` or
`_pong_live_at`; `_collect_task_snapshot_records` uses the selected TID).

## Invariants and constraints

- Status writes nothing: no PING, no `ctrl_in` row, no registry row
  ([WT-2a.2], [CLI-1.2.1]). Tested by an unchanged broker `last_timestamp`.
- Definitively stale active rows and unknown rows older than
  `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS` stay omitted.
- Selection and leadership (`select_active_manager`,
  `_select_active_manager_from_snapshot`, `_selected_active_manager_tid`,
  Manager leadership checks, startup) still require `live` or matched-PONG proof.
- `weft manager list`, `manager status`, and `manager list --diagnostic` keep
  their current output; non-observer disposition is unchanged.
- No new liveness reduction, no new queue, no persisted format change.
- No backward-compatibility code ([WT-2a.9]).

## Hidden couplings

- `desired` on internal-service status rows follows the listed active
  Managers. It is now true while an unknown, unexpired Manager row is listed
  (as it was before `b6d850c1` once a PONG rescued the row).
- `internal_reserved` peeks now include unknown Managers' reserved queues:
  read-only, no new write.
- `client.system.status().managers` and `client.managers.status(tid)` no
  longer agree field-for-field: only status fills `liveness`. Found by
  `tests/core/test_client.py`; recorded in the [PY-2] delta and as an owner
  question.

## Difference register

| Change | Class | Theory basis |
|--------|-------|--------------|
| `weft status` lists an active, unexpired Manager row whose liveness is `unknown` in the reading process (omitted since `b6d850c1`; before it, listed only after a matched PONG that status itself wrote a PING for) | C | [WT-2a.13]; [WT-4] 4.3; [MA-1] "missing probes are unknown evidence" |
| A Manager that died without proof (no provider loaded, container gone) stays listed as `active` with `liveness: "unknown"` until the 300 s unknown window expires (before `b6d850c1`: omitted after a failed PING) | C | [WT-2a.13]: a failed or missing probe is unknown, not death; the row is labelled, never shown as live; `weft manager list` and `--diagnostic` still probe |
| Status JSON and plain text carry `liveness` (`live` or `unknown`) on `active` Manager rows (JSON had `null`; plain text had no line) | C | Additive observation; [CLI-1.2.1] |
| Python `SystemStatusSnapshot.managers[i].liveness` is filled for `active` rows, so it no longer equals the `client.managers.status(tid)` snapshot field-for-field (that one keeps `liveness: None`) | C | Additive on status; manager status unchanged; [PY-2] |
| Status service rows report `desired: true` while an unknown, unexpired Manager row is listed | C | `desired` follows the listed active Managers; [WT-2a.13] |
| Status stays write-free (no PING) | N | [WT-2a.2]; [CLI-1.2.1] |
| Selection, leadership, startup and `weft manager list/status` are unchanged | N | [MA-1] "not leadership authority" |

No `D` rows.

## Spec baseline

- `08c2f87b` — `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/10-CLI_Interface.md`,
  `docs/specifications/14-Python_API_Surfaces.md` at plan authoring time.
- Promotion baseline identifier: the commit that lands this plan (strategy B,
  spec and code together).

## Proposed spec delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/10-CLI_Interface.md` | B — atomic | [CLI-1.2.1] implementation-mapping paragraph and "Current behavior" list; Related Plans |
| `docs/specifications/03-Manager_Architecture.md` | B — atomic | [MA-1] custody paragraph, sentence "Younger unknown rows keep the bounded keyed-PING rescue described above;"; Related Plans |
| `docs/specifications/14-Python_API_Surfaces.md` | B — atomic | [PY-2] `ManagerSnapshot` bullet, clause "diagnostic mode populates them."; Related Plans |

### [CLI-1.2.1] — insert after the bullet "shows manager registry information and task snapshots"

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
- lists each Manager's latest registry row. An `active` row is listed when its
  runtime liveness is `live`, or `unknown` and younger than
  `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`, and carries `liveness`
  (`live` or `unknown`) in JSON and plain text; a definitively stale row and
  an older unknown row are omitted. A row is `unknown` when this process
  cannot prove the Manager's runtime, for example an external-supervisor row
  whose liveness provider is not loaded here. Status sends no PING to resolve
  it and never selects an unknown row as the active Manager ([MA-1]). Other
  Manager rows carry no `liveness`
```

### [CLI-1.2.1] — implementation-mapping paragraph

Inserted after "`weft/commands/system.py` owns status collection and the
explicit task/manager/service JSON projections and `cmd_status()`;", with a
plan link appended to the paragraph:

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
`weft/commands/system.py::_collect_manager_records` reads Manager rows through
`weft/core/manager_runtime.py::list_manager_records(observe=True)`;
```

### [MA-1] — custody paragraph, the sentence "Younger unknown rows keep the bounded keyed-PING rescue described above;"

The following clause ("both the definite-stale and expired-unknown pruning
predicates override keep-newest for every manager status.") is kept unchanged
as its own sentence, now capitalized.

Replacement (`docs/specifications/03-Manager_Architecture.md`):
```text
Younger unknown rows keep the bounded keyed-PING rescue described above,
except in the passive project-status observation ([CLI-1.2.1]): it sends no
PING, keeps a younger unknown `active` row and labels it
`liveness: "unknown"`, because a missing or process-local-only probe is
unknown evidence, not proof of death. That observation is not leadership
authority; active-manager selection still requires live or matched-PONG proof.
```

### [PY-2] — `ManagerSnapshot` bullet, clause "diagnostic mode populates them."

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```text
  `canonical: bool | None = None`; diagnostic mode populates them. Project
  status populates only `liveness` (`live` or `unknown`) on `active` rows
  ([CLI-1.2.1]).
```

## Tasks

1. RED: add `test_system_status_lists_unknown_manager_without_ping` to
   `tests/commands/test_status.py`. Real broker queues. Registry rows: a live
   host-pid Manager (this test process, exact identity); an
   `external-supervisor` Manager whose `liveness_provider` is a name no process
   registers; a host-pid Manager with this process's PID and a wrong create
   time (definitively stale); an `external-supervisor` row inserted with a
   timestamp older than the unknown window. Call `system_status`. Expect
   `{live: "live", unknown: "unknown"}` exactly, no keyed PING call, no
   `ctrl_in` rows, unchanged broker `last_timestamp`. Confirm it fails on
   08c2f87b because the unknown row is missing.
2. Fix: replace `list_manager_records(probe_stale=...)` with
   `observe: bool = False`, threaded through `_snapshot_registry` to
   `_manager_registry_disposition`. In observe mode the disposition omits
   stale and expired-unknown rows, keeps the rest, stamps
   `record["liveness"]`, and never probes. Status calls
   `list_manager_records(..., observe=True)`.
3. Plain text: `_render_status_snapshot` prints `liveness: <value>` for a
   Manager row whose `liveness` is set. Rendering test in
   `tests/cli/test_cli_status_rendering.py`.
4. Apply the spec delta (strategy B, same commit) and the Related Plans
   backlinks.
5. Verify (below), commit, update this plan's execution record.

## Testing plan

- Do not mock: queues, registry rows, liveness reduction. The only patch is a
  recorder around `send_keyed_ping_probe` (to prove no PING).
- Sensitivity: revert the observe branch and confirm the new test fails on the
  missing unknown row; drop the expired-unknown omission and confirm the test
  fails on the expired row; drop the stale omission and confirm it fails on the
  stale row; force a PING in observe mode and confirm the no-PING assertions
  fail; drop the plain-text line and confirm the rendering test fails.
- Edge case in scope: the expired-unknown row (needs an explicit old
  timestamp via `Queue.insert_messages`). Out of scope: draining/stopped rows
  (the disposition does not filter them today).

## Verification and gates

Targets: the status/system/manager files plus every test file that reads
status Manager rows (found by grepping `tests/` for `system_status`,
`cmd_status`, `client.system.status`, `.managers` and CLI `status` calls).

```bash
T="tests/commands/test_status.py tests/commands/test_manager_commands.py tests/commands/test_system_public_contract.py tests/cli/test_status.py tests/cli/test_cli_status_rendering.py tests/cli/test_cli_manager.py tests/cli/test_cli_system.py tests/cli/test_cli_run.py tests/cli/test_cli_long_session.py tests/core/test_client.py tests/cli/test_internal_service_titles.py tests/system/test_helpers.py tests/commands/test_observer_command_failures.py tests/commands/test_serve.py tests/commands/test_task_evidence.py tests/commands/test_task_commands.py tests/tasks/test_command_runner_parity.py"
./.venv/bin/python -m pytest $T -n 4 -q </dev/null
./.venv/bin/python -m pytest tests/cli/test_cli_long_session.py -m "" -n 4 -q </dev/null
./bin/pytest-pg $T </dev/null
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python bin/check-doc-paths && ./.venv/bin/python bin/check-dom15-fixtures && ./.venv/bin/python -m pytest tests/specs -q
```

## Rollback

Revert the single commit. No persisted format, queue name, or registry schema
changes; no rollout order; no one-way door.

## Out of scope

- `weft manager list` keeping unknown rows after a failed PONG (spec'd rescue;
  owner question below).
- Selection, leadership, startup, pruning, the Docker probe registration path.
- Loading liveness providers into the status process.

## Deviation log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

Process deviation (declared): class 4/5 asks for independent review before
implementation. The coordinator directed implementation in an isolated
worktree and owns the cross-family review; it is recorded below.

## Owner questions

- Should `weft manager list` (default mode) also keep a younger unknown row
  after a failed PONG, labelled `unknown`, instead of omitting it? [WT-2a.13]
  says a failed probe is unknown; [MA-1] currently specifies the rescue.
- Should `weft manager list` / `manager status` also fill `liveness` (live,
  or live by matched PONG) so they match project status field-for-field? Today
  only `--diagnostic` and project status fill it.

## Review and execution record

(Coordinator fills the cross-family review here.)

Execution (2026-11-09, branch `perf/fix-status-unknown-managers` from 08c2f87b):

- RED on 08c2f87b: `test_system_status_lists_unknown_manager_without_ping`
  failed with `{'1780000000000000401': None} == {..401: 'live', ..402: 'unknown'}`
  (unknown row missing). Reproduced the owner's failure first:
  `test_cli_run_parallel_no_wait_adopts_active_manager` failed with
  `phase: manager_convergence_timeout`, `manager_count: 0` on every attempt.
- GREEN: the new test, the rendering test, and both originally failing tests
  pass, unchanged (`test_cli_run_parallel_no_wait_adopts_active_manager` on
  SQLite; `test_cli_long_session_produces_identical_transcript_across_backends`
  on PostgreSQL via `./bin/pytest-pg`, 1 passed in 351 s, and on SQLite with
  `-m ""`).
- Mutation sensitivity (5/5 killed): observer keeps only live rows (unknown
  row missing); expired-unknown omission dropped (expired row listed); stale
  omission dropped (stale row listed); observer PINGs unknown rows
  (`probe_calls == ['…402']`); plain-text line dropped (rendering test fails).
- Hidden coupling found during verification:
  `tests/core/test_client.py::test_system_and_manager_namespaces_expose_shared_runtime_state`
  failed on `liveness: 'live' != None`; normalized as described above.
- End-to-end CLI probe: a registry row with `liveness_provider: "docker"` and
  no Docker plugin loaded is listed by `weft status --json` as
  `('…501', 'active', 'unknown')` and by plain `weft status` with
  `liveness: unknown`; its `ctrl_in` stays empty and broker `last_timestamp`
  is unchanged.
- SQLite (`-n 4`): the 9 status/system/manager/run/long-session files, 349
  outcomes, 0 failed (2 PG-only skips); the 8 other status-consuming files plus
  `tests/architecture`, 410 outcomes, 0 failed; long-session with `-m ""`
  passed.
- PostgreSQL (`./bin/pytest-pg` over all 17 target files, marker `shared`,
  long-session included): 619 passed in 368.51 s.
- Gates: `ruff check .` clean; `ruff format --check` on touched Python files
  clean; repo mypy command clean (499 files); `bin/check-doc-paths`,
  `bin/check-dom15-fixtures`, `tests/specs` clean.
