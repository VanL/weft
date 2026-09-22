# Runtime Identity Custody Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [LIVENESS.R2], [LIVENESS.R4], [LIVENESS.R5], [OBS.6a]; docs/specifications/01-Core_Components.md [CC-3.2], [CC-2.4.1]; docs/specifications/03-Manager_Architecture.md [MA-1]; docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
Superseded by: none

Class: 5 — reclassified from 4 after the Codex pre-implementation review:
[CC-3.2] never states when a completed worker's handle stops being a
task's current mapping identity, so this plan adds that normative rule
(exact delta below, promotion strategy **B — atomic**: [CC-3.2] already
carries a broad implementation mapping, so strategy A would make new
requirements appear implemented during the gap; text, code, and backlinks
land in one slice), and it changes task process-control behavior on the
durable spine, so the hardening checklist applies. Plan type: implementation with spec revision. Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

A task never releases the identity of workers it has finished with. Three
defects follow from that one omission, verified 2026-08-31 with
real-broker probes (the probe setup is reproduced in §6 so it is not a
scratchpad dependency):

1. `BaseTask._managed_pids` only grows (`register_managed_pid`, base.py
   ~:2286; no `discard`/`clear` anywhere in `weft/`). OS-signal
   termination — `weft manager stop`, parent loss via `launcher.py`, an
   operator `kill -TERM` — reaches `Consumer.handle_termination_signal`
   (consumer.py ~:1142) → `_terminate_active_worker` (~:1202) and
   `BaseTask.handle_termination_signal` (base.py ~:2026), which call
   `terminate_process_tree`/`kill_process_tree` (helpers/__init__.py ~:400,
   ~:483) on **every historical worker PID with no identity check**. After
   PID wraparound an unrelated same-UID process can be signaled.
2. `Consumer._register_outcome_runtime` (consumer.py ~:301) publishes the
   finished worker's host-pid handle as the task's `_runtime_handle`
   (`register_runtime_handle`, base.py ~:2298; assignment sites ~:2328 and
   ~:2741 only). `_build_tid_state_payload` (~:2331) publishes
   `self._runtime_handle or self._task_process_runtime_handle()`, so after
   its first work item a persistent task's mapping row carries only a dead
   PID: `endpoints.py::_record_owner_is_live` classifies the owner dead
   and `list_resolved_endpoints` deletes the claim — every persistent
   `weft run --name` task loses its endpoint after one item — and
   `weft/liveness/analysis.py::analyze_liveness` returns `stale` for an
   idle persistent task, so the LivenessMonitor would retire its mapping
   row after the minimum age (a dead exact identity is definitively
   `stale` per [CC-3.2]'s point-in-time evidence rule, ~:785; [LIVENESS.R4]
   supplies only the retirement timing).
3. When `register_runtime_handle` merges a replacement handle with
   `_managed_pids`, a historical managed PID absent from that handle's
   `host_processes` is observed again by `_merge_host_process_observations`
   (base.py ~:159). A reused PID can then acquire its new owner's
   `create_time`, defeating the gated `_stop_registered_runtime_handle`
   path (~:2750). Identities already present in `existing_processes` are
   preserved today: `dict.get` eagerly evaluates the OS-query default but
   does not use that value for an existing key. The defect is missing
   custody across handle replacement, not replacement of an existing key.

This plan makes worker identity acquire/release symmetric, makes every
direct signal identity-checked with `None` meaning *no authority*, and
keeps runner-authority processes off the direct-signal path entirely. It
adds no process registry abstraction and does not change any Protocol.

## 2. Source Documents

- 07 [LIVENESS.R5]: host identity is scoped `(pid, create_time)` with
  zombie rejection; "registration grants no control authority".
  [LIVENESS.R2]: liveness evidence never authorizes a process signal.
  [LIVENESS.R4]: a dead exact identity is `stale` and retires after
  minimum age.
- 01 [CC-3.2]: "control and observability must work through a durable
  runtime handle, not only through a host PID"; `control.authority`
  determines who may act; task processes publish a `host-pid` handle for
  their own process when no runner-specific handle exists; **completed
  outcomes retain their runtime handle** (~:774) — the sentence this plan
  disambiguates.
- 03 [MA-1] (~:296): the manager force-reaps only tracked-child or
  scoped-handle authority — precedent for identity-gated control, not the
  governing BaseTask rule.
- 05 [MF-3.1]: endpoint liveness uses `weft.state.tid_mappings` +
  `runtime_handle`.
- Guidance: `CLAUDE.md` §1.1, §4; `docs/agent-context/engineering-principles.md`.

## 3. Context and Key Files

Files to modify:
- `weft/core/tasks/base.py` — `register_managed_pid` (~:2286),
  `register_runtime_handle` (~:2298), `_merge_host_process_observations`
  (~:159), `_build_tid_state_payload` (~:2331),
  `_task_process_runtime_handle` (~:2704), `handle_termination_signal`
  (~:2026), `_stop_registered_runtime_handle` (~:2750)
- `weft/core/tasks/consumer.py` — `_register_outcome_runtime` (~:301),
  `_register_running_worker` (~:822), `handle_termination_signal`
  (~:1142), `_terminate_active_worker` (~:1202)
- `weft/helpers/__init__.py` — `pid_matches_create_time` (~:102; today
  `create_time is None` falls back to `pid_is_live`, a PID-only check),
  `terminate_process_tree`/`kill_process_tree` (construct a fresh
  `psutil.Process` from the bare PID); **new** `terminate_verified_process_tree(pid,
  create_time, *, kill, timeout)` — the single owner of atomic
  identity-check-plus-tree-control: acquire one `psutil.Process`, compare
  its `create_time` to the recorded value, and terminate the tree through
  that same instance (descendants enumerated from it); refuse when
  `create_time is None` or mismatched
- `weft/core/runners/host.py` — `HostRunnerPlugin.stop`/`kill` (~:1222–1240)
  today do `_host_pid_matches` (PID-only when `create_time is None`) and
  then call the tree helpers with a bare PID — the same race, reached
  **before** the task loops via `_stop_registered_runtime_handle`. Both
  switch to the verified helper. `tests/tasks/test_runner.py::test_host_runner_plugin_skips_pid_identity_mismatch`
  (~:111) pins the current shape and is updated.
- `weft/commands/tasks.py` — `_stop_via_fallback`, `_kill_via_fallback`,
  `_force_kill_task_processes` (~:1469–1565) use `pid_matches_create_time`
  then bare-PID signaling; they switch to the verified helper too (the
  first draft wrongly called them "already gated")
- Tests: `tests/core/test_subprocess_runner.py`, `tests/tasks/test_runner.py`
  (the existing runner coverage — there is no `tests/core/test_runtime_handle*.py`),
  `tests/tasks/test_task_endpoints.py`, `tests/cli/test_cli_run.py`
  (`test_cli_run_persistent_spec_name_claims_and_releases_endpoint`,
  ~:2160), new `tests/tasks/test_runtime_identity_custody.py`

**Not modified**: `weft/core/runners/outcome.py::RunnerOutcome` — it has
no reap field (`status, value, error, stdout, stderr, returncode,
duration, metrics, runtime_handle, diagnostics`) and this plan does not
add one; the reap discriminator is defined in task 3a instead.
**Not modified** (Codex B3): `weft/ext.py::TaskRunnerBackend.run_with_hooks`
(~:390–400) is a declared Protocol carrying the `on_worker_started`
callback signature, implemented by the host runner, `subprocess_runner.py`
(shared by several backends), Docker, macOS sandbox, and Microsandbox. The
first draft's `on_worker_exited` callback would have changed that Protocol
and every backend. Release happens instead at the existing outcome
boundary (task 4).

Read first: the spec sections above; `weft/liveness/host.py::inspect_host_process`
(the tri-state identity inspector to reuse); `weft/core/runners/host.py`
worker join (~:516–521) to see where reap is confirmed.

Shared paths — reuse, do not duplicate: `inspect_host_process` is the
identity evaluator; `_stop_registered_runtime_handle` is the plugin-gated
stop path; `_register_tid_state` is the edge-triggered republish
([OBS.6a]). Acquisition uses `register_runtime_handle`; the host outcome
release clears the active handle and calls `_register_tid_state`, whose
existing fallback publishes the task-process handle. Do not change
`register_runtime_handle(None)` from its current no-op behavior.

Comprehension check before editing: (a) which two sites assign
`self._runtime_handle`, and why does the task's own PID vanish from the
payload after the first item? (b) which signal-delivery paths reach the
ungated PID loop, and which reach the gated plugin path? (c) what does
`pid_matches_create_time` return for `create_time=None`, and why is that
the wrong default for signaling? (d) why must a task-process mapping
fallback never be installed as that task's active worker-control handle?

## 4. Invariants and Constraints

- No weft path may signal a process whose `(pid, create_time)` does not
  match an identity weft recorded **at registration time** ([LIVENESS.R5]).
  `create_time=None` means **no direct signal authority**: such a PID is
  never signaled directly (Codex B1 — the PID-only fallback would signal
  a recycled PID).
- The identity check and the signal use the **same** `psutil.Process`
  object: verify the recorded `create_time` on the object you are about
  to signal, not in a separate precheck (Codex B1 — exit-then-reuse
  between precheck and signal).
- Authority routing follows [CC-3.2] exactly: `host-pid` → the verified
  tree helper; `runner` → the plugin's `stop`/`kill` only;
  `external-supervisor` → **no runtime control from Weft at all** (today's
  `_stop_registered_runtime_handle` already returns without a plugin call
  for it — keep that). Neither of the last two is ever PID-signaled
  directly (Codex B2 — Microsandbox reports a host PID with no
  `create_time` under runner authority).
- **Reap discriminator** (Codex round 2, B1): a PID is released only when
  it was registered through `on_worker_started` for a **one-shot host
  `run_with_hooks` call that has returned** — the host runner joins the
  worker before returning (`weft/core/runners/host.py` ~:516–521; verify
  and pin with a test), so the return is the reap proof. Persistent
  agent-session PIDs (registered from the session path, consumer.py
  ~:1254) are **not** released by this rule; their session owns the
  process object and releases on session teardown. `RunnerOutcome` gains
  no field.
- After that release, the task's newest mapping row carries **only the
  task-process handle** (`runner="host"`, `kind="process"`,
  `control.authority="host-pid"`, `host_processes` == the task's own
  `(pid, create_time)`) — exact result, not "task PID present somewhere"
  (Codex B4). `RunnerOutcome.runtime_handle` remains historical outcome
  metadata and is not republished for a released worker.
- The release leaves `_runtime_handle is None`; the task-process handle
  comes only from `_build_tid_state_payload`'s existing fallback. The
  current task is absent from `_managed_pids` and from task-internal
  worker-control targets. An idle task handles termination by recording
  its terminal state and performing normal cleanup, never by sending a
  process-tree signal to itself. External task control may still use its
  published mapping handle.
- Unknown `create_time` trade-off, made explicit: refusing to signal can
  leave a live worker. The surviving cleanup mechanism is the owned
  process object (the runner's `Popen`/`Process` for the active item;
  sessions own theirs); when no owned object remains, skip, log at
  WARNING naming the survivor, and never fall back to PID-only signaling.
- Identity store: `_managed_pids` becomes a single `dict[int, float | None]`
  (PID → recorded `create_time`); release `pop`s the entry. No parallel
  set (Codex: a set plus a dict leaks one entry per worker and can
  reject a legitimate new worker at a reused PID).
- Mapping publication stays edge-triggered and append-only ([OBS.6a]).
- Manager-side child shutdown (manager.py ~:3765–3790) is not touched.
  The `weft task kill` fallbacks (commands/tasks.py ~:1469–1565) are in
  scope for the verified helper, as listed in §3 and task 3b.

Review gates: no Protocol change; no new abstraction; real subprocess
workers in tests; external review before implementation.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 07, 01, 03, 05 at plan authoring time (2026-08-31).
  Promotion baseline identifier: recorded after task 1.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/01-Core_Components.md | B — atomic (text + code + backlinks in one slice) | [CC-3.2] — insert after the "completed outcomes retain their runtime handle" sentence (~:774) |

### [CC-3.2] — insert

> A completed outcome's runtime handle is historical metadata for that
> outcome. For a one-shot host worker, the return of the runner call that
> joined the worker is the reap proof: the task then releases that
> worker's identity, and its newest TID mapping carries only the task
> process's own `host-pid` handle — never a reaped worker's identity. A
> task's own mapping identity is not an active worker-control target:
> task-internal termination must not signal the task process itself.
> A released worker PID grants no control authority. Session-held runtimes are
> released by their session's teardown, not by this rule. Direct host
> control acts through the same acquired process instance whose
> `create_time` was matched; a recorded host identity whose `create_time`
> is unknown grants no direct signal authority. Per `control.authority`,
> `runner` runtimes are controlled only through their plugin and
> `external-supervisor` runtimes receive no runtime control from Weft;
> neither is signaled by PID.

## 5. Tasks

1. **Independent review** of this plan and the delta (§8) — before any
   spec or code change (runbook order: plan → delta review → promotion).
2. **Red tests** (`tests/tasks/test_runtime_identity_custody.py`; real
   broker via `broker_env`; real worker subprocess from
   `tests/tasks/sample_targets`; handlers invoked **directly** — never
   deliver a real SIGTERM to the pytest process):
   - (a) Persistent function-target task; one work item; after
     `work_item_completed`, the newest `weft.state.tid_mappings` row's
     `runtime_handle` has `runner == "host"`, `kind == "process"` (the
     only valid kinds are `process`, `container`, `sandboxed-process`,
     `supervised-process`), `id` == the task process PID,
     `control.authority == "host-pid"`, `host_processes` == exactly the
     task's `(pid, create_time)`, and the reaped worker PID is absent;
     `analyze_liveness(...)` is `live`. Red today.
   - (b) The named-endpoint variant is covered by extending the existing
     `tests/cli/test_cli_run.py::test_cli_run_persistent_spec_name_claims_and_releases_endpoint`
     to send one work item before asserting the claim persists — one
     scenario, one place (Codex: do not add three versions).
   - (c) After the item, the identity store has no entry for the worker
     PID and `_runtime_handle is None`. Red today. Extend the signal
     matrix below with this post-item idle state: no worker-control
     helper or plugin is invoked, and graceful/kill handling still
     records the expected terminal state and envelope. Keep actual signal
     delivery confined to isolated task subprocesses, never pytest.
   - (d) Signal gates, as a parametrized matrix over {`BaseTask`,
     `Consumer`} × {graceful, kill} × {**known-matching identity with a
     descendant** (the correct tree IS terminated — this cell keeps an
     implementation that skips everything from passing), known-mismatched
     identity (survives, logged), unknown `create_time` (survives, WARNING
     names the survivor), runner-authority handle (plugin `stop`/`kill`
     called exactly once, no direct tree signal), external-supervisor
     handle (neither plugin nor PID signal)}. Red today for the direct-loop
     and host-plugin cells.
   - (e) Register worker A, then replace the runtime handle with worker
     B's handle, which omits A from `host_processes`. A must retain its
     registration-time identity even if the OS now reports a different
     creation time; it must not be observed again. This is the failing
     custody sequence to prove before the fix. A PID already present in
     `existing_processes` keeping its creation time is green today and
     remains characterization coverage. Only genuinely new PIDs may be
     observed. Creation-time changes use controlled observation inputs;
     do not depend on OS PID wraparound.
   - (f) Host runner reap proof: after a one-shot `run_with_hooks` returns,
     the worker PID is not alive (pins the join at host.py ~:516–521 that
     task 3a relies on).
3. **Atomic slice (strategy B), with three implementation steps.**
   3a *release at the outcome boundary* — no callback. In the Consumer's
   outcome commit path, for PIDs registered via `on_worker_started` during
   a one-shot host `run_with_hooks` call that has returned: `pop` the PID
   from the identity store, clear `_runtime_handle` to `None`, and call
   `_register_tid_state()` once for that release edge. Its existing
   fallback publishes the task-process handle without storing self as an
   active worker-control target. Keep the clear and republish together
   at this outcome boundary; do not add a second identity store or change
   the meaning of `register_runtime_handle(None)`.
   `_register_outcome_runtime` must not
   republish a released worker's handle; session-held PIDs and every
   non-host backend are untouched (their returned handles publish as
   today).
   3b *verified process-tree control* — add `terminate_verified_process_tree`
   to helpers (one acquired `psutil.Process`; compare `create_time`;
   enumerate descendants and terminate through that instance; refuse on
   `None` or mismatch); switch `HostRunnerPlugin.stop`/`kill` and the
   `weft task kill` fallbacks to it; update
   `test_host_runner_plugin_skips_pid_identity_mismatch`.
   3c *identity storage and caller routing* — `_managed_pids` becomes
   `dict[int, float | None]`; `_merge_host_process_observations` reads
   recorded values for known PIDs; `handle_termination_signal` /
   `_terminate_active_worker` route per authority: `host-pid` → the
   verified helper; `runner` → `_stop_registered_runtime_handle` (plugin);
   `external-supervisor` → nothing; a recorded PID with unknown
   `create_time` → skip + WARNING. No active handle and no managed worker
   is the normal idle state and requires no warning or process signal.
   Both graceful and kill branches in both classes. The [CC-3.2] text, all three
   implementation steps, mapping claims, and `Spec:` backlinks land
   together in the atomic slice; intermediate steps are not separately
   promoted as an implemented contract.
4. **Traceability reconciliation.** Deviation log closed; gates rerun;
   ledger row updated.

Stop if: any step wants a Protocol change or a registry class — report.

## 6. Testing Plan

Probe setup to reproduce (from the 2026-08-31 verification; commit it as
the fixture for test 3(a)): construct a `Consumer` from a persistent
function-target TaskSpec against a `broker_env` database; write one inbox
message; run `process_once` until `work_item_completed` appears in
`weft.log.tasks`; read the newest `weft.state.tid_mappings` row and the
`_managed_pids` store. Today the row's `host_processes` lists only the
dead worker and `_managed_pids` still contains it.

Queues and worker execution stay real. Synthetic identity inputs cover
mismatched/unknown creation times and the replacement-handle observation
sequence. For the self-control regression, first use a signal-call recorder
to prove the proposed self-handle installation would target the current
process; after the fix, exercise idle termination on the real isolated task
path and verify terminal bookkeeping. Never signal the pytest process.

## 7. Verification and Gates

Per task: `./.venv/bin/python -m pytest tests/tasks/test_runtime_identity_custody.py tests/tasks/test_task_endpoints.py tests/cli/test_cli_run.py -q`.
Final (durable spine): full suite + mypy + ruff (commands as in the
ledger). Rollback: single revertable change; no persisted format changes.
Runtime observation: `weft status` on a persistent named task after one
item still shows the endpoint; LivenessMonitor does not retire idle
persistent tasks.

## 8. Independent Review Loop

Two Codex (cross-family) passes completed 2026-08-31 — see Review
Record. Task 1 is a third pass on this revision, before any change.
Stance: construct a case where the verified helper fails to terminate a
worker that *should* be terminated, and a case where the reap
discriminator (one-shot host `run_with_hooks` returned) is satisfied
while the worker is still alive.

## 9. Out of Scope

Endpoint custody rules (plan 4); manager-side child control; any Protocol
change; Docker/Microsandbox handle semantics beyond the authority gate.

## 10. Fresh-Eyes Review

Author pass 2026-08-31 (revised after Codex): dropped the callback design
(it was a Protocol change); made `None` mean no authority; moved the
release to the outcome boundary; added the [CC-3.2] delta and
reclassified to Class 5; replaced the scratchpad-probe reference with the
setup in §6; collapsed the three endpoint scenarios into one.

## Review Record (append-only)

**2026-08-31 — Codex (cross-model) pre-implementation review.** Verdict:
blocked as first written. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — the proposed gate still signals a recycled PID (`create_time=None` → PID-only check; precheck-then-signal race) | Applied: `None` = no authority; verify on the same `psutil.Process` used to signal |
| B2 — raw loop bypassed runner control authority (Microsandbox host PID, no `create_time`) | Applied: runner/external-supervisor PIDs never signaled directly |
| B3 — `on_worker_exited` would change the `TaskRunnerBackend.run_with_hooks` Protocol and four backends | Applied: release at the outcome boundary; no callback |
| B4 — no normative post-reap contract; two admissible results | Applied: Class 5; exact [CC-3.2] delta; test 3(a) asserts the exact handle |
| S — outcome registration could undo the fix; set-vs-dict contradiction; signal-test matrix; missing test files; probes not in repo; duplicate endpoint scenarios; [MA-1] as precedent; [LIVENESS.R4] retirement note | All applied |

**2026-08-31 — Codex (cross-model) second pre-implementation review, on
the revised plan.** Verdict: still blocked; the set→dict, stop-recomputing,
release-after-confirmed-reap, and authority-routing changes were each
judged to lose no valid protection — the blockers were about
implementability. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — `RunnerOutcome` carries no reap proof; a persistent agent session's outcome holds a live session handle | Applied: discriminator = PID registered via `on_worker_started` for a one-shot host `run_with_hooks` call that has returned (host runner joins before returning; pinned by test 2(f)); session PIDs excluded; no new outcome field |
| B2 — `HostRunnerPlugin.stop/kill` do check-then-reconstruct and run *before* the task loops via `_stop_registered_runtime_handle` | Applied: `terminate_verified_process_tree` is the single owner; host plugin, task loops, and `weft task kill` fallbacks all use it; host.py and its test in scope |
| B3 — delta contradicted [CC-3.2]'s `external-supervisor` = no Weft control | Applied: wording follows [CC-3.2]; external-supervisor gets nothing |
| B4 — normative rule broader than the host-only implementation | Applied: delta scoped to one-shot host workers; session-held runtimes named |
| B5 — test 3(a) required `kind == "host-pid"`, an invalid kind | Applied: `kind == "process"` |
| B6 — promotion before review | Applied: review is task 1; strategy B atomic slice follows |
| S — `weft task kill` fallbacks wrongly called "already gated"; matrix lacked known-matching/descendant and plugin-called-once cells; unknown-create-time cleanup decision; `inspect_host_process` cannot satisfy same-object; merge change needs a firing test; task 5 too broad; [LIVENESS.R4] vs [CC-3.2] ~:785 citation; ledger row; strategy A→B; same-instance rule in the delta | All applied |

**2026-09-07 — independent claim review at `bcea628e`; plan revision only.**
Initial verdict: BLOCKED. Class 5 revision of this implementation plan;
governing specs and code remain unchanged. Revised text requires independent
review before promotion; this record does not claim implementation or a
passing final review.

| Finding | Evidence and disposition |
|---------|--------------------------|
| I1 — installing the task-process mapping handle as `_runtime_handle` makes idle termination signal self before terminal bookkeeping | A safe call-recording probe installed the proposed task-process handle and invoked `_stop_registered_runtime_handle`; with actual signaling replaced by a recorder, it reported `Current process PID targeted by task runtime stop: True`. Accepted: task 3a now clears the active handle and republishes through the existing mapping fallback. Invariants, [CC-3.2] delta, and post-item idle termination tests prohibit self-control while preserving external mapping-based control. |
| I2 — the merge claim and red test confused an eagerly evaluated default with replacement of an existing identity | With `existing_processes=((123, 111.0),)` and the OS observation controlled to return `999.0`, `_merge_host_process_observations` returned `111.0`. Accepted: the claim and test now target a historical managed PID omitted from a replacement handle's observations; existing-key preservation is characterization coverage. |
| Adjacent contradictions in the changed slice | Corrected the untouched-fallback statement to match task 3b, and made strategy B one atomic promotion of all implementation steps and the full delta rather than promoting the full contract during the first of three commits. |


## Implementation Record (2026-09-07)

- Class 5, hardened: runtime control and normative [CC-3.2] change.
- Implementation baseline: `bcea628ee2ea7322988de9ef688113820611a032`;
  supplied draft plans and index were already dirty and are preserved.
- Independent same-family review: PASS on the corrected plan. The available
  in-session reviewer verified code and spec; cross-family review was not used.
- Accepted correction: the multiprocessing host runner's suppressed cleanup
  and bounded join do not prove exit. Before returning any normal outcome,
  check the owned process outside suppression; a surviving or unresolved
  worker raises and keeps Consumer custody. Command execution already waits
  or propagates its final wait failure. Add successful and failed-reap tests.
- Accepted correction: verified tree control reports whether signaling was
  attempted, preserving Windows control-convergence semantics. Death proof
  remains separate. Remove unreachable raw-PID fallback loops in task commands:
  their identity extractor requires the handle their preceding branch excludes.
- Preserve observation-only `pid_matches_create_time` behavior and Manager
  control. The strict new helper owns direct control in this slice.
- Verification: focused real-broker regressions, full `pytest -m ""`,
  `ruff check .`, repository-wide mypy and traceability gates before commit.
  Test execution and static checks can run independently; edits and commits
  remain sequential, and the root agent owns formatting of its touched files.
- Promotion baseline: [CC-3.2] at the baseline SHA above plus this worktree's
  atomic spec/code diff; no persisted schema changes.
- Red proof: `pytest tests/tasks/test_runtime_identity_custody.py -n 0 -q`
  failed on the dead-worker mapping and replacement-handle identity before
  implementation. Both pass after the fix.
- Additional accepted coupling: verified tree waits observe identity/status
  without `waitpid`; the runner's owned process object remains sole reaper.
  A local completed flag establishes normal return even inside a caller's
  exception handler. Regression tests cover both details.
- Independent completed-work review initially found F1 (ambient exception
  bypass), F2 (an added session-release path), F3 (incomplete idle matrix).
  F1 fixed with a local completion flag and regression; F2 removed to retain
  the original session-owned lifecycle; F3 expanded to both classes and
  both signals, both control paths, and one terminal envelope. Round 2: PASS.
- Focused command/custody suite: 83 passed. Signal and endpoint slice: 21
  passed. Ruff and all 192 mypy source files pass. Full suite (`./.venv/bin/python -m pytest -m '' -q`) passed on the settled
  implementation. Backend/provider opt-in skips remain the existing suite policy.
