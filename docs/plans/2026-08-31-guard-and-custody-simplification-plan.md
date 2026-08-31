# Guard and Custody Simplification Plan

Status: draft
Source specs: docs/specifications/07-System_Invariants.md [QUEUE.6], [OBS.1], [OBS.6], [OBS.6a], [LIVENESS.R3], [LIVENESS.R6]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3.1], [MF-5]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/01-Core_Components.md [CC-2.4.1], [CC-2.5]; docs/specifications/02-TaskSpec.md [TS-1.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/00-Quick_Reference.md (queue table)
Superseded by: none

Class: 4 — multi-subsystem custody and cleanup-lifecycle changes touching the
durable spine (task terminal paths, reserved-queue disposition, runtime
registries), with small clarifying spec deltas. Hardening per
`docs/agent-context/runbooks/hardening-plans.md` is mandatory.

Plan type: implementation with spec revision. Promotion strategies: A for the
custody paragraphs in [CC-2.4.1], [MA-1], and [MF-2]; D for the
00-Quick_Reference queue-table correction (clarification of already-shipped
behavior).

## 1. Goal

A 2026-08-31 five-subsystem complexity audit (five independent review agents,
findings inventoried in §3 below) found that the failure class fixed by the
liveness custody split — multiple deleters of one state queue, filters that
strand state, and guards against states the invariants already preclude — is
present elsewhere in the system. This plan removes that complexity by
establishing, for each affected area: the invariant being protected, the one
code path that owns it, and the proof that the surviving semantics are
correct. The direction is the one set by the liveness split: **one deleting
owner per shared state store, one fold per derivation, readers filter but
never delete, and no destructive "make extra sure" backstops layered on paths
that already have an owner.**

This plan deletes and consolidates; it adds no new features, no new
abstractions, and no new execution paths.

## 2. Source Documents

Specs (normative):

- docs/specifications/07-System_Invariants.md [QUEUE.6] (reserved policy),
  [OBS.1] (terminal write retry), [OBS.6]/[OBS.6a] (edge-triggered append-only
  mapping publication; read-before-write dedup forbidden), [LIVENESS.R3]
  (sole-deleter custody precedent), [LIVENESS.R6]
- docs/specifications/05-Message_Flow_and_State.md [MF-2] (reservation flow;
  "crash leaves the message in reserved state for explicit operator
  recovery"), [MF-3.1] (endpoint discovery), [MF-5] (state observation and
  monitor cleanup ownership)
- docs/specifications/03-Manager_Architecture.md [MA-1] (manager behaviour and
  `weft.state.services` registry; implementation mapping [MA-1.4]), [MA-3]
- docs/specifications/01-Core_Components.md [CC-2.4.1] (endpoint registry is
  discovery-only), [CC-2.5] (execution flow)
- docs/specifications/02-TaskSpec.md [TS-1.3] (runner selection and plugin
  contract; `validate_runner_capabilities` mapping)
- docs/specifications/14-Python_API_Surfaces.md [PY-2] (typed commands surface
  — the sole supported commands generation)
- docs/specifications/00-Quick_Reference.md (queue-name table)

Plans (historical context, non-normative):

- [2026-08-29-liveness-reaper-and-custody-split-plan.md](./2026-08-29-liveness-reaper-and-custody-split-plan.md)
  — the custody-split precedent this plan generalizes. **Sequencing
  dependency:** do not start WS2, WS3, or WS5 of this plan until that plan's
  implementation has landed (verify with `git log` that the liveness custody
  commit exists; its two known review blockers must be resolved in that
  plan's scope, not here).
- [2026-08-08-registry-selection-pruning-authority-refactor-plan.md](./2026-08-08-registry-selection-pruning-authority-refactor-plan.md)
  — prior registry-authority work backlinked from [CC-2.4.1].

Guidance: `CLAUDE.md` §1.1 and §4, `docs/agent-context/engineering-principles.md`,
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`.

**Line-number caveat:** every file:line reference in §3 was verified on
2026-08-31 against the post-liveness-split worktree. Locate sites by symbol
name, not line number. If a cited site no longer exists, treat it as already
fixed, record that in the Deviation Log, and move on — do not reinvent it.

## 3. Workstreams

Each workstream answers five questions: (a) what invariants are protected,
(b) which spec sections govern, (c) what is the one path that survives,
(d) why that path is the right one, (e) how we prove the changed semantics
are correct. Workstreams are independently landable slices. Priority order:
WS1 and WS2/WS3 first (live data-integrity risk), then WS4–WS6, then the
mechanical retirements WS7–WS9.

---

### WS1 — Reserved-queue disposition: one owner, no destructive backstops

**Findings.** Every stop/kill/error path in the task classes runs a
copy-pasted triad: `_apply_reserved_policy(policy)` →
`_ensure_reserved_empty()` (destructive `read_many` drain) →
`_cleanup_reserved_if_needed()` (second drain, gated on `cleanup_on_exit`)
(`weft/core/tasks/base.py::_ensure_reserved_empty`, `_apply_reserved_policy`,
`_cleanup_reserved_if_needed`; call sites in `consumer.py`, `heartbeat.py`,
`interactive.py`, `pipeline.py` — 10+ sites). The Manager has the same shape:
`weft/core/manager.py::_ensure_reserved_queue_empty` drains after applying
`reserved_policy_on_error`/`on_stop`, including after REQUEUE. Under REQUEUE,
`_move_reserved_to_inbox` breaks on the first broker error and the follow-up
drain silently deletes the surviving rows — converting "requeue for
re-execution" into permanent message loss with a debug log.

**(a) Invariants protected.**
- [QUEUE.6] / CLAUDE.md §3: "Reserved queue policy must be honored
  (keep/requeue/clear)" — a NEVER-break invariant. A backstop that deletes
  rows REQUEUE said to preserve violates it.
- [MF-2]: "crash leaves the message in reserved state for explicit operator
  recovery" — leftover reserved rows are owned by operator/monitor recovery,
  not by task-side drains.

**(b) Spec sections.** 05 [MF-2] (lines ~105–129), 07 [QUEUE.6], 05 [MF-5]
(monitor-owned reserved cleanup with age gates and destruction protection).

**(c) The one path.** `_apply_reserved_policy` (task side) and the Manager's
policy application (`move_one` with exact `message_timestamp` for REQUEUE,
drain for CLEAR, nothing for KEEP) are the sole reserved-queue mutation at
task/manager level. The backstop drains — `_ensure_reserved_empty`,
`_ensure_reserved_queue_empty`, and the `_cleanup_reserved_if_needed` pass
where it targets the reserved queue after policy has already run — are
deleted. Leftover rows after a failed policy application are left in place
and surfaced via the existing warning log; recovery belongs to the monitor's
store-backed reserved cleanup ([MF-5]) and the operator (`weft system prune
--family task-local … --archive`).

**(d) Why this is the right path.** Policy application is the only code that
knows what the policy *was*; a generic "empty the queue" backstop cannot be
policy-correct by construction. The states the backstops defend are
precluded upstream: the reserved queue is single-writer per task, Manager
child launch is single-flight (`_process_queue_message` blocks spawn sources
during a launch), and CLEAR has already drained by the time the backstop
runs. When the impossible state *does* occur (a broker error mid-policy), the
spec explicitly assigns it to operator recovery — the backstop destroys
exactly the evidence that recovery needs.

**(e) Semantic correctness proof.**
- Red first: a test that injects a broker failure mid-REQUEUE (real broker
  via `broker_env`, failure injected at the queue-object seam, not by
  mocking queue semantics) and asserts the surviving rows are STILL PRESENT
  in `T{tid}.reserved` afterwards, with a warning logged. Seams per path
  (review N1): the task-side bulk fallback is `move_many` batches in
  `base.py::_move_reserved_to_inbox` (break-on-first-error), so fail the
  second batch; the manager path is exact `move_one` then fallback, so fail
  the fallback. This test is red against current code (rows are drained)
  and green after.
- Success-ack residue (review S3): three triad sites follow a SUCCESS-path
  exact ack, not a policy application — `consumer.py` post-ack,
  `heartbeat.py::_delete_reserved_message` (in a `finally`), and the
  pipeline post-ack. There the drain is the fallback ack when the exact
  delete fails. Deleting it is safe: a leftover completed reserved row is
  never re-executed (reservation is an inbox→reserved move; reserved rows
  are not re-read for execution) and monitor reserved cleanup harvests it.
  The [MF-2] delta (§4c) covers this residue explicitly. Enumerate these
  three sites in the task's file list; do not treat them as policy sites.
- Spec mapping: `docs/specifications/02-TaskSpec.md` maps
  `spec.cleanup_on_exit` to `BaseTask._cleanup_reserved_if_needed()` by
  name (~:304). WS1 deletes that method; update the mapping to the
  consolidated disposition method in the same change. Semantics are
  preserved — every current call site is gated `policy is not KEEP` or
  post-success (queue already empty), so its only live effect today is
  draining failed-policy/failed-ack residue, which is exactly what WS1
  removes on purpose.
- Characterization: existing reserved-policy tests
  (`tests/tasks/test_consumer.py`, `tests/core/monitor/policies/`) pin
  KEEP/REQUEUE/CLEAR observable outcomes; they must stay green untouched.
- One consolidation: the surviving policy application is extracted to ONE
  shared method on `BaseTask` called from all sites (this is fold-in, not a
  new abstraction — the triad's 10+ call sites collapse to one call).

Files to modify: `weft/core/tasks/base.py`, `weft/core/tasks/consumer.py`,
`weft/core/tasks/heartbeat.py`, `weft/core/tasks/interactive.py`,
`weft/core/tasks/pipeline.py`, `weft/core/manager.py`, plus their test files.
Read first: 05 [MF-2], 07 [QUEUE.6], `tests/helpers/weft_harness.py`.

---

### WS2 — `weft.state.services` custody: readers filter, one gated deleter

**Findings.** Four deleters across three process types: (1) the Manager's
own-row lifecycle (`_register_manager` heartbeat delete, supersede delete,
`_unregister_manager`, `_prune_older_self_registry_entries`) plus peer-row
deletes (`_prune_expired_manager_registry_entries`,
`_active_dispatch_manager_records` stale-proof delete,
`_prune_managed_service_registry_history`); (2) CLI/client **read paths** —
`manager_runtime.py::_snapshot_registry` deletes rows judged stale on every
`weft status` / `weft manager list` / `ensure_manager`, after blocking PING
probes (0.5s per candidate); `_mark_manager_stopped` deletes all rows for a
TID and synthesizes a terminal record; (3) the pruning engine
(`pruning/runtime.py::_manager_candidates`, `_service_candidates`) with
min-age and keep-recent gates the others lack. Three different staleness
predicates govern one delete. Related cost: `_register_manager`'s post-write
supersede re-check performs up to four full registry replays per heartbeat.
(2026-08-31 independent review, finding B1: the re-check itself is
LOAD-BEARING — see (c) below — only its replay count is excess.)

**(a) Invariants protected.**
- Registry self-healing by append: the Manager re-registers on every
  heartbeat, so a stale row is never load-bearing for longer than one
  heartbeat interval — the same argument that made tid-mapping reaping safe
  ([OBS.6]/[LIVENESS.R6] analogue).
- [MANAGER.14]: stale or ambiguous registry proof must degrade status and
  selection rather than halt dispatch. It is silent on deletion — the
  no-reader-deletion rule is ADDED by this plan's [MA-1] delta; do not claim
  [MANAGER.14] already states it.
- Reads must be reads: `weft status` must not be a write path.

**(b) Spec sections.** 03 [MA-1] (registry text and schema, implementation
mapping [MA-1.4]), 03 [MA-3] (bootstrap waits on the registry), 07
[MANAGER.13]/[MANAGER.14], [LIVENESS.R3] as the custody precedent.

**(c) The one path.**
- **Own-row lifecycle stays with the Manager**: registration, heartbeat
  supersession of its own prior rows, unregistration, and supersede handling
  — a process deleting rows *it wrote* is owner custody, same as a task
  deleting its own endpoint row.
- **All cross-process staleness deletion moves to the pruning engine**
  (`weft/core/pruning/runtime.py`), which already has the predicate
  (`manager_registry_record_is_stale`), min-age gates, and keep-recent
  protection, and already runs from both the monitor maintenance pass and
  `weft system prune`.
- **Readers never delete.** `_snapshot_registry` and
  `_manager_registry_disposition` classify records (keep/omit) and filter
  them from results; the "prune" disposition arm and its delete calls are
  removed. `_mark_manager_stopped` writes the terminal record (append) and
  stops deleting prior rows — the pruner collects them.
- The Manager's peer-row deletes (`_active_dispatch_manager_records`,
  `_prune_expired_manager_registry_entries` for non-self rows) are removed;
  leadership evaluation filters stale peers using the existing disposition
  predicate without deleting them.
- **KEEP the `_register_manager` post-write supersede re-check** (2026-08-31
  review, B1). It is load-bearing for `weft manager start --replace`:
  `replace_active_manager` still APPENDS superseded rows under this plan,
  its wait loop exits the moment `select_active_manager` returns None, and
  an in-flight incumbent heartbeat that lands after that write publishes a
  newer active row that permanently masks the superseded row in the
  latest-per-TID snapshot — `_self_superseded_manager_record_visible`
  never fires, and [MA-3] proactive supersession then runs backwards
  (incumbent supersedes the replacement). The re-check is also own-row
  custody (it deletes only the row the manager just wrote), fully permitted
  by the [MA-1] delta. Scope here is limited to REDUCING its replay cost:
  collapse the up-to-four full registry replays per heartbeat to a single
  post-write superseded re-scan with unchanged detection semantics.

**(d) Why this is the right path.** It is the liveness precedent applied
verbatim: the pruner is the designated `weft.state.*` maintainer with the
safety gates (min-age closes the same startup-window race that bit
endpoints); one staleness predicate gets one enforcement point, ending
predicate drift.

**Accepted behavior changes (state them, do not hide them — review S4):**
(i) for a CLI-only user with no live manager, stale rows persist in
`weft.state.services` indefinitely until a TaskMonitor maintenance pass or
an explicit `weft system prune` runs — acceptable because readers filter
them and the queue is runtime-only; (ii) an ambiguous crashed-manager row
incurs the bounded keyed-PING probe on each read until a pruner collects it
(today's reader-side delete pays that probe once). Both go into the
characterization matrix as pinned expected behavior.

**(e) Semantic correctness proof.**
- Characterization first: pin current observable behavior of `weft status`,
  `weft manager list`, `ensure_manager`, `start_manager`, and leadership
  selection with a registry containing (i) a live manager, (ii) a stale row
  for a dead PID, (iii) a superseded row — results (which records appear,
  which manager is selected) must be IDENTICAL before and after; only the
  queue's row count differs (stale rows remain until the pruner runs).
- New regression: after reader paths run against a stale row, assert the row
  is still present in `weft.state.services` (readers no longer delete), and
  that a subsequent `run_runtime_prune_for_context` removes it.
- Supersede convergence: existing `replace_active_manager` tests
  (`tests/core/test_manager.py`) must stay green; add one regression that
  constructs the B1 interleaving (superseded row written by
  `replace_active_manager` between the incumbent's pre-write check and its
  heartbeat write) and asserts the incumbent reaches superseded shutdown —
  pinning the retained re-check as load-bearing so it is not deleted later.
- Custody enforcement: extend `tests/architecture/` with a delete-custody
  test for `weft.state.services` modeled on
  `tests/architecture/test_liveness_boundaries.py`, with the improved
  matcher from WS-note below.

Files to modify: `weft/core/manager.py`, `weft/core/manager_runtime.py`,
`weft/core/pruning/runtime.py` (only if a predicate needs importing — the
engine itself should not change behavior), tests in `tests/core/`.
Read first: 03 [MA-1]/[MA-3], 07 [MANAGER.12]–[MANAGER.14],
`weft/core/control_probe.py::pong_proves_dispatch_eligible` (do not add a
second narrowing rule — [MA-1.4]).

**Stop-and-re-evaluate gate:** if removing a peer-row delete breaks a
leadership test in a way filtering cannot fix, stop — that means the delete
was load-bearing for selection, which contradicts [MANAGER.14], and the
conflict must be reported, not papered over.

---

### WS3 — `weft.state.endpoints` custody: readers filter, appends stay append-only

**Findings.** Three deleters: (1) every resolution
(`weft/core/endpoints.py::list_resolved_endpoints`) deletes non-live-owner
rows inline with **no age gate** — it can delete a row in the startup window
before the owner's tid-mapping row is published (`_record_owner_is_live`
returns False when the mapping is absent); (2) the pruning engine
(`pruning/runtime.py::_endpoint_candidates`) does the same delete behind
min-age and keep-recent gates; (3) `base.py::register_endpoint_name` performs
scan-before-write, write, scan-after-write, delete-prior — the exact
read-before-write dedup pattern [OBS.6a] bans for tid_mappings, implemented
next to the `_register_tid_mapping` docstring that states the ban.

**(a) Invariants protected.**
- [CC-2.4.1]: the registry is discovery-only; records are "runtime-only
  hints". Hints are filtered, not destroyed, by consumers.
- [OBS.6a]'s design rule generalized: writer-side read-before-write
  deduplication on shared observational queues is forbidden; latest-wins
  reader reduction plus a gated reaper replaces it.
- Startup race safety: a row must not be deletable before its owner has had
  time to publish liveness evidence — exactly what the pruner's min-age gate
  provides and the reader-side delete lacks.

**(b) Spec sections.** 01 [CC-2.4.1], 05 [MF-3.1], 07 [OBS.6a] (pattern),
[LIVENESS.R3] (custody precedent).

**(c) The one path.**
- **Owner custody:** the task appends its registration and deletes its own
  rows in `unregister_endpoint_name()` at exit. This stays.
- **`register_endpoint_name` becomes a plain append** — no pre-scan, no
  post-scan, no delete-prior. Reader reduction (latest row per (name, tid))
  makes the superseded row inert; the pruner collects it. For
  `unregister_endpoint_name`'s exit-time own-row delete (which stays),
  capture the write's returned message id at registration time (the
  `_register_manager` pattern) or rely on the existing scan fallback —
  review N3(a); pick the capture form, it removes the last scan.
- **Accepted residual risk (review N3(b), one sentence, record it):** an
  old endpoint row of a live task whose newest mapping row was reaped
  under the [LIVENESS.R4] attempted-unknown timeout is prunable, and
  endpoints — unlike mappings — have no republish self-heal; the exposure
  is strictly smaller than today's ungated reader-side delete of the same
  row, and re-registration on task restart recreates it.
- **Readers filter, never delete:** `list_resolved_endpoints` classifies
  dead-owner rows out of its results and stops calling delete.
- **The pruning engine is the sole deleter of stale rows.**

**(d) Why this is the right path.** The reader must classify dead owners
anyway to produce correct results — the delete adds nothing to correctness,
only a second (ungated) deletion authority. The pruner already exists, has
the same predicate (`endpoint_record_owner_is_live`), and its min-age gate is
the documented answer to the startup race. Append-only registration is the
pattern the codebase already declares canonical for observational rows.

**(e) Semantic correctness proof.**
- Characterization: resolution results (`resolve_endpoint`,
  `list_resolved_endpoints`) for live, dead-owner, and duplicate-claimant
  registries are pinned before the change and must be identical after —
  including the [CC-2.4.1] lowest-live-TID canonical rule and
  `live_candidates` counts.
- Red-first regression for the startup race: register an endpoint whose
  owner has NOT yet published a tid-mapping row, run resolution, and assert
  the row still exists in `weft.state.endpoints` (red today: reader deletes
  it) while being absent from live results.
- Re-registration semantics: register, re-register (same task, new queue
  set), resolve — newest row wins; the superseded row remains until
  `run_runtime_prune_for_context` removes it (assert both).
- Custody enforcement: add `weft.state.endpoints` to the architecture
  delete-custody test (WS-note below). Allowed deleters: the owning task's
  `unregister_endpoint_name` and the pruning engine.

Files to modify: `weft/core/endpoints.py`, `weft/core/tasks/base.py`, tests
in `tests/core/` and `tests/tasks/`. Read first: 01 [CC-2.4.1], the
`_register_tid_mapping` docstring in `weft/core/tasks/base.py` (the pattern
to copy), `weft/core/pruning/runtime.py::_endpoint_candidates`.

**Also in this workstream (small):** delete the two dead guards in canonical
selection — `canonical_record` reduces exactly to `ordered[0]` after the
ascending sort; the `canonical_tid is None` branch and the `next(...,
ordered[0])` fallback guard states the TID invariant precludes.

---

### WS4 — One terminal path, one terminal vocabulary

**Findings.** (1) `InteractiveTaskMixin` reimplements STOP/KILL inline and
emits its own terminal ctrl_out envelope via bare `ctrl_out.write` — skipping
`_write_state_queue_message(terminal=True)` and therefore the [OBS.1]
bounded terminal-write retry; two envelope schemas coexist on ctrl_out
(`weft/core/tasks/interactive.py::_interactive_handle_control`,
`_interactive_terminal_envelope`). (2) The terminal-status set is spelled as
a literal in four code sites (`base.py` ×2, `interactive.py`,
`taskspec/model.py`) while TWO identical frozensets exist in `_constants.py`
(`TERMINAL_TASK_LIFECYCLE_STATUS_VALUES` at ~:116,
`TERMINAL_TASK_STATUSES` at ~:1754). (3)
`store.retire_completed_collation_families` is called from both the store
cycle and the reserved slice while a comment declares the reserved slice
"the sole collation-retirement owner"
(`weft/core/monitor/task_monitor.py`).

**(a) Invariants protected.** Forward-only state transitions (one place
decides what "terminal" means); the bounded terminal-write retry (an
implementation elaboration documented in `_write_state_queue_message`
under [OBS.1] — review N2: characterization behavior, not spec-mandated
text) applies to every terminal signal, not just non-interactive ones;
single-owner custody for destructive monitor-store mutations.

**(b) Spec sections.** 07 [OBS.1], 00-Quick_Reference (states table), 05
[MF-5] (monitor cleanup ownership).

**(c) The one path.**
- Interactive STOP/KILL route through `_handle_stop_request` /
  `_handle_kill_request`, with interactive-specific work (session teardown)
  as a hook invoked from the canonical path — not a parallel sequence.
  **Envelope ordering and dedup (review S1, binding):** exactly ONE
  terminal envelope is emitted, by `_send_terminal_envelope`, from the
  interactive finalize step AFTER output drain and after the final stream
  markers (today's observable ctrl_out ordering: stderr chunks and final
  markers precede the terminal envelope — preserve it). The base path's
  inline emission at mark-time is suppressed or deferred for interactive
  mode; emitting base-first-then-finalize (two envelopes) or
  envelope-before-markers (reordering) are both failures. The `event`
  field is dropped: no in-repo ctrl_out reader keys on it
  (`weft/commands/tasks.py` keys only on `type=="terminal"` + `status`).
- `TERMINAL_TASK_STATUSES` in `_constants.py` is the single terminal
  vocabulary. `TERMINAL_TASK_LIFECYCLE_STATUS_VALUES` is redefined as an
  alias derived from it (`= TERMINAL_TASK_STATUSES`) or deleted with its one
  consumer repointed; the four inline literals are replaced with the
  constant.
- `retire_completed_collation_families` is called from exactly one lane —
  the reserved slice, per the existing ownership comment. The store-cycle
  call is deleted. **Gate fix required first (review S2):** the reserved-
  slice call currently sits behind `if not errors`, so with the store-cycle
  call gone, one persistently erroring reserved queue would permanently
  halt ALL family retirement. Move the surviving call out of the
  `if not errors` gate (retirement's own predicate is conservative and
  idempotent — it does not depend on this cycle's cleanup succeeding), and
  verify retirement still occurs in `report_only` mode, where the
  store-cycle call covers it today. Add a test for both.

**(d) Why this is the right path.** The base implementations carry the
spec-mandated retry and the schema consumers already parse; the mixin fork
has already drifted (missing retry, extra field) — proof that two paths
diverge. The reserved slice is the lane the code itself documents as owner;
honoring the written custody claim beats honoring the accident.

**(e) Semantic correctness proof.**
- Interactive: existing interactive STOP/KILL tests must stay green; add one
  asserting the terminal ctrl_out envelope from an interactive task is
  schema-identical to a consumer task's (same required keys), and one
  injecting a transient broker write failure at terminal time and asserting
  the retry fires (red today for interactive, green for consumer — the
  differential is the finding).
- Vocabulary: `grep -rn` gate — zero inline occurrences of the literal
  five-status set outside `_constants.py`; `tests/system/test_constants.py`
  updated to pin the single source.
- Retirement: existing retirement tests stay green with the store-cycle call
  removed; retirement counters attribute to one lane.

Files to modify: `weft/core/tasks/interactive.py`, `weft/core/tasks/base.py`,
`weft/core/taskspec/model.py`, `weft/_constants.py`,
`weft/core/monitor/task_monitor.py`, matching tests.

---

### WS5 — One fold per derivation

**Findings.** (1) The "latest tid-mapping row" fold is hand-rolled at least
eight times with inconsistent conventions: `weft/commands/system.py`
(`_read_tid_mappings`, `_latest_tid_mapping_entries`),
`weft/commands/tasks.py` (`_read_tid_mapping_entries`, `mapping_for_tid`
last-wins, `resolve_full_tid` FIRST-wins), `weft/core/endpoints.py`
(`_latest_tid_mapping_entries` — the strict canonical one, consumed by
manager admission and the pruner), `weft/core/manager.py`
(`_latest_tid_runtime_handle(s)`), `weft/core/manager_runtime.py`
(`_lookup_manager_pid`). The commands copies have already micro-drifted
(missing empty-string check, no strict decode, no int cast). (2)
`manager_runtime.py` privately re-inlines the helpers-owned handle-liveness
functions (`_live_host_processes_from_handle`,
`_manager_handle_has_live_host_process` vs
`weft/helpers/__init__.py::live_host_processes_from_handle`,
`handle_has_live_host_process`); the private copy is semantically identical.

**(a) Invariants protected.** [OBS.6]: each mapping row is a complete
observability record and the NEWEST row per TID is the current one — there
is exactly one correct answer to "which row is current", and every consumer
must compute it the same way. Since republish-on-activity is the liveness
self-healing mechanism ([LIVENESS.R6]), superseded rows are routinely
present; first-wins folds are not a stylistic variant, they are wrong.

**(b) Spec sections.** 07 [OBS.6], [OBS.6a], [LIVENESS.R6]; 03 [MA-1.8]
(admission already names
`endpoints.py::latest_tid_mapping_entries_for_endpoint_resolution` as the
strict reduction).

**(c) The one path.**
`weft/core/endpoints.py::latest_tid_mapping_entries_for_endpoint_resolution`
(and its underlying `_latest_tid_mapping_entries`) is the single fold —
it is already the one [MA-1.8] cites. All commands-layer and
manager_runtime folds are repointed to it (commands → core imports are
legal per the layering rules); short-TID resolution (`resolve_full_tid`)
derives from the canonical latest-per-full-TID map and then matches the
short form — newest-wins, with ambiguous short matches surfaced as errors
exactly as today. For handle liveness, the `weft/helpers/__init__.py`
functions are the single owner; the manager_runtime private copies are
deleted.

**(d) Why this is the right path.** The endpoints fold is the only one with
strict decode, empty-string rejection, and int-cast hygiene, and it is
already load-bearing for admission control and pruning — the highest-stakes
consumers. Consolidating downward (into commands) would invert the layering.
The helpers' handle-liveness functions are already consumed by
`endpoints.py`; the manager copy re-inlines a branch the helper already
contains.

**(e) Semantic correctness proof.**
- Differential fixture: one test module builds adversarial mapping-queue
  contents — republished rows, terminal-after-active rows, short-TID
  collision, malformed row, empty-string TID — and asserts the canonical
  fold's output. Before deleting each duplicate fold, run its call-site
  behavior against the same fixture; where the old fold disagrees (the
  known first-wins/last-wins split), the NEW behavior is newest-wins and the
  change is recorded here as intended. **Decided (review S5 — there is no
  existing ambiguity error; do not invent one):** `resolve_full_tid` on a
  short-TID collision resolves to the newest matching row, full stop. A
  new collision-error surface would be a CLI contract change and is out of
  scope; if an implementer believes one is needed, STOP and report.
- All existing `weft status` / `weft task list` snapshot tests stay green.
- Grep gate: no remaining local definitions matching
  `latest_tid_mapping|_read_tid_mapping` outside `weft/core/endpoints.py`.

Files to modify: `weft/commands/system.py`, `weft/commands/tasks.py`,
`weft/core/manager.py` (repoint only — do not change admission behavior),
`weft/core/manager_runtime.py`, matching tests. Read first: 07 [OBS.6],
`weft/liveness/policy.py::reduce_mapping_history` (the reaper's own fold —
verify the canonical read fold and the reaper's policy fold agree on
malformed-row and newest-row handling before repointing anything; if they
disagree, STOP and report, because that disagreement is itself a WS5
finding).

---

### WS6 — Guard-semantics repair (wrong, contradictory, or constant-computing guards)

Small, independent fixes; each deletes a guard whose defended state is
precluded or whose semantics are wrong.

1. **Negative-sentinel branches with contradictory meanings.**
   `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS` and
   `MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS` are positive `Final`
   constants, yet five sites branch on them being negative and disagree on
   what negative means (everything-expired vs never-stale vs
   block-startup-forever): `manager.py::_registry_entry_is_expired`,
   `_manager_record_liveness`;
   `manager_runtime.py::_record_is_recent_enough_for_uncertain_selection`
   and two more. Delete the dead branches. Invariant: positive-Final
   constants ([§4.4 house style]). Proof: mypy/tests green; no behavior can
   change because the branches are unreachable.
2. **`_closed_activity_waiter_ids` id()-keyed skip-set**
   (`weft/core/tasks/multiqueue_watcher.py`): replace the append-only
   `id()` registry with a per-waiter `closed` flag (or idempotent
   `close()`), eliminating the excluded-forever set and the id-recycling
   false-skip leak. Proof: existing displaced-waiter test stays green; add
   one that replaces a waiter twice and asserts both old waiters are closed.
3. **Queue-discovery cadence computes a constant**
   (`task_monitor.py`: `_next_runtime_cleanup_queue_discovery_due_monotonic`
   reset to 0.0 each cycle → `queue_discovery_due` always true). Decide the
   semantics, then make the code say it: the simple truth is "discovery runs
   every destructive cycle", so DELETE the deadline fields, the
   `runtime_cleanup_queue_discovery_due` policy function, the not-due early
   return, and the diagnostics plumbing — do not "fix" the cadence into
   existence (that would be adding behavior nothing needs). Fix the
   `_maybe_run_maintenance_pass` docstring that cites this as a model.
   Proof: cleanup-slice tests stay green; grep gate on the deleted names.
4. **SimpleBroker return-shape guards that strand state**
   (`heartbeat.py`, `consumer.py` isinstance/len checks after
   `peek_one`/`move_one`/`peek_many`): delete them. The library's typed
   contract is the boundary ([§1.1: respect the SimpleBroker layer]); a real
   violation should raise loudly, not silently strand a moved reserved row.
   Proof: tests green; no new behavior — the guards were unreachable.
5. **Probe ownership guards that fail closed under delegation**
   (macOS/microsandbox liveness probes return `"unknown"` forever when
   `handle.runner != <plugin name>`): the registry dispatches by
   `liveness_provider_key` and core already delegates foreign handles
   (manager sets `liveness_provider="docker"` on supervisor handles). Align
   all three plugins on the docker-style acceptance shape (probe what the
   registry routed to you; return `"unknown"` only for evidence you cannot
   read). Spec: 07 [LIVENESS.R5] evidence-authority. Proof: extension probe
   tests updated; add a delegation test per plugin (handle with foreign
   `runner` but matching `liveness_provider`) asserting a real probe attempt.

---

### WS7 — Dead-generation retirement (mechanical deletions)

**Findings and the one path per item.** The invariant protected throughout
is [PY-2]: the typed `cmd_*` commands surface is the single supported
generation; and the repo rule "one path with one owner". No behavior
changes — every item below is a zero-production-caller deletion verified by
per-symbol grep on 2026-08-31; the implementer re-verifies each grep before
deleting.

1. **Legacy tuple-returning commands generation** (~600+ lines). Land this
   as one sub-slice PER COMMAND MODULE (review N5: a single ~600-line
   deletion is too large to review safely), each following
   grep-then-delete-then-repoint:
   `weft/commands/system.py::_legacy_cmd_status`, `_watch_task_events` and
   renderers reachable only from it;
   `weft/commands/result.py::_legacy_cmd_result`, `_collect_all_results`,
   `_result_request_error`, `_claimed_result_response`,
   `_single_result_response`; `weft/commands/prune.py::cmd_prune`,
   `render_runtime_prune_human`, `render_retention_prune_human`,
   `_runtime_prune_command`, `_retention_prune_command`;
   `weft/commands/manager.py::start_command`, `stop_command`;
   `weft/commands/serve.py::serve_command`; `weft/commands/load.py::cmd_load`.
   Tests pinning them (`tests/commands/test_status.py` aliasing
   `_legacy_cmd_status as cmd_status`, `tests/commands/test_result.py`, etc.)
   are repointed at the typed generation — the assertions about observable
   behavior are kept; only the entry point changes. If a legacy test asserts
   behavior the typed path does NOT have, STOP: that is a gap in the typed
   surface, report it before deleting.
2. **client/CLI forked bodies**: repoint `tidy_system`, `dump_system`,
   `cmd_manager_serve`, `cmd_manager_stop` at the typed `cmd_system_*` /
   shared bodies the way `load_system` already does (the in-repo exemplar).
   This FIXES a live divergence: client errors become typed
   (`CommandExecutionError`) instead of bare `RuntimeError` — record this
   intended behavior change in the client-facing changelog/docstrings.
   Similarly, `weft/client/_namespaces.py::stop_many/kill_many` stop calling
   the private `tasks._task_control_result` and route through
   `cmd_task_stop`/`cmd_task_kill`; the empty-selection contract unifies on
   the CLI's explicit error. Record both as intended contract fixes.
3. **Dead validate module**: delete
   `weft/cli/validate_taskspec.py::cmd_validate_taskspec`,
   `_resolve_taskspec_source` — but FIRST compare its resolution behavior
   (stored-name + bundle-dir handling) with the live
   `app.py::spec_validate` inline resolution; if the dead path's stored-name
   resolution is behavior users should have, that is an owner decision —
   flag it, do not silently adopt or drop it. The render helpers the live
   path imports move next to their caller.
4. **Dead helpers suite**: delete the zero-caller logging facade
   (`send_log`, `debug_print`, `log_*`, `is_logging_enabled`,
   `is_debug_enabled`), `format_tid`/`parse_tid`, `resolve_cli_command` +
   `CommandNotFoundError`, `reload_config` from
   `weft/helpers/__init__.py`, plus their ~40 tests. Keep the
   `WEFT_DEBUG`/`WEFT_LOGGING_ENABLED` env keys (still forwarded to
   SimpleBroker overrides in `_constants.py`).
5. **Monitor dead machinery**: delete
   `task_monitor.py::_coalesce_and_delete_dead_task_log_rows_for_tids`,
   `_delete_monitor_store_task_log_rows_for_tids`, the store method + SQL
   builder `list_deletable_task_log_messages_for_tids`, and the always-zero
   result fields `dead_tid_log_refs_selected`/`dead_tid_log_rows_deleted`
   (update the test asserting they are 0). Delete the test-only
   `reserved_cleanup_enabled` arm: `policies/reserved.py` entirely, the
   config field, and `policies/task_log.py`'s reserved hook — the
   store-backed `_run_reserved_cleanup_slice` is the one owner ([MF-5]).
   Delete `store.list_unemitted_terminal_tasks` (+ SQL builder, no callers),
   the hardcoded-zero `dead_tid_control_rows_estimated_deleted` field, and
   collapse the byte-identical SQL builder pair
   (`select_summary_ready_open_tasks` /
   `select_stale_service_owner_candidate_tasks`) to one builder.
6. **Dead config and spec drift**: delete `yield_strategy`
   (multiqueue_watcher param, attribute, and its
   `_WORKER_SNAPSHOT_EXPECTED_FIELDS` ledger entry); delete
   `DEFAULT_CPU_PERCENT`/`DEFAULT_MAX_FDS`/`DEFAULT_MAX_CONNECTIONS`,
   `STATUS_RUNNING`/`STATUS_FAILED`/`STATUS_CANCELLED`,
   `RUNNER_DIAGNOSTICS_FIELD` — OR repoint real consumers at
   `RUNNER_DIAGNOSTICS_FIELD` so the constant becomes true SSOT (preferred
   for that one, since four modules hardcode the string); fix
   `DEFAULT_FUNCTION_TARGET` to a resolvable target or an explicitly
   non-resolvable sentinel with a docstring saying it is never dereferenced
   (verify first with grep that nothing resolves it). For
   `WEFT_MANAGER_CTRL_IN_QUEUE`/`WEFT_MANAGER_CTRL_OUT_QUEUE`: verify no
   compat consumer exists (grep weft/, extensions/, integrations/, and dump
   fixtures); if clean, delete the constants, update
   `tests/core/test_manager.py` registry-record fixtures, and land the
   00-Quick_Reference + CLAUDE.md §2 corrections (Proposed Spec Delta §4c).
   If a compat consumer IS found, keep the constants and fix only the spec
   wording to say "legacy record field, no live queue". Update
   `tests/system/test_constants.py` for every removal — that test pins the
   constants inventory and is the per-task done signal.

**(e) Semantic correctness proof for all of WS7:** per-symbol grep returning
zero production references BEFORE deletion (recorded in the task commit
message), full test suite green AFTER with legacy tests repointed rather
than deleted (behavioral assertions preserved), and `weft/commands/__init__.py`
exports unchanged except for documented removals.

---

### WS8 — Boundary re-validation removal (validate once, then trust the model)

**Findings.** (1) `TaskSpec._validate_strict_requirements`
(`weft/core/taskspec/model.py`, ~70 lines run on every task construction)
re-checks conditions Pydantic makes unrepresentable (min_length fields,
required fields under `extra="forbid"`, non-Optional default_factory fields,
`hasattr` on declared fields); ~3 lines are live (the resolved-spec io
presence check for `auto_expand=False`). Also `validate_required_elements`
(documented no-op) and `metadata is None` guards in
`update_metadata`/`set_metadata`. (2) Docker and macOS runner `__init__`s
re-run checks `validate_taskspec` just ran (core calls validate→create
unconditionally in `tasks/runner.py` and `runner_validation.py`), and
Docker's copy has forked its error message. (3) All three plugins re-check
capability constraints (`interactive`/`persistent`/type) that
`validate_runner_capabilities` already enforces before `validate_taskspec`
is ever called. (4) `runner_validation.py` preflight calls every plugin
validator twice (preflight=False then preflight=True; all plugins implement
preflight as a superset). (5) The macOS plugin re-implements
`weft/liveness/host.py::inspect_host_process` decision-for-decision
(`_host_process_liveness`). (6) The `_liveness_probe_registered`
module-global in all three plugins guards an already-idempotent
registration.

**(a) Invariants protected.** `spec`/`io` immutability after TaskSpec
creation (nothing can un-validate between boundary and use); "validate at
the boundary, then stay strict" (engineering-principles §3); [LIVENESS.R5]
single evidence-authority implementation.

**(b) Spec sections.** 02 [TS-1.3] (runner selection/validation mapping —
`validate_runner_capabilities` is the named capability gate), 07
[LIVENESS.R5], 01 [CC-2.5].

**(c) The one path.**
- TaskSpec validity is owned by the Pydantic model + its field/model
  validators, entered once via `validate_taskspec_payload` at trust
  boundaries. `_validate_strict_requirements` shrinks to its ~3 live lines
  (or those move into a model validator and the method is deleted);
  the no-op validator and the impossible `metadata is None` guards go.
  The Manager's re-validation on dequeue of the PUBLIC spawn queue stays —
  that queue is a real trust boundary.
- Plugin option validity is owned by `validate_taskspec`; runner
  constructors consume pre-validated options. Microsandbox's shared
  `parse_options` shape is the model: docker and macOS move their
  normalization into a shared parse function called by both validate and
  create (one path invoked twice, no drift surface), and delete the
  `__init__` re-checks. Capability constraints are owned solely by
  `validate_runner_capabilities`; the in-plugin re-checks go.
- Preflight calls the plugin validator once, with `preflight=True`; the
  Protocol docstring in `weft/ext.py` is updated to state the contract:
  preflight checks MUST be a superset of non-preflight checks.
- Host-pid liveness evidence is owned by
  `weft/liveness/host.py::inspect_host_process`; the macOS copy is deleted
  and the probe calls the canonical function. Probe registration relies on
  the registry's documented replace-idempotence; the module-global guards
  go (or, if kept for import-cost reasons, become one shared helper in
  `weft/ext.py` — decide once, apply to all three).

**(d) Why this is the right path.** Each surviving owner is the one the spec
mapping already names; every duplicate has either already drifted (docker's
error message) or is unreachable through the only call sequence core
provides. Consolidating into the plugin SDK surface also serves the recorded
project goal of a public embedding surface.

**(e) Semantic correctness proof.**
- Every deleted re-check gets a covering assertion at the surviving owner:
  the extension test suites (`extensions/*/tests/`) must show the same
  rejection (same option, roughly same message) still fires via
  `validate_taskspec`/`validate_runner_capabilities` when driven through
  `TaskRunner`'s real validate→create sequence — not by calling the deleted
  guard's location.
- Error-message parity check: capture the pre-change rejection messages for
  the re-validated options; post-change messages may differ in wording but
  the failing option and exception type must match; intentional unification
  is recorded in each extension's changelog.
- macOS probe: differential test asserting `_host_process_liveness`'s
  replacement returns `inspect_host_process(...)`-derived verdicts for
  live/stale/zombie/unknown fixtures (reuse the fixtures in
  `weft/liveness` tests).
- TaskSpec: full `tests/core/taskspec` suite green; add one construction
  test for the surviving resolved-spec io presence check
  (`auto_expand=False`, empty io → the same error as today).

Files to modify: `weft/core/taskspec/model.py`,
`weft/core/runner_validation.py`, `weft/ext.py` (docstring),
`extensions/weft_docker/weft_docker/plugin.py`,
`extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`,
`extensions/weft_microsandbox/weft_microsandbox/plugin.py`, extension tests.

---

### WS9 — Monitor-store verification moved off the hot path, with an escape hatch

**Findings.** `ensure_schema` runs `verify_schema_structure` +
`verify_v6` — a full-table re-parse of every stored JSON value — on EVERY
store open (every builtin-cycle worker clone and every control-cleanup
slice). It re-validates single-writer data this same code wrote
transactionally, and it fails closed forever: one noncanonical row anywhere
raises `MonitorStoreUnavailable` on every subsequent cycle, permanently
halting ingest, summaries, and all task-log deletion, with no quarantine or
repair path.

**(a) Invariants protected.** Self-healing over bricking: monitor
degradation must never become a permanent, unrecoverable halt of the
cleanup owner (the same principle as the liveness not-attempted freeze —
self-degradation must not manufacture standing failure). Single-writer
store integrity is protected by the transactional write path, which is the
actual boundary.

**(b) Spec sections.** 05 [MF-5]; the v6 migration history is recorded in
[2026-08-25-monitor-schema-semantic-validation-plan.md](./2026-08-25-monitor-schema-semantic-validation-plan.md)
(completed) — read it first; the per-open verify was that plan's migration
gate and this workstream is explicitly narrowing its scope now that
migration is complete.

**(c) The one path.** Deep verification (`verify_v6` full-table parse) runs
exactly once per store lifecycle event that can introduce foreign data:
migration/creation (as today) — not on routine re-opens. Routine opens do
the cheap structural check only (schema version row + table existence). On
a deep-verification failure the store does not become permanently
unavailable: the failing row(s) are quarantined (moved to a
`quarantine`-suffixed table or exported to the lifetime-report path and
deleted) with a WARNING naming the row, and the store proceeds. Quarantine
is bounded and logged — never silent.

**(d) Why this is the right path.** The write path is transactional and
single-writer; per-open re-verification audits the auditor. The migration
plan's own completion means the one real source of foreign rows (pre-v6
data) is gone. A cleanup owner that can be permanently halted by one bad
row inverts the system's philosophy — every other subsystem self-heals.

**(e) Semantic correctness proof.**
- Existing migration tests (`tests/core/test_monitor_store.py`) stay green
  — creation/migration still deep-verifies. **Review S6:** the
  reopen-time fail-closed tests are NOT migration tests and go red by
  design — enumerate and repoint them to the quarantine assertion (at
  minimum `test_monitor_store_v6_rejects_malformed_owned_json` and its
  siblings that corrupt a v6 store and expect `MonitorStoreUnavailable`
  from a routine re-open). Implementation notes from review: `verify_v6`
  raises on the FIRST bad row — quarantine requires a collect-all rewrite
  of that scan; and `verify_schema_structure` tolerates extra tables, so
  the quarantine table passes structural verification without a v7 bump
  (the stop-gate below is satisfiable). No non-test dependent of the
  fail-closed behavior exists (monitor degrades via
  `MonitorStoreStatus(available=False)`; no runbook references
  `MonitorStoreUnavailable`).
- New: corrupt one JSON cell in a v6 store fixture, open the store, assert
  (i) open succeeds, (ii) the row is quarantined with a warning, (iii)
  ingest and cleanup proceed, (iv) the quarantined row's content is
  recoverable from the quarantine location. This test is red today
  (`MonitorStoreUnavailable` forever).
- Perf note (observable, not gate): per-cycle open cost no longer scales
  with total stored rows.

**Stop-and-re-evaluate gate:** if quarantine requires schema changes beyond
one auxiliary table, stop and report — the fix must not grow into a v7
migration.

---

### Decide-first items (owner decisions — NOT in scope until Van rules)

These were found by the audit but need a product/owner ruling before any
plan slice exists. Recorded here so they are not lost; do not implement.

1. **Retention prune vs monitor-store proofs.** `weft system prune`'s
   retention path selects deletions from `weft.log.tasks` and task-family
   queues using its own terminal-ness fold, blind to the store's
   `summary_emitted_at_ns`/disposition/salvage gates. Options: (a) subordinate
   retention selection to store proofs (share the fold), (b) declare it an
   operator escape hatch and document that it bypasses monitor guarantees,
   (c) both, gated by a flag. Owner call; affects [MF-5] text.
2. **Window-scan fallback task-log engine** (`monitor/cleanup.py` +
   `policies/task_log.py` family selection): a complete second collation
   implementation reachable only when the collation store is disabled. Is
   "store disabled" a supported operating mode? If not, this is the largest
   single removable legacy block in the monitor package.
3. **Dynamic-topology mutation machinery** in `multiqueue_watcher.py`
   (~350 lines: mutation queue, rollback ladder, deferred-SIGINT window):
   spec-backed by 07 [QUEUE.8] for standalone watchers, but no production
   caller can reach it (BaseTask rejects it per [QUEUE.7]; the one direct
   production watcher never mutates). Keeping it is keeping spec-mandated
   capability; removing it is a spec change to [QUEUE.8]. Owner call.
4. **Four terminal-ness folds of `weft.log.tasks`** (store merge, window
   scan, retention, snapshot reducer): full unification is a large change
   with real semantic choices (payload-status vs state-status precedence).
   Deferred; WS5 deliberately consolidates only the tid-mappings fold.
   Revisit after decide-first item 1.

## 4. Invariants and Constraints (whole plan)

Must not change:

- TID format and immutability; forward-only state transitions.
- Reserved queue policy semantics (WS1 *strengthens* enforcement of the
  policy; it must not alter what KEEP/REQUEUE/CLEAR mean).
- `spec`/`io` immutability after TaskSpec creation.
- Spawn-context process behavior; `weft.state.*` queues stay runtime-only
  and excluded from dumps.
- Public CLI shapes and exit codes (WS7 removes dead internals; the typed
  surface's behavior is the contract). The two recorded client contract
  changes (typed errors from `client.system.*`; empty-selection error from
  `stop_many`/`kill_many`) are intended and must be called out in the
  changelog.
- Edge-triggered append-only mapping publication ([OBS.6a]) — WS3 extends
  the pattern to endpoints; nothing may reintroduce read-before-write.
- No weft path ever signals or kills a process it did not spawn.

Review gates for every slice: no new execution path; no new dependency; no
drive-by refactors outside the workstream's file list; no mock-heavy
substitute where `WeftTestHarness`/`broker_env` is practical; deletion
slices must show the pre-deletion zero-caller grep in the commit message.

**Architecture-test note (applies to WS2/WS3):** the existing delete-custody
AST test (`tests/architecture/test_liveness_boundaries.py`) has a known
blind spot — a queue-name constant and a `.delete(` call in different
functions of the same module evade it. The registry custody tests added in
WS2/WS3 must use the stronger rule: any module that imports the registry
queue-name constant and contains a `.delete(` call on a queue object is
flagged unless the module is on the custody allowlist. Extend the liveness
test to the same rule in the same change.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `74a2d3bc` — all Source specs listed above, at plan authoring time
  (2026-08-31). The liveness reaper plan's spec deltas ([LIVENESS.R1]–[R10],
  [OBS.13.7] rewrite) exist in the worktree pending that plan's landing;
  WS2/WS3/WS5 slices record a promotion baseline identifier after the
  liveness landing per the sequencing dependency in §2.

## Proposed Spec Delta

Promotion strategy per file:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/01-Core_Components.md | A — in-file, text before link claims | [CC-2.4.1] add custody rule |
| docs/specifications/03-Manager_Architecture.md | A | [MA-1] add custody paragraph |
| docs/specifications/05-Message_Flow_and_State.md | A | [MF-2] add disposition sentence |
| docs/specifications/00-Quick_Reference.md | D — correction of shipped behavior | queue table |

### [CC-2.4.1] — append to "Current rules" bullet list

> - custody: the owning task appends its own registration records and
>   deletes its own rows in `unregister_endpoint_name()`; the runtime
>   pruning engine is the only other deleter of `weft.state.endpoints`
>   rows. Registration is a plain append with no read-before-write
>   deduplication; superseded rows are inert under latest-wins reader
>   reduction until pruned. Resolution and listing are read-only: readers
>   classify stale owners and filter them from results, and never delete
>   registry rows.

### [MA-1] — insert after the `weft.state.services` schema paragraph (03:~401)

(Reworded per review S7: the rule scopes DELETION only — [MA-3] proactive
supersession and operator replacement legitimately APPEND superseded rows
for other TIDs, and that is unaffected.)

> `weft.state.services` custody: a manager DELETES only rows it wrote — its
> own registration, heartbeat supersession, unregistration, and the
> managed-service rows it owns. Appending records for other TIDs (proactive
> supersession per [MA-3], operator replacement) is unaffected by this
> rule. The runtime pruning engine is the sole deleter of stale rows
> written by other processes, behind its min-age and keep-recent gates.
> Status, list, bootstrap, and leadership readers classify and filter stale
> or superseded records but never delete them; stale registry evidence
> degrades status and selection, it does not authorize reader-side
> destruction.

### [MF-2] — insert after "crash leaves the message in reserved state for explicit operator recovery"

(Extended per review S3 to cover failed success-acks, and softened so the
exact-move→bulk-move fallback inside one policy application is not read as
a forbidden second application.)

> Reserved disposition applies the configured policy through one
> application pass (which may include an internal exact-then-bulk
> fallback). A row that survives a failed policy application (for example
> a partial requeue move failure) or a failed success-path acknowledgement
> delete is left in `T{tid}.reserved` for the operator/monitor recovery
> path above; no task-side or manager-side code drains a reserved queue as
> a backstop after the policy or acknowledgement has run.

### 00-Quick_Reference queue table — correction (pending WS7.6 verification)

> Remove the `weft.manager.ctrl_in` and `weft.manager.ctrl_out` rows (the
> runtime addresses manager control via the manager task's own
> `T{tid}.ctrl_in`/`T{tid}.ctrl_out`, discovered through
> `weft.state.services` records). Update the CLAUDE.md §2 queue-naming
> block in the same change. Land only after the WS7.6 grep verification;
> if a legacy-record compat consumer exists, replace this correction with
> wording that marks the names as legacy record fields with no live queue.

## 5. Tasks

Dependency-ordered. Tasks 1–3 are the spec-promotion slice; workstream
tasks are then independently landable in any order EXCEPT the stated
liveness-landing dependency for WS2/WS3/WS5 and the rule that WS7.1's test
repointing lands with (not after) its deletions.

1. **Spec-promotion slice.** Apply the §4c deltas to [CC-2.4.1], [MA-1],
   [MF-2] (strategy A — no implementation-mapping claims yet). Hold the
   00-Quick_Reference correction until task 10. Add this plan to each
   touched spec's `## Plans` / `## Related Plans` section. Record the
   promotion baseline identifier in this plan.
   - Verify: `./.venv/bin/python -m pytest tests/specs/ -q` (plan metadata
     and spec-policy gates green).
2. **Independent review of this plan and the deltas** (see §8). Address
   findings before any code slice.
3. **Architecture custody tests (red where honest).** Write
   `tests/architecture/test_registry_custody.py` with the strengthened
   matcher (§4 note) for `weft.state.services` and `weft.state.endpoints`;
   allowlist the current extra deleters with `# TODO(WS2/WS3)` markers so
   the test lands green but the allowlist shrinks per slice; extend the
   liveness test's matcher identically.
4. **WS1 reserved disposition.** Red-first REQUEUE partial-failure test;
   consolidate policy application to one shared BaseTask method; delete the
   backstop drains in tasks and manager.
   - Verify: `./.venv/bin/python -m pytest tests/tasks/ tests/core/test_manager.py -q`
5. **WS2 services custody** (after liveness landing). Characterization
   tests (including the S4 accepted-behavior matrix) → remove reader-side
   deletes and peer-row deletes → reduce the supersede re-check to a
   single post-write re-scan with unchanged detection semantics, pinned by
   the B1 interleaving regression → shrink the custody-test allowlist.
   - Verify: `./.venv/bin/python -m pytest tests/core/ tests/cli/test_cli_system.py -q`
6. **WS3 endpoints custody** (after liveness landing). Red-first
   startup-race test → append-only registration → readers stop deleting →
   dead selection guards → shrink the allowlist.
   - Verify: `./.venv/bin/python -m pytest tests/core/ tests/tasks/ -q`
7. **WS4 terminal path + vocabulary.** Interactive consolidation honoring
   the S1 ordering/dedup rule, with the envelope-parity, ordering, and
   retry-differential tests; single terminal frozenset; single retirement
   call site with the S2 `if not errors` gate fix and report_only test.
8. **WS5 fold consolidation** (after liveness landing). Differential
   fixture first; repoint folds; delete duplicates; grep gate.
9. **WS6 guard repairs.** Items 1–5, each its own commit with its stated
   proof.
10. **WS7 dead-generation retirement.** Sub-items 1–6 in order, each with
    its pre-deletion grep recorded; 00-Quick_Reference + CLAUDE.md
    correction lands inside sub-item 6 behind its verification.
11. **WS8 boundary re-validation removal.** TaskSpec shrink; plugin
    consolidation (docker, macOS to the microsandbox parse-options shape);
    single preflight call + Protocol docstring; macOS probe repoint;
    registration-guard decision applied to all three plugins.
12. **WS9 store verification.** Read the 2026-08-25 migration plan first;
    move deep verify to migration/creation; add quarantine with the
    red-first corrupted-row test.
13. **Traceability reconciliation (final slice).** Add implementation-
    mapping claims + reciprocal `Spec:` backlinks for the promoted custody
    text; update `_Implementation mapping_` blocks in [CC-2.4.1] and
    [MA-1.4] to reflect removed functions; deviation log closed (no
    `pending`); rerun all gates; flip plan status per the index rules.

## 6. Testing Plan

- Harness: `WeftTestHarness` for CLI/manager/lifecycle; `broker_env` + real
  `Queue` for queue semantics. Do not mock queues, reservations, state
  transitions, or process lifecycle; inject failures at the queue-object
  seam (wrapper raising on the Nth call) rather than patching SimpleBroker
  internals.
- Red-first tests named above: WS1 REQUEUE partial-failure preservation;
  WS3 startup-race row survival; WS4 interactive terminal-retry
  differential; WS9 corrupted-row quarantine. Each is the exact regression
  its workstream exists to prevent.
- Characterization-before-delete is mandatory for WS2/WS3/WS5/WS7: pin the
  surviving path's observable behavior, then delete, tests stay green.
  Repointed legacy tests keep their behavioral assertions.
- Every deleted guard/function gets either a grep gate (zero references) or
  a covering assertion at the surviving owner — never silent removal.
- Edge case in scope: WS5 short-TID collision with republished rows (the
  first-wins/newest-wins divergence). Edge case out of scope: multi-writer
  races on `weft.state.*` beyond what the custody tests enforce (the
  single-deleter design removes the race rather than testing it).

## 7. Verification and Gates

Per-task verification as listed in §5. Final gates before claiming any
workstream done:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/ tests/architecture/ -q
```

Full suite is required for WS1, WS2, WS3, WS4 (durable-spine blast radius);
narrower named suites suffice per-task for WS6–WS9 with the full suite at
each workstream's completion. Rollback: every workstream is a revertable
unit (no cross-workstream data-format changes); WS9's quarantine table is
additive and ignorable by older code.

## 8. Independent Review Loop

Reviewer: an agent from a different family than the author. Read this plan
including §4c, the audit-cited code sites (by symbol), and 07
[QUEUE.6]/[OBS.6a]/[LIVENESS.R3]. Stance:

> Look for errors, bad ideas, latent ambiguities, and performative
> overengineering — this plan DELETES guards, so the review must
> specifically try to prove a deleted guard is load-bearing: for each
> workstream, construct the concrete scenario in which the removed
> filter/backstop/re-check was the only thing preventing data loss or
> divergence, or state that none exists. Could you implement each
> workstream confidently against the promoted deltas?

Feedback returns as findings the author must answer point-by-point in this
plan (append-only review record) before the corresponding slice starts.

## 9. Out of Scope

- The four decide-first items (§3 end) until Van rules on each.
- The liveness reaper plan's two open review blockers (owned there).
- Any SimpleBroker-layer change; any new abstraction (explicitly: no
  registry framework, no generic custody library — the custody rule is a
  sentence per spec section plus an architecture test).
- Behavior changes to admission control, leadership selection outcomes,
  reserved-policy meanings, or public CLI shapes beyond the two recorded
  client contract fixes.
- The `_WORKER_SNAPSHOT_EXPECTED_FIELDS` ledger design (noted as friction;
  its consumer is load-bearing monitor code — revisit separately).

## 10. Fresh-Eyes Review

Author self-review performed 2026-08-31 (separate pass after drafting).
Findings applied: added the line-number caveat and symbol-based location
rule; added the WS2 stop-gate for load-bearing peer-row deletes; added the
WS5 reaper-fold agreement check (STOP condition); made WS7.1's
legacy-test-asserts-missing-behavior case an explicit STOP; pinned the
00-Quick_Reference correction behind WS7.6's grep verification instead of
landing it unconditionally; required the strengthened AST matcher in both
new and existing custody tests. External independent review (task 2)
completed 2026-08-31; all findings dispositioned in the Review Record
below and applied in place. The plan remains `draft` until Van approves
the review dispositions — in particular the B1 resolution (keep the
supersede re-check, reduce its replay cost) and the recorded accepted
behavior changes (S4, N3(b), and the two client contract fixes in WS7.2).

## Review Record (append-only)

**2026-08-31 — independent adversarial review** (different agent family;
stance per §8: prove each deleted guard load-bearing). Dispositions:

| Finding | Verdict | Author disposition |
|---------|---------|--------------------|
| B1 — WS2 supersede re-check is load-bearing (`--replace` vs in-flight heartbeat race; latest-per-TID snapshot masks the superseded row; [MA-3] convergence runs backwards) | Blocker, confirmed against code | Applied: re-check KEPT; scope reduced to collapsing four registry replays into one post-write re-scan; B1 interleaving regression test added to WS2(e); §3 finding text corrected |
| S1 — interactive terminal-envelope ordering/dedup unspecified | Should-fix | Applied: one envelope, from finalize, after output drain and final stream markers; base inline emission suppressed/deferred; `event` field dropped (no reader keys on it) |
| S2 — store-cycle retirement call covers reserved-slice `if not errors` gate and report_only mode | Should-fix | Applied: surviving call moved out of the error gate; report_only retirement test required |
| S3 — success-ack drain sites + `cleanup_on_exit` 02-TaskSpec mapping unaddressed; [MF-2] delta too narrow | Should-fix | Applied: three success-ack sites enumerated with the no-re-execution proof; 02-TaskSpec mapping update added to WS1; [MF-2] delta extended to failed success-acks and softened re internal fallback |
| S4 — stale-row persistence and recurring probe latency for CLI-only users | Should-fix | Applied: recorded as accepted behavior changes in WS2(d) and added to the characterization matrix |
| S5 — WS5 cited a nonexistent ambiguity error | Should-fix | Applied: newest-wins decided; collision-error surface explicitly out of scope with a STOP |
| S6 — WS9 reopen-time fail-closed tests go red; quarantine needs collect-all scan; extra tables pass structural verify | Should-fix | Applied: tests enumerated for repointing; collect-all rewrite named; stop-gate confirmed satisfiable |
| S7 — [MA-1] delta wording collides with [MA-3] appends; [MANAGER.14] over-claimed | Should-fix | Applied: delta scoped to deletion; [MANAGER.14] framing corrected in WS2(a) |
| N1 — WS1 red-test seam named wrong API | Note | Applied: `move_many` batch seam (task) vs `move_one`+fallback (manager) |
| N2 — [OBS.1] retry is implementation elaboration, not spec text | Note | Applied: WS4(a) reworded |
| N3 — WS3 sound; unregister message-id capture; reaped-mapping prune exposure | Note | Applied: capture form chosen; residual risk recorded as accepted |
| N4 — WS6 spot-checks confirmed; extensions re-verify post-liveness | Note | No edit needed; line-number caveat already mandates re-verification |
| N5 — WS7.1 too large as one unit; extra legacy-test file | Note | Applied: per-command-module sub-slices |
| N6 — plan not overengineered; no ceremony to trim | Note | Recorded |

Reviewer's readiness verdict after edits: WS3/WS6/WS7/WS8 ready as
written; WS1/WS5/WS9 ready with the applied edits; WS2/WS4 ready only via
the applied B1/S1/S2 resolutions above.
