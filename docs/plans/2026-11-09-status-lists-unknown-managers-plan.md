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

Revision 2 (owner, 2026-11-09) widens and changes this: `weft manager list`
and `weft manager status` use the same rows as status, and all three send one
bounded PING round to rows still `unknown`. See
"Revision 2" below; it supersedes "still write nothing".

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
| Python `ManagerSnapshot.liveness` is filled for `active` rows by status, `client.managers.list()` and `client.managers.status(tid)` alike (rev. 2; slice 1 filled it on status only) | C | Additive; owner, 2026-11-09: manager list/status "consistent with weft status"; [PY-2] |
| Status service rows report `desired: true` while an unknown, unexpired Manager row is listed | C | `desired` follows the listed active Managers; [WT-2a.13] |
| A newly retained unknown Manager's `internal_reserved` evidence now feeds status service rows (`weft/commands/system.py` ~1126): with no competing evidence, a service can change from `unknown`/`none` to `reserved`/`internal-spawn-reserved`, with queue and Manager provenance | C | [WT-4.1] "Queues carry facts; processes carry hints": the reserved row is a queue fact; read-only |
| (rev. 2) `weft status`, `weft manager list`, `weft manager status` and their client equivalents send one parallel keyed-PING round to the listed `active` rows still `unknown` after the passive reduction; added latency is at most one Manager probe bound (`min(CONTROL_SURFACE_WAIT_TIMEOUT, MANAGER_COMPETING_STARTUP_GRACE_SECONDS)` = 0.5 s) and only when such a row exists. Status is no longer write-free (slice 1 / `b6d850c1`: no PING): per call it mints one requester TID and writes one PING per unknown row | A | Owner, 2026-11-09: "I don't mind having a tiny bit more latency on it if the latency only affects the status commands and weft manager list". A probe adds positive evidence only and is never proof of death ([WT-2a.13]); a PING requester holds a TID and owns its reply `ctrl_in` ([WT-2a.12]) |
| (rev. 2) An answering unknown row reports `liveness: "live"`, `proof_source: "pong"` | C | Additive positive evidence |
| (rev. 2, revised in rev. 3) An unanswered PING is deleted from the target's `ctrl_in` by exact ID, pending or claimed, and the requester queue is retired. A target that claimed its PING before the deadline may answer after it: that late PONG leaves one row in the dead requester's own `T{tid}.ctrl_in`, which the TaskMonitor dead-TID cleanup retires once the requester TID is older than `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS` (`weft/core/monitor/task_monitor.py:3596` `TaskMonitor._run_dead_task_cleanup_slice`; `weft/core/monitor/policies/dead_task.py:143` `select_dead_task_tids_from_queue_names`, `:101` `dead_task_queue_cleanup_plan`). The rule runs only while the TaskMonitor service is enabled | A | Owner, 2026-11-09: "Named cleanup owns it." The dead-TID cleanup excludes Manager queues, so a PING left in a dead Manager's `ctrl_in` would persist; the requester queue is an ordinary dead task queue ([MF-3], [MF-5]) |
| (rev. 3) Every `send_keyed_ping_probe` caller (`weft task ping`, `task status --ping`, Manager startup and selection probes in `manager_runtime`) deletes its unanswered PING by exact ID, as the status round does; what each caller decides from a missing PONG is unchanged | A | Owner, 2026-11-09: "Yes, all callers." One probe rule; queues carry facts, and an unanswered request is not one |
| (rev. 3) A PONG matched before a later reply-read or wait failure is kept, and reading stops once every target has answered. For one target this is the baseline (`0de2fd60`) behavior, which `2f446e5c` had broken; for the round it is new | N | Positive evidence is never discarded ([WT-2a.13]) |
| (rev. 3) Replies are decoded once and matched by their `tid` (was: every pending target tried per row) | N | Linear in replies; no behavior change |
| (rev. 3) `--diagnostic` no longer probes unexpired unknown **draining** canonical Managers (`0de2fd60` probed active and draining rows; the round probes active rows only), so such a row now reports `unknown` with its runtime authority as source where a dispatch-eligible PONG made it `live`/`pong` before | D | Owner, 2026-11-09: one probe rule for the status commands and `--diagnostic`; [WT-4] 4.4 "Shared function, shared code". A draining Manager is in a one-way drain with no dispatch authority ([ALT-WT-007]), so the diagnostic's canonical and dispatch answers are unchanged, and `weft task ping TID` still proves a draining Manager live on request |
| (rev. 2) `weft manager list` / `manager status` keep an unexpired unknown row whose PING went unanswered, labelled `unknown` (they omitted it before) | C | [WT-2a.13]: a failed probe is unknown, not death |
| (rev. 2) A failed PING write leaves the row `unknown`; the command does not fail (manager list omitted the row before) | C | [WT-2a.13]; owner, 2026-11-09 |
| (rev. 2) Plain `manager list` gains a `LIVE` column; plain `manager status` gains a `Liveness:` line; their JSON gains `liveness` / `proof_source` | C | Additive; same facts as status |
| (rev. 2) `--diagnostic` probes by the same rule, in one parallel round (it probed one row at a time); a matched PONG from a Manager that is not dispatch-eligible now reports `live`/`pong` with `dispatch_eligible: false` (it reported `unknown`) | C | One probe rule (owner, 2026-11-09); liveness and dispatch eligibility are different questions ([WT-4] 4.3); canonical marking still requires dispatch eligibility |
| (rev. 2) `send_keyed_ping_probe` delegates to the new multi-target `send_keyed_ping_round`; single-target behavior is unchanged | N | [WT-4] 4.4 "Shared function, shared code" |
| Selection, leadership, startup, stop, harness internals and `weft run` Manager reuse are unchanged; a PONG proved by a status command does not make a row selectable | N | [MA-1] "not leadership authority" |
| Status service rows are not pinged: they use the service-owner reduction, whose self-refreshed owner row is the only service liveness proof | N | Owner, 2026-11-09 service-refresh decision (other track) |

One `D` row (rev. 3, `--diagnostic` draining coverage), with its theory basis.

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

Slice 1 text, superseded by revision 2 (`docs/specifications/10-CLI_Interface.md`):
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

Slice 1 text, superseded by revision 2 (`docs/specifications/10-CLI_Interface.md`):
```text
`weft/commands/system.py::_collect_manager_records` reads Manager rows through
`weft/core/manager_runtime.py::list_manager_records(observe=True)`;
```

### [MA-1] — custody paragraph, the sentence "Younger unknown rows keep the bounded keyed-PING rescue described above;"

The following clause ("both the definite-stale and expired-unknown pruning
predicates override keep-newest for every manager status.") is kept unchanged
as its own sentence, now capitalized.

Slice 1 text, superseded by revision 2 (`docs/specifications/03-Manager_Architecture.md`):
```text
Younger unknown rows keep the bounded keyed-PING rescue described above,
except in the passive project-status observation ([CLI-1.2.1]): it sends no
PING, keeps a younger unknown `active` row and labels it
`liveness: "unknown"`, because a missing or process-local-only probe is
unknown evidence, not proof of death. That observation is not leadership
authority; active-manager selection still requires live or matched-PONG proof.
```

### [PY-2] — `ManagerSnapshot` bullet, clause "diagnostic mode populates them."

Slice 1 text, superseded by revision 2 (`docs/specifications/14-Python_API_Surfaces.md`):
```text
  `canonical: bool | None = None`; diagnostic mode populates them. Project
  status populates only `liveness` (`live` or `unknown`) on `active` rows
  ([CLI-1.2.1]).
```

## Revision 2 (owner decisions, 2026-11-09)

Two owner decisions after slice 1 (`dded29e2`, reviewed in `0de2fd60`):

1. "weft manager list and weft manager status should be consistent with weft
   status." This answers both slice-1 owner questions.
2. "Status is sort of a special-purpose command, so I don't mind having a tiny
   bit more latency on it if the latency only affects the status commands and
   weft manager list." This supersedes slice 1's "status sends no PING".

Rule, identical for `weft status`, `weft manager list`, `weft manager status
TID`, `client.managers.list()` and `client.managers.status(tid)`:

1. Passive reduction first (slice 1's observe disposition): live rows kept,
   definitively stale and expired-unknown rows omitted, active unexpired
   unknown rows kept.
2. Then one probe round for the listed rows still `unknown`, and only those:
   one requester TID and reply queue, one keyed PING per row written at once,
   one wait bounded by the existing Manager probe bound. A matched PONG makes
   the row `live` with `proof_source: "pong"`. No PONG or a failed write keeps
   it `unknown` and listed; the command does not fail. Unanswered PINGs are
   deleted by exact ID; the reply queue is retired.
3. Live and stale rows are never pinged. Selection, leadership, startup, stop,
   harness internals and `weft run` reuse are unchanged; a status-command PONG
   does not stamp `_pong_live_at`, so it never makes a row selectable.
4. `--diagnostic` keeps its extra fields and uses the same round.

Services: status service rows use the service-owner reduction, not this
Manager reduction, and the owner made the self-refreshed owner row their only
liveness proof on the service-refresh track, so no service row is pinged.

Implementation:

- `weft/core/control_probe.py`: `KeyedPingTarget`, `send_keyed_ping_round`
  (multi-target round; `retract_unanswered`), helpers `_write_round_requests`,
  `_await_round_pongs`, `_read_matching_pongs`, `_retract_probe_requests`;
  `_write_probe_request` returns the PING row ID; `send_keyed_ping_probe` is
  the one-target case of the round (no retraction, unchanged contract).
- `weft/core/manager_runtime.py`: `_manager_probe_timeout` (the existing bound,
  now shared), `_ping_unknown_manager_records` (round with retraction);
  `list_manager_records(observe=True, tid=...)` runs the round after the
  passive reduction and filters; `manager_diagnostic_records` computes each
  row's liveness once and runs the same round; `_manager_record_diagnostic`
  takes that liveness and the round's PONG.
- `weft/commands/manager.py`: `cmd_manager_list` (non-diagnostic),
  `list_managers`, `manager_status` and `cmd_manager_status` read through
  `list_manager_records(observe=True)` (status by TID via
  `_observed_manager_record`). Stop paths keep `manager_record`.
- `weft/cli/app.py`: `LIVE` column in plain `manager list`, `Liveness:` line in
  plain `manager status`.

Tests (RED on `0de2fd60`, then GREEN):

- `tests/commands/test_manager_commands.py::test_status_commands_ping_only_unknown_managers_and_leave_target_queues_clean`:
  live, answering-unknown, silent-unknown, stale and expired rows; for status,
  manager list and client list the answering row is `live`/`pong`, the silent
  row `unknown`, only those two are pinged, the silent `ctrl_in` keeps no row
  (pending or claimed) and no requester queue remains; the same per TID for
  `cmd_manager_status` / `manager_status`; `--diagnostic` uses the same round;
  plain-text `LIVE` / `Liveness:` rendering. RED: status reported the answering
  row `unknown`.
- `test_status_manager_list_and_manager_status_agree_field_for_field`: the
  three commands (and both client calls) return equal `ManagerSnapshot`s;
  stale and expired rows are absent everywhere. RED: manager list returned
  fewer rows than status.
- `test_status_commands_keep_unknown_manager_when_ping_write_fails`: the first
  row's PING write raises `BrokerError`; that row stays `unknown`, the next row
  is still pinged and proved, no command fails. RED: manager list omitted the
  refused row.
- Updated: `tests/commands/test_status.py::test_system_status_lists_silent_unknown_manager_after_one_ping_round`
  (slice-1 test; now asserts one PING to the unknown row only and a clean target queue
  instead of no write) and
  `test_system_status_and_task_status_stay_passive_and_agree_when_only_active_manager_is_unproven`
  (bulk status pings the unknown Manager once and leaves its queue clean; point task
  status stays passive).
- Reverted: slice 1's `liveness` exception in
  `tests/core/test_client.py::test_system_and_manager_namespaces_expose_shared_runtime_state`;
  the field-for-field comparison holds again.

Revision 2 spec delta (strategy B, atomic with the code):

Revision 2 text, superseded by revision 3 (`docs/specifications/10-CLI_Interface.md`):
```text
no per-task control/outbox peeks or active task PING (Manager rows follow the
bounded PING round below) and may lag local completion or
```

Revision 2 text, superseded by revision 3 (`docs/specifications/10-CLI_Interface.md`):
```text
- lists each Manager's latest registry row. An `active` row is listed when its
  runtime liveness is `live`, or `unknown` and younger than
  `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`, and carries `liveness`
  (`live` or `unknown`) in JSON and plain text; a definitively stale row and
  an older unknown row are omitted. A row is `unknown` when this process
  cannot prove the Manager's runtime, for example an external-supervisor row
  whose liveness provider is not loaded here. After that passive reduction,
  status sends one round of keyed PINGs, in parallel, to the listed `active`
  rows that are still `unknown`, and waits once for the Manager probe bound
  (`min(CONTROL_SURFACE_WAIT_TIMEOUT, MANAGER_COMPETING_STARTUP_GRACE_SECONDS)`).
  A matched PONG makes the row `live` with `proof_source: "pong"`. No PONG,
  or a PING write that fails, leaves the row `unknown` and listed; it is never
  `stale` and the command does not fail. Live and stale rows are never pinged.
  Each unanswered PING is deleted by exact ID and the requester's reply queue
  is retired, so the round leaves no residue; status writes nothing else.
  `weft manager list` and `weft manager status TID` read the same rows by the
  same rule, so for one registry state the three commands report the same
  Manager rows field for field. Status never selects an unknown or
  PONG-proved row as the active Manager ([MA-1]). Other Manager rows carry
  no `liveness`
```

Revision 2 text, superseded by revision 3 (`docs/specifications/10-CLI_Interface.md`):
```text
`weft manager list` and `weft manager status TID` (and
`client.managers.list()` / `client.managers.status(tid)`) report Manager rows
exactly as project status does ([CLI-1.2.1]): the same passive reduction, the
same single PING round for rows still `unknown`, the same `liveness` and
`proof_source` fields. Plain `manager list` shows a `LIVE` column and plain
`manager status` a `Liveness:` line when the row has one.
`weft manager list --diagnostic` keeps the default manager-list semantics behind
an explicit operator flag: it reads the latest known manager service-owner rows
without stale pruning, classifies each row as live/stale/unknown/non-live,
reports the proof source (`host-pid`, `external-supervisor`, `pong`, or registry
metadata), and marks the selected canonical live owner. Its probing is the
status-command rule: one PING round for active canonical rows still `unknown`
and unexpired; a matched PONG reports `live` with source `pong`, and
`dispatch_eligible` additionally requires the PONG to prove dispatch
eligibility. It is a diagnostic view of `weft.state.services`, not a fallback
process table.
```

Revision 2 text, superseded by revision 3 (`docs/specifications/03-Manager_Architecture.md`):
```text
Younger unknown rows keep the bounded keyed-PING rescue described above.
The status commands (`weft status`, `weft manager list`, `weft manager
status`; [CLI-1.2.1]) apply it as one parallel round to the rows still
unknown and keep an unanswered row labelled `liveness: "unknown"` instead of
omitting it, because a missing probe or a missing PONG is unknown evidence,
not proof of death; their unanswered PINGs are retracted by exact ID. That
observation is not leadership authority; active-manager selection still
requires live or matched-PONG proof.
```

Revision 2 text, superseded by revision 3 (`docs/specifications/14-Python_API_Surfaces.md`):
```text
  `canonical: bool | None = None`; diagnostic mode populates them. Project
  status, `client.managers.list()` and `client.managers.status(tid)` populate
  `liveness` (`live` or `unknown`) on `active` rows and `proof_source: "pong"`
  when a PONG proved the row; for one registry state their snapshots are
  equal ([CLI-1.2.1]).
```

Revision 2 text, superseded by revision 3 (`docs/specifications/05-Message_Flow_and_State.md`):
```text
Bulk acquisition performs no per-task control/output reads, active task PING,
implicit global-history replay, or Monitor reads. The only write a status
command makes is the bounded keyed PING round to Manager rows whose liveness
is still unknown ([CLI-1.2.1]); it leaves no residue.
```

Revision 2 text, superseded by revision 3 (`docs/specifications/07-System_Invariants.md`):
```text
  This reconciliation writes nothing; the only write a status command makes
  is the bounded keyed PING round to Manager rows still `unknown`, which
  leaves no residue and selects no Manager ([CLI-1.2.1]). (Plans:
  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`,
  `docs/plans/2026-11-09-status-lists-unknown-managers-plan.md`)
```

## Revision 3 (cross-family review of `2f446e5c`, owner decisions 2026-11-09)

Review findings and dispositions:

- **F1 (P2, accepted): late reply after retraction.** A Manager can claim a
  PING before the deadline and PONG after the round deleted the PING and the
  reply queue; the reply write recreates the requester queue. Owner: "Named
  cleanup owns it." The target's queue stays clean: the round deletes each
  unanswered PING by exact ID, pending or claimed (`delete_message_ids` deletes
  both). The one late row lands in the dead requester's own `T{tid}.ctrl_in`.
  The existing rule that retires it, confirmed in code: TaskMonitor's dead-TID
  cleanup slice, `weft/core/monitor/task_monitor.py:3596` `TaskMonitor._run_dead_task_cleanup_slice` (selection in `_select_dead_task_cleanup_slice`, `:3502`; deletion in `_delete_dead_task_control_queues`, `:2527`), with `weft/core/monitor/policies/dead_task.py:143` `select_dead_task_tids_from_queue_names` and `:101` `dead_task_queue_cleanup_plan`. The requester TID has no runtime and no
  Monitor record, so once it is older than
  `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS` the name-derived TID-age rule
  selects it, and `standard_dead_task_stale_queue_names` (`dead_task.py:82`)
  makes its `ctrl_in` stale at once. The exclusion of Manager, global and
  service queues does not apply: the requester TID is not a Manager. Caveat:
  the rule runs only while the TaskMonitor service is enabled
  (`TASK_MONITOR_ENABLED`, default on). Every "zero residue" statement is
  replaced with this one in the register, specs and docstrings.
- **F2 (P2, accepted): later read failure discarded matched proof.**
  `_read_matching_pongs` records each match into the caller's results as it
  arrives and stops reading once no target is pending; `_await_round_pongs`
  writes into the caller's results too, so a later read or wait failure keeps
  every earlier match. Single-target probes are back to the baseline behavior.
- **F3 (P3, accepted): quadratic matching.** Each reply is decoded once
  (`decode_control_object`), its `tid` selects the pending target, and
  `_matched_pong_payload` validates the request key and envelope.
  `coerce_pong_response` is that decode plus the same validation.
- **F4 (P3, accepted as D):** see the `--diagnostic` draining row in the
  register.

Owner decision, 2026-11-09: "Yes, all callers." Every `send_keyed_ping_probe`
caller now deletes its unanswered PING by exact ID, through the same round
(the `retract_unanswered` switch is gone). What each caller decides from the
result is unchanged.

Tests (RED first on `2f446e5c` where a behavior changed):

- `tests/core/test_control_probe.py::test_send_keyed_ping_probe_keeps_match_when_a_later_reply_read_fails`
  (single target, baseline contract). RED: `error='injected reply read failure'`, no match.
- `test_ping_round_keeps_earlier_match_when_a_later_reply_read_fails`. RED: the
  answering target lost its match.
- `test_ping_round_keeps_earlier_match_when_a_later_wait_fails`. RED: the
  answering target was missing from the results (`KeyError`).
- `test_unclaimed_ping_is_retracted_when_the_probe_times_out` (replaces the
  unclaimed half of the old late-PONG test). RED: the PING stayed in the
  target's `ctrl_in`.
- `test_claimed_late_pong_stays_with_dead_requester_until_existing_sweep_selects_it`:
  the target claims its PING before the deadline and answers after it; the
  target's `ctrl_in` keeps no row, the requester queue holds the late PONG, and
  the dead-TID selection picks the requester TID only once it is old enough.
- `tests/tasks/test_task_monitor.py::test_dead_tid_cleanup_retires_the_requester_queue_of_a_late_status_pong`:
  the same delayed responder against the status round, then the TaskMonitor
  dead-TID cleanup slice driven directly at the required age (no sleeps); the
  requester queue is gone afterwards.
- `tests/commands/test_task_commands.py::test_task_ping_against_a_silent_target_leaves_its_ctrl_in_clean`.
  RED: the PING stayed in the target's `ctrl_in`.

Revision 3 spec delta (strategy B, atomic with the code):

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
- lists each Manager's latest registry row. An `active` row is listed when its
  runtime liveness is `live`, or `unknown` and younger than
  `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`, and carries `liveness`
  (`live` or `unknown`) in JSON and plain text; a definitively stale row and
  an older unknown row are omitted. A row is `unknown` when this process
  cannot prove the Manager's runtime, for example an external-supervisor row
  whose liveness provider is not loaded here. After that passive reduction,
  status sends one round of keyed PINGs, in parallel, to the listed `active`
  rows that are still `unknown`, and waits once for the Manager probe bound
  (`min(CONTROL_SURFACE_WAIT_TIMEOUT, MANAGER_COMPETING_STARTUP_GRACE_SECONDS)`).
  A matched PONG makes the row `live` with `proof_source: "pong"`. No PONG,
  or a PING write that fails, leaves the row `unknown` and listed; it is never
  `stale` and the command does not fail. Live and stale rows are never pinged.
  Each unanswered PING is deleted from the Manager's `ctrl_in` by exact ID,
  pending or claimed, and the requester's reply queue is retired. A Manager
  that claimed its PING before the deadline may still answer after it; that
  late PONG leaves one row in the requester's own `T{tid}.ctrl_in`, which the
  TaskMonitor dead-TID cleanup retires ([MF-3]). Status writes nothing else.
  `weft manager list` and `weft manager status TID` read the same rows by the
  same rule, so for one registry state the three commands report the same
  Manager rows field for field. Status never selects an unknown or
  PONG-proved row as the active Manager ([MA-1]). Other Manager rows carry
  no `liveness`
```

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
no per-task control/outbox peeks or active task PING (Manager rows follow the
bounded PING round below) and may lag local completion or
```

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
`weft manager list` and `weft manager status TID` (and
`client.managers.list()` / `client.managers.status(tid)`) report Manager rows
exactly as project status does ([CLI-1.2.1]): the same passive reduction, the
same single PING round for rows still `unknown`, the same `liveness` and
`proof_source` fields. Plain `manager list` shows a `LIVE` column and plain
`manager status` a `Liveness:` line when the row has one.
`weft manager list --diagnostic` keeps the default manager-list semantics behind
an explicit operator flag: it reads the latest known manager service-owner rows
without stale pruning, classifies each row as live/stale/unknown/non-live,
reports the proof source (`host-pid`, `external-supervisor`, `pong`, or registry
metadata), and marks the selected canonical live owner. Its probing is the
status-command rule: one PING round for active canonical rows still `unknown`
and unexpired; a matched PONG reports `live` with source `pong`, and
`dispatch_eligible` additionally requires the PONG to prove dispatch
eligibility. It is a diagnostic view of `weft.state.services`, not a fallback
process table.
```

Replacement (`docs/specifications/10-CLI_Interface.md`):
```text
A synchronous keyed PING owns an ephemeral reply queue whose lifetime is
bounded by the dead-task cleanup age. A PING still unanswered at the end of
the probe is deleted from the target's `ctrl_in` by exact ID, so `weft task
ping` against a silent target leaves that queue clean; a late PONG lands only
in the requester's own reply queue, which the dead-TID cleanup retires
([MF-3]). `--timeout` must be finite and satisfy
```

Replacement (`docs/specifications/03-Manager_Architecture.md`):
```text
   send one bounded keyed PING to that manager's task-local control queue. A
   synchronous startup or selection probe deletes its PING by exact ID when it
   ends unanswered ([MF-3]); the running Manager's non-blocking leadership
   probe below does not. A
```

Replacement (`docs/specifications/03-Manager_Architecture.md`):
```text
Younger unknown rows keep the bounded keyed-PING rescue described above.
The status commands (`weft status`, `weft manager list`, `weft manager
status`; [CLI-1.2.1]) apply it as one parallel round to the rows still
unknown and keep an unanswered row labelled `liveness: "unknown"` instead of
omitting it, because a missing probe or a missing PONG is unknown evidence,
not proof of death; like every synchronous probe, they delete an unanswered
PING by exact ID ([MF-3]). That observation is not leadership authority;
active-manager selection still requires live or matched-PONG proof.
```

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```text
  `canonical: bool | None = None`; diagnostic mode populates them. Project
  status, `client.managers.list()` and `client.managers.status(tid)` populate
  `liveness` (`live` or `unknown`) on `active` rows and `proof_source: "pong"`
  when a PONG proved the row; for one registry state their snapshots are
  equal ([CLI-1.2.1]).
```

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```text
  task queue. Every synchronous probe (`weft task ping`, `task status --ping`,
  Manager startup and selection probes, and the status-command
  round of [CLI-1.2.1]) deletes each PING still unanswered at its end from the
  target's `ctrl_in` by exact ID, pending or already claimed, so the target's
  queue keeps no probe row. A target that claimed its PING before the deadline
  may still answer after it; that late PONG, or a reply written after
  requester death, recreates the requester's own `T{tid}.ctrl_in` with one
  row. The TaskMonitor dead-TID cleanup retires that queue once the requester
  TID is older than `TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS`: the
  requester has no runtime and no Monitor record, so the name-derived TID-age
  rule selects it and deletes its stale `ctrl_in`
  (`weft/core/monitor/task_monitor.py::TaskMonitor._run_dead_task_cleanup_slice`,
  `weft/core/monitor/policies/dead_task.py::select_dead_task_tids_from_queue_names`
  and `dead_task_queue_cleanup_plan`). The rule runs only while the TaskMonitor
  service is enabled.
```

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```text
Bulk acquisition performs no per-task control/output reads, active task PING,
implicit global-history replay, or Monitor reads. The only write a status
command makes is the bounded keyed PING round to Manager rows whose liveness
is still unknown ([CLI-1.2.1]). It leaves no row in a Manager's `ctrl_in`; a
late PONG leaves at most one row in the requester's own reply queue, which the
dead-TID cleanup named in [MF-3] retires.
```

Replacement (`docs/specifications/07-System_Invariants.md`):
```text
  This reconciliation writes nothing; the only write a status command makes
  is the bounded keyed PING round to Manager rows still `unknown`, which
  leaves no row in a Manager's `ctrl_in`, at most one late-PONG row in its own
  requester queue for the dead-TID cleanup ([MF-3]), and selects no Manager
  ([CLI-1.2.1]). (Plans:
  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`,
  `docs/plans/2026-11-09-status-lists-unknown-managers-plan.md`)
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

Both slice-1 questions were answered on 2026-11-09 ("weft manager list and
weft manager status should be consistent with weft status"); see Revision 2.

Open (revision 3): the running Manager's own leadership probes are not
`send_keyed_ping_probe` calls. They are non-blocking reactor probes
(`weft/core/manager.py::Manager._manager_pong_dispatch_proof`, PING written
at the `encode_control_message(CONTROL_PING, ...)` site near line 4118 and
resolved as `ping_unanswered` in `_advance_manager_pong_probe`), and an
unanswered one still stays in the peer Manager's `ctrl_in`. "Yes, all callers"
was applied to every `send_keyed_ping_probe` caller (status commands, `weft
task ping`, `task status --ping`, Manager startup and selection probes in
`weft/core/manager_runtime.py`). Should the reactor probe also keep its PING
row ID and delete it when it resolves `ping_unanswered`? Not done here.

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

### Cross-family review (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-09,
plan and implementation at `dded29e2` against `08c2f87b`. Verdict: no blocker.
F1 (P3): the register omitted the service-output effect of retained unknown
Managers' `internal_reserved` evidence. Accepted, fixed (C row added; no
runtime change). The reviewer confirmed the three-valued evidence informs
observation while selection still requires positive proof, and ran an
in-memory check of the actual disposition functions over 96 predicate
combinations: non-observer behavior matched baseline; observer behavior
matched retention, labeling and no-PING expectations. Broker-backed tests and
the mutation suite were not rerun by the reviewer (read-only). Review loop
closed.

### Revision 2 execution (2026-11-09, on `0de2fd60`)

- RED on `0de2fd60` (source files swapped back to HEAD, tests kept): the
  answering row read `('unknown', None)` instead of `('live', 'pong')` in
  status; manager list returned fewer rows than status; manager list omitted
  the row whose PING write failed. All three GREEN after the change.
- Mutation sensitivity, 9/9 killed: no PING round in observe mode; PING every
  listed row (live too); unanswered PING not retracted; unanswered unknown row
  dropped as dead; PONG without `proof_source`; manager status on the old
  probing path; a failed write aborting the round (killed only after the
  write-failure test gained a second, answering row); `--diagnostic` without
  the round; plain `manager status` without the `Liveness:` line.
- End-to-end CLI probe on a temp context with one `liveness_provider: "docker"`
  row and no Docker plugin loaded: `weft status --json`, `weft manager list
  --json` and `weft manager status TID --json` each report
  `('active', 'unknown')` in 1.15-1.18 s wall time per CLI process (Python
  start-up plus the 0.5 s probe bound); plain text shows the `LIVE` column and
  `Liveness: unknown`; afterwards the row's `ctrl_in` holds no pending or
  claimed row and no `.ctrl_in` queue remains.
- SQLite (`-n 4`): `tests/commands/test_status.py`,
  `tests/commands/test_manager_commands.py`, `tests/core/test_control_probe.py`,
  `tests/core/test_client.py` and 17 more status/manager/run/CLI files
  (including `tests/cli/test_cli_run.py`, `tests/commands/test_run.py`,
  `tests/test_harness_registration.py`): 1,017 outcomes, 1 failure,
  `tests/cli/test_cli_status_rendering.py::test_task_list_and_task_status_json_use_status_task_json_payload_for_wall_clock_timestamps`,
  which passes alone and with its own file under `-n 4`. Cause, pre-existing
  and unrelated: the test monkeypatches `weft.commands.tasks.cmd_task_status`,
  but the CLI calls the `weft.commands` facade, whose `__getattr__` caches the
  real function the first time any earlier test in the same xdist worker runs
  `weft task status` in-process. Long-session with `-m ""` on SQLite: passed.
- PostgreSQL (`./bin/pytest-pg`): the status, manager, control-probe, client,
  system-contract, CLI status/rendering/manager/system/run and
  `tests/commands/test_run.py` files plus `tests/cli/test_cli_long_session.py`:
  547 passed in 380.33 s.
- Gates: `ruff check .`, `ruff format --check` on touched Python files, repo
  mypy command (499 files), `bin/check-doc-paths`, `bin/check-dom15-fixtures`,
  `tests/specs`: clean.

### Revision 3 execution (2026-11-09, on `2f446e5c`)

- RED on `2f446e5c` (source swapped back to HEAD, tests kept):
  `test_send_keyed_ping_probe_keeps_match_when_a_later_reply_read_fails`
  (`error='injected reply read failure'`, no match),
  `test_ping_round_keeps_earlier_match_when_a_later_reply_read_fails` (match
  lost), `test_ping_round_keeps_earlier_match_when_a_later_wait_fails`
  (`KeyError`: the answering target missing),
  `test_unclaimed_ping_is_retracted_when_the_probe_times_out` and
  `test_task_ping_against_a_silent_target_leaves_its_ctrl_in_clean` (the PING
  stayed in the target's `ctrl_in`). All GREEN after the change.
- Mutation sensitivity for F1 and F2, 5/5 killed: keep reading after every
  target answered (single-target read count); matches not recorded as they
  arrive; round results merged only after the wait loop; unanswered PING not
  retracted; the named dead-TID rule no longer treating `ctrl_in` as stale.
- SQLite (`-n 4`): `tests/core/test_control_probe.py`, `test_status.py`,
  `test_manager_commands.py`, `tests/core/test_client.py`,
  `test_task_commands.py`, `test_task_evidence.py`,
  `test_system_public_contract.py`, the CLI status/rendering/manager/system/
  run/serve files, `tests/commands/test_run.py`,
  `tests/test_harness_registration.py`, the Manager and task runtime
  connection files and `tests/tasks/test_task_monitor.py`: 1,113 outcomes,
  0 failed. Long-session with `-m ""`: passed.
- PostgreSQL (`./bin/pytest-pg`, the same files plus
  `tests/cli/test_cli_long_session.py`): 968 passed, 1 skipped in 380.54 s.
- Gates: `ruff check .`, `ruff format --check` on touched Python files, repo
  mypy command (499 files), `bin/check-doc-paths`, `bin/check-dom15-fixtures`,
  `tests/specs`: clean.
