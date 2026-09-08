# Registry Custody Contracts Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3.1]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/01-Core_Components.md [CC-2.4.1]; docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5], [OBS.6], [OBS.6a], [MANAGER.14], [LIVENESS.R3]; docs/specifications/10-CLI_Interface.md [CLI-1.2.3]
Superseded by: none

Class: 5 — changes normative custody text in [MF-3.1], [MA-1]/[MA-1.4],
[CC-2.4.1] and adds a short-TID resolution rule; touches manager and task
execution paths, so hardening applies. Plan type: implementation with spec
revision. Promotion strategy: **A** for the five spec files touched here (05, 03, 01, 10, 07 — the last for an [OBS.5] cross-reference and the [OBS.13.6] malformed-row policy); the [OBS.5] short-form derivation is split out to
[2026-08-31-short-tid-derivation-plan.md](./2026-08-31-short-tid-derivation-plan.md).
Depends on
[2026-08-31-runtime-identity-custody-plan.md](./2026-08-31-runtime-identity-custody-plan.md)
landing first (endpoint characterization tests are meaningless while
persistent tasks lose their claims after one item). Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

Apply the custody rule refined by review to the two remaining shared
registries and to short-TID resolution:

> Writers may exact-delete rows they own; one cross-owner reaper (the
> runtime pruning engine) owns stale-row policy; readers filter and never
> delete.

Today `weft.state.services` has four deleters across three process types
with three staleness predicates, and `weft status` performs registry
writes and blocking PING probes; `weft.state.endpoints` is deleted by
readers with no age gate, and registration does scan-write-scan-delete;
short-TID collisions resolve first-wins in one reducer and last-wins in
another. Both reader-side deletions are currently **spec-mandated** ([MF-3.1]
"resolve and list surfaces opportunistically prune stale claims";
[MA-1.4] callers "prune dead or expired active records"), so the spec text
changes first.

**Short TIDs (Van, 2026-08-31): a loud ambiguity error here; the folded
derivation in its own plan.** This plan adds the error and introduces the
single short-form owner `weft/helpers/__init__.py::tid_short_form(tid)`
with **today's** formula (`tid[-10:]`), so every producer and the
process-title matcher stop slicing digits themselves. The derivation
change — expanding the counter-zero residue space 1,024-fold, which permits
~32× as many distinct TIDs at the same birthday-collision probability under
independent uniform sampling — is
promoted and implemented separately in
[2026-08-31-short-tid-derivation-plan.md](./2026-08-31-short-tid-derivation-plan.md),
per the Codex review: a derivation change touches many production sites,
fixtures, copied identifiers, and default pipeline names, and its
promotion must be independently reviewable and revertible.

## 2. Source Documents

- 05 [MF-3.1] lines ~285–290 (the "opportunistically prune" bullet and the
  liveness-check bullet).
- 03 [MA-1] step 4 "Registry heartbeat" lines ~152–164 (the sentence
  "callers reduce to the latest relevant record…, prune dead or expired
  active records, and then filter…"); [MA-1.4] implementation mapping;
  [MA-3] proactive supersession (a lower-TID manager may append a
  `superseded` row for a higher TID).
- 01 [CC-2.4.1] "Current rules" bullets.
- 07 [OBS.5] (short form = low-order digits), [OBS.6] (newest row per full
  TID), [MANAGER.14] (stale proof degrades status/selection — silent on
  deletion), [LIVENESS.R3] (sole-deleter precedent).
- 10 [CLI-1.2.3] line ~543 ("`weft task tid` resolves short TIDs… via the
  TID-mapping queue" — collision unspecified).
- Prior plan: [2026-08-08-registry-selection-pruning-authority-refactor-plan.md](./2026-08-08-registry-selection-pruning-authority-refactor-plan.md).

## 3. Context and Key Files

**Endpoints.**
- `weft/core/endpoints.py` — `list_resolved_endpoints` (:364–414; the
  delete at :392–396), `_classify_latest_endpoint_records` (:338–361),
  `_record_owner_is_live` (:294–320), `find_endpoint_registry_message`
  (:217–226, becomes unused), canonical selection (:399–410; `canonical_record`
  reduces to `ordered[0]` — the `None` branch and `next(..., ordered[0])`
  fallback guard states the TID invariant precludes), `_latest_tid_mapping_entries`
  (:264–291, keeps `(message_id, payload)` internally, returns payload map).
- `weft/core/tasks/base.py` — `register_endpoint_name` (:2549–2586:
  pre-scan, write discarding the returned id at :2558, post-scan,
  delete-prior), `unregister_endpoint_name` (:2592–2623, uses stored id,
  scans as fallback), `_claim_configured_runtime_endpoint` (:2495–2511,
  the only production caller, once from `__init__` :348).
- `weft/core/pruning/runtime.py::_endpoint_candidates` (:557–626) — the
  cross-owner reaper, min-age gated. Unchanged.
- Test: `tests/tasks/test_task_endpoints.py:126–145`
  (`test_task_reregistration_replaces_prior_endpoint_claim`) — the only
  thing holding the rename-on-reregister behavior; no production caller
  registers twice.

**Services.**
- `weft/core/manager_runtime.py` — `_snapshot_registry` (:225–279; prune
  disposition and delete), `_manager_registry_disposition` (:181–207;
  keep/omit/prune three-way with keyed-PING probes
  `_manager_record_has_matched_pong` :691–736), `_mark_manager_stopped`
  (:854–953; deletes all rows for a TID then writes the terminal record),
  `_lookup_manager_pid` (:810–825, a private mapping fold),
  `_live_host_processes_from_handle` / `_manager_handle_has_live_host_process`
  (:615–631, private copies of `weft/helpers/__init__.py:115–131`).
- `weft/core/manager.py` — `_prune_expired_manager_registry_entries`
  (:2049–2107, age-based delete of *any* canonical manager row),
  `_active_dispatch_manager_records` (:3112–3117, deletes peer rows on
  stale proof), `_register_manager` (:1983–2016, post-write supersede
  re-check — **keep**, unchanged; the replay-count reduction proposed earlier is dropped, see §4), own-row lifecycle
  (heartbeat delete, supersede, `_unregister_manager`,
  `_prune_older_self_registry_entries` — own-row custody, **keep**),
  `_prune_managed_service_registry_history` (:5862–5876) and its one
  caller in `_observed_service_candidates_by_key` (:5825–5833 — the
  manager's per-convergence deletion of *managed-service* rows: expired
  beyond `_manager_registry_retention_ns()` = 300 s, superseded per
  owner, and older non-live rows; **delete**, see §4), `_latest_tid_runtime_handle(s)`
  (:5157–5201, private fold).
- `weft/core/pruning/runtime.py::_manager_candidates` (:344–424),
  `_service_candidates` (:427–480) — **two predicates added** (review B1; retirement-rule reconciliation 2026-09-04):
  today `_manager_candidates` classifies only `active` rows with a stale
  handle and unconditionally protects the newest row per TID
  (`keep_recent_per_key >= 1`), so once the Manager's peer-row age delete
  goes, terminal `stopped`/`superseded` rows would have **no deleter at
  all** — every `weft manager stop`, idle exit, and `--replace` would
  leave a permanent row replayed by every registry read. Add: a
  non-`active` newest row older than `min_age` is a candidate. This is in
  scope (it is the one owner the custody rule names). Rewrite
  `tests/core/test_manager.py:~6142 test_manager_registry_prunes_expired_rows_on_refresh`,
  which pins the removed peer delete (and the malformed-schema delete).
- `weft/core/manager_runtime.py::_manager_record_stale_status` (~:660–668)
  — returns `(stale=True, definitive=False)` for **any** host-pid handle,
  so every crashed manager's row (not just "ambiguous" ones) triggers the
  keyed PING in `_manager_registry_disposition` on every read. In scope
  (review S2): a host-pid handle whose `(pid, create_time)` no longer
  matches is **definitively stale** ([LIVENESS.R5] identity) — no probe;
  probe only when identity cannot be evaluated. Without this, each
  `weft status`/`weft run`/`weft manager list` would pay
  `min(CONTROL_SURFACE_WAIT_TIMEOUT=2.0, MANAGER_COMPETING_STARTUP_GRACE_SECONDS=0.5)`
  = 0.5 s per crashed canonical row for ~1–2 h after a crash (pruner
  `min_age` 3600 s + maintenance interval 3600 s), or indefinitely with no
  TaskMonitor.
- `weft/core/control_probe.py::pong_proves_dispatch_eligible` — the one
  PONG eligibility gate ([MA-1.4]); do not add a second narrowing rule.

**Short TIDs and folds.**
- `weft/commands/tasks.py` — `resolve_full_tid` (:158–170, first-wins),
  `mapping_for_tid` (:118, last-wins), `_read_tid_mapping_entries` (:107),
  `task_tid` PID path (:192, `reversed`).
- `weft/commands/system.py` — `_read_tid_mappings` (:329, last-wins),
  `_latest_tid_mapping_entries` (:343, drifted copy: no empty-string check,
  no strict decode, no int cast).
- `weft/_constants.py::TASKSPEC_TID_SHORT_LENGTH` (:79) = 10 → short =
  `tid[-10:]`; collision odds ~1e-7 per pair, reachable in a long-lived
  context.
- `weft/core/tasks/liveness_monitor.py::_reconcile_mapping_rows`
  (:159–213) — the reaper's own fold retaining `MappingRow(tid,
  message_id, payload, generation)`.

**Short-form owner.** There is no short-form helper today; every site
slices `[-10:]`/`[-TASKSPEC_TID_SHORT_LENGTH:]` directly (producers:
`base.py:253` and its uses at :678, :1845, :2245, :2335, :2391;
`commands/tasks.py` :528, :571, :761, :1081, :1195; `commands/system.py`
:368, :1427, :1434, :1597; `commands/_task_snapshot_reducer.py` :196,
:202, :269; `commands/task_monitor.py:424`; `task_monitor.py:1287`;
`manager.py:547`; `core/pipelines.py:405`; `cli/app.py:1511`; and the
process-title *matcher* `weft/liveness/host.py:47`). This plan introduces
`tid_short_form` with today's formula and repoints all of them; the
derivation plan later changes only the helper body. Consumers of the
*stored* `short` (`commands/tasks.py:166`, `commands/system.py:335`)
switch to deriving from `full`. The reaper's `valid_tid_mapping_payload`
(`weft/liveness/policy.py:163–170`) requires only that `full` and `short`
are non-empty strings; existing fixtures include `{"full": "undecidable",
"short": "undecidable"}` (`tests/core/test_manager.py:2157`, :2691), so a
`full` value is **not** guaranteed to be a 19-digit TID.

## 4. Invariants and Constraints

- Deletion custody: a process deletes only rows it wrote; the pruning
  engine is the sole cross-owner deleter; readers never delete.
- Appending records for other TIDs per [MA-3] proactive supersession and
  operator replacement is **unaffected** (the rule scopes deletion, not
  writes).
- The `_register_manager` post-write supersede re-check stays: with
  `replace_active_manager` still appending superseded rows, an incumbent
  heartbeat landing after the CLI's wait loop exits publishes a newer
  active row that masks the superseded row in the latest-per-TID snapshot;
  without the re-check convergence reverses ([MA-3] proactive supersession
  would supersede the replacement). Own-row delete, permitted. The earlier proposal to reduce `_register_manager` to two scans is **dropped** (Codex round 3): it is an optimization unrelated to custody, and "one pre-write and one post-write scan" did not specify what each scan returns for the self-status, lower-manager, older-self, and supersession checks. `_register_manager`'s replay structure is unchanged by this plan.
- One fold for "newest mapping row per full TID" ([OBS.6]);
  `endpoints.py` is the owner (it already backs admission control and
  pruning per [MA-1.8]).
- Short-TID collision is an **error**, never a silent choice: `weft task
  kill <short>` on a collision must not target the wrong task.
- Short-form derivation has **one owner** — `weft/helpers/__init__.py::tid_short_form(tid)`
  — and every producer and the title matcher call it; no site slices
  digits itself. In this plan the helper implements today's formula; the
  contract: a 19-digit numeric string → ten characters; anything else →
  `ValueError`. Short *resolution* never calls it on a non-derivable
  `full`: rows whose `full` is not a 19-digit numeric string are
  **skipped without aborting the scan** (Codex: otherwise one
  `"undecidable"` row would deny every short-TID command).
- Resolution computes the short form from a row's `full` at read time and
  **never trusts the stored `short` field**.
- **Canonical mapping fold validity**: the canonical fold adopts exactly
  `decode_tid_mapping_row` validity (non-empty `full` and `short`); "any
  row" in [CLI-1.2.3] means any *valid* newest row, and short resolution
  additionally requires a derivable `full`. Exposing message ids is done
  by adding a tuple-returning fold and keeping the payload-map function as
  a thin view (its three consumers — endpoint liveness, streaming pruning,
  `Manager._observe_admission_usage` — are unchanged).
- **Host identity is tri-state, with an explicit reduction** (Codex B3,
  round 2 — `inspect_host_process` returns `stale/process_absent` for
  `NoSuchProcess` and cannot itself tell a dead PID from one invisible
  from a container namespace): per recorded host process, `create_time`
  observable and equal → `live`; observable and different → `stale`;
  `process_absent` while `detect_container_runtime()` reports the reader
  is containerized → `unknown`; `process_absent` on a host reader →
  `stale`; recorded `create_time` missing → `unknown` (never PID-only
  liveness). Across a handle's processes: any `live` → live; else any
  `unknown` → unknown (keeps today's bounded keyed-PING rescue); else
  `stale` (definitive, unprobed). Applied in **both**
  `manager_runtime._manager_record_stale_status` and
  `Manager._manager_record_liveness` (~:2471).
- **Service-registry custody, stated without contradiction** (Codex B4,
  round 2): a row's logical owner may exact-delete its rows at any time;
  the runtime pruner may delete *any* row only under a named predicate.
  Three owner classes: (i) rows with the manager's own `owner_tid`,
  including an operator-written `superseded` row for that TID (writer and
  owner differ; the owner may delete it); (ii) managed-service rows the manager writes for its supervised singleton services — `owner_tid` is the service's TID and the payload carries no author field (both writers, `_register_managed_service_owner` ~:1479 and `_register_terminal_managed_service_owner` ~:1521, discard `Queue.write()`'s id). **The manager does not delete these rows at all**: `_prune_managed_service_registry_history` and its call are deleted, and the runtime pruner's `_service_candidates` becomes their sole deleter, passing `ttl_ns=int(MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000)` (the TTL every reader already applies through `reduce_service_ownership`) to `plan_service_owner_history_prune` instead of today's `-1`, still behind the pruner's `min_age`. Verified basis (2026-09-04 retirement-rule reconciliation): a managed-service row is written once at spawn (:1262) and never republished, so every reader treats it as expired after 300 s regardless of who deletes it; today the manager deletes expired rows for its desired keys each convergence, and the first draft's retained-id store would have left rows whose ids were lost at restart or `--replace` — newest status `active` — with no deleter at all (the pruner protects every newest live-status row per owner and never expires). The pass-through is one argument; the id store (add-on-write, remove-on-delete, retry, restart loss) is not built; (iii) v1 schema rows — `discard_v1_service_registry_rows` is a spec-mandated migration sweep at every reader ([MANAGER.3]), an explicit exception; (iv) rows carrying the current service-owner schema tag that fail that schema's field validation (`parse_service_owner_row` → `malformed`): today two deleters remove them — the manager's heartbeat sweep in `_prune_expired_manager_registry_entries` (any schema-tagged row `parse_service_owner_record` rejects) and the pruner's `malformed_service_owner_row` candidate (runtime.py ~:354–370, pinned by `tests/commands/test_runtime_prune.py::test_manager_prune_reports_malformed_service_owner_rows`) — while [MF-5] ~:1372 says foreground runtime pruning "must preserve … malformed or unknown-shape rows" (Codex round 3). This plan removes the first deleter, so the second must be spec-authorized rather than left contradicting [MF-5]: the [MF-5] and [OBS.13.6] deltas below name schema-tagged-but-invalid `weft.state.services` rows disposable behind `min_age`, the pattern [OBS.13.6] already uses for `weft.log.tasks` and `weft.state.tid_mappings`. Rows with no recognized schema tag stay preserved (a future schema from a newer Weft is not malformed). **Owner decision (Van, 2026-09-04): malformed rows are pruned and, when logging is on, each deletion is logged as an error.** Mechanism: in `weft/core/pruning/runtime.py::_apply_candidates`, call the existing `apply_exact_prune_candidates` with `exact_status=any(candidate.reason == "malformed_service_owner_row" for candidate in candidates)`. A pass containing malformed candidates therefore obtains actual per-ID deletion status; passes without them retain batching. Do not use `reconcile_missing` for logging: it treats an already-absent row as applied, which does not prove this pass deleted it. After the call, every applied candidate with `reason == "malformed_service_owner_row"` that was deleted emits `send_log("Pruned malformed service-owner row", level=logging.ERROR, extra={"queue": …, "message_id": …, "owner_tid": …})` through the `weft.helpers` logging facade, which is already gated on `WEFT_LOGGING_ENABLED` and emits nothing otherwise; the TaskMonitor maintenance pass keeps counting these as deletions, never as cycle errors. The facade is kept by plan 5 (Van, 2026-09-04); this is its first in-repo production caller.
- **Pruner predicate for manager rows** (Codex B1, round 2 — minimum age
  alone is unsafe: `weft system prune --min-age 0 --apply` could delete a
  fresh `superseded` row before the incumbent consumes it, reversing a
  `--replace`; and `draining` is in `LIVE_SERVICE_STATUSES`): the pruner deletes a manager row only when the owner is **stale** **and** the row is older than `min_age`. Stale means: a host-managed identity definitively stale under the tri-state reduction below, or an `external-supervisor` handle whose registered probe returns a definitive `stale`. Missing or inconclusive probes are `unknown` evidence per [MA-1] (~:167); today's `_manager_record_stale_status` (manager_runtime.py:660–680) converts that `unknown` into non-definitive staleness by age alone, which contradicts [MA-1] and is **removed** in 4c (Codex round 3: the reconciliation draft had made an aged unprobeable `superseded` row prunable and never-prunable at once). **`unknown` owners are pruned once past all timeouts** (owner decision, Van, 2026-09-04): the pruner deletes an `unknown`-owner row when the row is older than both `min_age` and `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`; no probe is issued at prune time — a PING-at-prune-time guard was proposed and rejected as one more guard. Rationale: a manager that has not heartbeated for the larger of those windows (an hour by default) is dead for every purpose the registry serves, and its heartbeat path already treats a self-owned `superseded` row as shutdown authority, so a live manager cannot lose supersession evidence it has had an hour to consume. This is the only way rows of a containerized manager ever retire once the peer TTL sweep in `_prune_expired_manager_registry_entries` is removed (retirement-rule reconciliation 2026-09-04); it replaces the earlier "never prune unknown" text — the same predicate for `active`, `draining`,
  `stopped`, and `superseded`, including the newest row for every status.
  Both definitive-stale and expired-unknown predicates override keep-newest. A `superseded` or `draining` row for a live owner is never pruned; one for an `unknown` owner is pruned once past both timeouts — consistent with [MF-5]'s rule that rows whose owner is live or ambiguous are preserved *within* the windows; the [MF-5] delta below says so.
- Observable differences (state them, do not hide them; Van 2026-09-04: no diminution of capability is acceptable): stale service rows persist — filtered out of every read, visible only to `weft queue peek` — until the TaskMonitor maintenance pass (hourly, default on) or `weft system prune` runs; in a deployment that sets `WEFT_TASK_MONITOR_MAINTENANCE=0` the operator's `weft system prune` is the only deleter of them, where today the next manager heartbeat was. **Read cost stays bounded exactly as today:** readers issue the keyed-PING rescue only for an `unknown` row younger than `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`; an `unknown` row older than that window is omitted from the active view without a probe (the same "past all timeouts" rule the pruner applies, minus `min_age`; safe because a live manager refreshes its row every `MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS` = 30 s, so a 300 s-old newest row is ten missed heartbeats). Without this, every `weft status`/`weft run`/`weft manager list` would pay 0.5 s per crashed containerized manager row for up to two hours — a diminution; with it, today's one-probe-then-omit cost is preserved and the read path loses a guard rather than gaining one. Applied in `_manager_registry_disposition` and `Manager._manager_leadership_proof` in 4c. An old endpoint row
  of a live task whose newest mapping row was retired under [LIVENESS.R4]
  is prunable and, unlike mappings, does not self-heal by republish —
  strictly less exposure than today's ungated reader delete;
  re-registration on restart recreates it. `stop_manager --force` adopts
  newest-row PID semantics **and** stops returning success when no
  controllable PID is found (today `stop_manager` falls through to
  `return True, None`).

Review gates: no new execution path; custody proven by behavioral tests,
not a module-allowlist AST test; external review of the deltas before
promotion (Class 5) and before implementation (hardening).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 05, 03, 01, 07, 10 at plan authoring time (2026-08-31).
  Promotion baseline identifier: recorded after task 1.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | A | [MF-3.1] replace one bullet; [MF-5] ~:1372 amend the preservation bullet |
| docs/specifications/03-Manager_Architecture.md | A | [MA-1] step 4 sentence; custody paragraph after the registry schema (~:401) |
| docs/specifications/01-Core_Components.md | A | [CC-2.4.1] add bullet |
| docs/specifications/10-CLI_Interface.md | A | [CLI-1.2.3] add collision sentence |
| docs/specifications/07-System_Invariants.md | A | [OBS.5] gets only a cross-reference to [CLI-1.2.3]'s ambiguity rule (the derivation change is in the short-TID derivation plan); [OBS.13.6] example list extended |

### [MF-3.1] — replace "resolve and list surfaces opportunistically prune stale claims whose owner is terminal or no longer live"

> - resolve and list surfaces classify claims whose owner is terminal or
>   no longer live out of their results; they never delete registry rows.
>   The owning task exact-deletes its own claim on clean shutdown; the
>   runtime pruning engine is the sole deleter of stale claims written by
>   other processes, behind its minimum-age gate

### [MA-1] step 4 — replace "…prune dead or expired active records, and then filter to canonical non-superseded live managers…"

> …filter out dead or expired active records, and then filter to canonical
> non-superseded live managers before treating the result as the
> active-manager view. Readers do not delete registry rows.

### [MA-1] — insert after the `weft.state.services` schema paragraph

> `weft.state.services` custody: a row's logical owner may exact-delete
> its rows at any time; the runtime pruning engine may delete any row
> only under a named predicate; readers otherwise never delete. Owner
> classes: (1) rows whose `owner_tid` is a manager's own TID — its
> registration history, heartbeat supersession, unregistration, and a
> `superseded` row an operator wrote for it — are owned by that manager.
> (2) Managed-service rows a manager writes for the singleton services it
> supervises carry the service's `owner_tid`; managers do not delete them.
> They are read through the service-owner TTL and are deleted only by the
> runtime pruning engine once older than both that TTL and the pruner's
> minimum age, together with superseded and surplus history rows.
> (3) Rows of the retired v1 service-owner schema are removed by every
> reader's mandatory migration sweep ([MANAGER.3]); this is a migration
> rule, not custody. (4) Rows carrying the current service-owner schema
> tag that fail its field validation are disposable by the runtime
> pruning engine once older than its minimum age ([MF-5], [OBS.13.6]);
> rows with no recognized schema tag are preserved. Appending records for other TIDs (proactive
> supersession per [MA-3], operator replacement) is unaffected. The
> pruner's predicate for manager rows is: the owner is stale — a
> host-managed identity that is definitively stale, or an externally
> supervised handle whose registered probe reports `stale` — and the row
> is older than the pruner's minimum age —
> applied alike to `active`, `draining`, `stopped`, and `superseded` rows,
> including the newest row for every status; a row whose owner's liveness is `unknown` (an inconclusive or
> missing probe, or a host identity that cannot be evaluated) is pruned
> once it is older than both the minimum age and the external-supervisor
> staleness window, without a probe at prune time; a `draining` or
> `superseded` row whose owner is live is drain or supersession authority
> and is never pruned.
> Host-managed identity is tri-state: an observable identity with a
> different creation time, or a PID absent from a host-namespace reader,
> is definitively stale and is not probed; a PID absent from a
> containerized reader, or a recorded identity without a creation time,
> is `unknown`; a handle is live if any of its recorded processes is live.
> Readers and manager leadership checks omit unknown rows older than
> `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS` without keyed PING.
> Younger unknown rows keep the bounded keyed-PING rescue described above;
> both the definite-stale and expired-unknown pruning predicates override
> keep-newest for every manager status.

### [MF-5] ~:1372 — replace "foreground runtime pruning must preserve recent rows, malformed or unknown-shape rows, and rows whose live owner remains active or ambiguous under existing liveness rules"

> - foreground runtime pruning must preserve recent rows, unknown-shape
>   rows (no recognized Weft schema tag), and rows whose live owner
> remains active or ambiguous under existing liveness rules and within
> the runtime pruner's minimum age and the external-supervisor staleness
> window; a `weft.state.services` row that carries the current
> service-owner schema tag but fails that schema's field validation is
> disposable once older than the minimum age ([OBS.13.6]), and each such
> deletion is logged at error level when Weft logging is enabled

### [OBS.13.6] — extend the example list

> …such as `weft.log.tasks`, `weft.state.tid_mappings`, and
> schema-tagged `weft.state.services` rows that fail service-owner
> validation ([MF-5]).

### [CC-2.4.1] — append bullet

> - custody: registration is a single append whose returned message id
>   the task retains and exact-deletes in `unregister_endpoint_name()`;
>   registering while a claim is held is an error (a task whose append
>   failed holds no claim and may retry; register → unregister → register
>   is legal). Readers filter stale owners and never delete; the runtime
>   pruning engine is the only other deleter of `weft.state.endpoints`
>   rows.
>
> Also align the existing bullet "one live task currently owns at most
> one active named-endpoint claim at a time" to "…holds at most one
> claim, registered once per claim" so it no longer reads as permitting
> replace-by-re-registration.

### [CLI-1.2.3] — add

> A short TID that matches more than one full TID among the valid newest
> mapping rows in `weft.state.tid_mappings` (valid per the mapping-row shape rule — a JSON object whose `full` and `short` are non-empty strings, the shape `LivenessMonitor` enforces under [LIVENESS.R3] — and with a `full` value that is a 19-digit TID — rows whose
> `full` is not derivable are skipped, never fatal; live or terminal) is
> ambiguous: resolution fails with an
> error naming the candidate full TIDs; no command selects one silently,
> and every batch control path — the CLI loops and the Python client's
> `stop_many`/`kill_many` — resolves all requested TIDs before writing any
> control message. Resolution derives each row's short form from its
> `full` TID per [OBS.5]; the stored `short` field remains required
> row shape but is not resolution authority.

### [OBS.5] — append one sentence (derivation unchanged here)

> A short form matching more than one full TID is an ambiguity error per
> [CLI-1.2.3].

## 5. Tasks

1. **Independent review** of the plan and deltas (§8) — before promotion (runbook order: plan → delta review → promotion); the Codex and Claude rounds recorded below satisfy this for the current text, and any later revision re-enters it.
2. **Spec-promotion slice** (strategy A, no mapping claims); backlinks;
   promotion identifier. Verify `tests/specs/`.
3. **Characterization tests first (services).** Pin results of `weft
   status`, `weft manager list`, `ensure_manager`, `start_manager`, and
   leadership selection for registries containing a live manager, a
   dead-PID row, a superseded row, and an ambiguous row — results must be
   identical before and after; add assertions that the stale row still
   exists after reads and that `run_runtime_prune_for_context` removes it.
   Add the B1 interleaving regression: superseded row written between the
   incumbent's pre-write check and its heartbeat write → incumbent reaches
   superseded shutdown.
4. **Services custody — four sub-slices, each its own commit.**
   4a *reader custody*: remove the prune arm of
   `_manager_registry_disposition`/`_snapshot_registry`;
   `_mark_manager_stopped` appends only; remove peer-row deletes in
   `_prune_expired_manager_registry_entries` (own-`owner_tid` rows may
   stay) and `_active_dispatch_manager_records` (keep its local snapshot
   eviction — that is what prevents stale leadership selection); delete `_prune_managed_service_registry_history` and its call in `_observed_service_candidates_by_key` (the manager deletes no managed-service rows); leave `discard_v1_service_registry_rows` untouched; tests: after a managed child exits and the manager republishes `terminal`, both rows remain until the pruner runs; a manager restart with a live child does not respawn a duplicate (convergence never depended on the delete — expired rows are already filtered by `reduce_service_ownership`).
   Dispose of the existing *services* reader-deletion tests explicitly —
   the `_snapshot_registry` delete decision table and delete-failure
   tests in `tests/commands/test_manager_commands.py`, and in
   `tests/commands/test_run.py`:
   `test_select_active_manager_prunes_stale_record_without_pong` (~:3542),
   `test_list_manager_records_prunes_dead_active_and_preserves_stopped_history`
   (~:3772), `test_list_manager_records_prunes_host_pid_identity_mismatch`
   (~:3834) — each becomes a "row remains" custody test or is removed as
   an obsolete implementation test; list each by name in the commit.
   (Endpoint test dispositions belong to task 5, where the endpoint
   deletes are actually removed.) 4b *manager-row pruning*: make owner-stale-and-aged the **only** manager-row predicate in `_manager_candidates`: add it for non-`active` rows including the newest one, and **replace** the existing age-only `older_than_min_age_and_not_latest_for_manager_tid` arm (runtime.py ~:384–398), which today cross-deletes a live owner's heartbeat history — the owner prunes its own history (`_prune_older_self_registry_entries`); keep the `malformed_service_owner_row` arm under the [MF-5]/[OBS.13.6] delta;
   rewrite `test_manager_registry_prunes_expired_rows_on_refresh`; add a test that `--min-age 0 --apply` does **not** delete a fresh `superseded` row for a live incumbent, one that a `draining` row for a live owner survives, one that an `external-supervisor` row whose registered probe reports `stale` is pruned, one that an `unknown`-probe row older than both windows is pruned with no probe issued, one that the same row younger than either window survives, and — for malformed schema-tagged rows — one that the deletion logs at ERROR through `send_log` with `WEFT_LOGGING_ENABLED` set and emits nothing without it (caplog; `reload_config()` between the two cases). Assert that the enabled case actually creates an ERROR record with the queue/message-id/owner fields and returns successful deletion results. Add a real-broker partial-apply case: select two malformed candidates, exact-delete one before apply, then apply both; only the remaining row is reported newly deleted and logged once, with no log for the missing row and no cycle error. New pruning cases go in `tests/commands/test_runtime_prune.py` (existing owner of malformed-manager and retained-history cases), not only in manager lifecycle tests. In the same slice pass `ttl_ns=int(MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000)` in `_service_candidates`; test: an `active` managed-service row older than the TTL and `min_age` whose ids no manager holds is pruned, and one younger than the TTL is not. Pin the units explicitly with a 60-second-old active row and `--min-age 0 --apply`: the row survives the 300-second TTL even though it exceeds the pruning age; assert the broker still contains it. The default 3,600-second minimum age would hide a seconds/nanoseconds error.
   4c *host-identity tri-state*: implement the §4 reduction (observable
   mismatch → stale; `process_absent` → stale on a host reader, `unknown`
   on a containerized reader via `detect_container_runtime()`; missing
   `create_time` → unknown; any-live wins, else any-unknown, else stale)
   in **both** `manager_runtime._manager_record_stale_status` and
   `Manager._manager_record_liveness`, building on `inspect_host_process`
   plus the container check; keep the PING rescue for unknown; **remove** the age-only `unknown`→stale conversion for `external-supervisor` handles in `_manager_record_stale_status` (return `unknown`, per [MA-1]). Tests use controlled process-inspection results for the namespace cases.
   4d *append-only registration*: `_register_manager`'s replay structure and its post-write supersede re-check are unchanged; update `tests/core/test_manager.py:~8815 test_manager_active_heartbeat_race_preserves_superseded_record` to the append-only interleaving. Delete the private liveness copies in
   `manager_runtime.py` (repoint to helpers). Stop if a leadership test
   breaks in a way filtering cannot fix.
5. **Endpoints custody.** Red test: register an endpoint whose owner has
   not yet published a mapping row; resolve; assert the row still exists
   and is absent from live results (red today). Then: `register_endpoint_name`
   captures `write()`'s return (SimpleBroker 8 returns the id), stores it,
   raises on a second call; `unregister_endpoint_name` exact-deletes the
   stored id (delete the scan fallback and `find_endpoint_registry_message`
   if unused); `list_resolved_endpoints` stops deleting; delete the two
   dead selection guards. Rewrite `test_task_reregistration_replaces_prior_endpoint_claim`
   as "second registration is rejected"; dispose of the deletion-order,
   delete-failure, and pattern-scoped deletion tests in
   `tests/tasks/test_task_endpoints.py` here (custody test or removed;
   list by name). Persistent-after-one-item test from plan 1 stays green.
6. **Short-form owner (today's formula)** — lands before the fold work so task 7 references a real helper instead of duplicating the formula (Codex round 3). Add
   `weft/helpers/__init__.py::tid_short_form(tid)` implementing `tid[-10:]`
   for a 19-digit numeric string (raise otherwise); repoint every producer
   and the `liveness/host.py` title matcher listed in §3; delete every
   `[-10:]`/`[-TASKSPEC_TID_SHORT_LENGTH:]` slice (grep gate: zero in
   `weft/`); consumers of the stored `short` derive from `full`, skipping
   non-derivable values. Behavior-preserving. The derivation change is
   the separate short-TID derivation plan.
7. **Short-TID and folds — three sub-slices** (requires task 6's helper). 7a *canonical fold*: add a
   tuple-returning fold in `endpoints.py` that adopts exactly
   `decode_tid_mapping_row` validity and keep the payload-map function as
   a thin view over it (its three consumers — endpoint liveness, streaming
   pruning, `Manager._observe_admission_usage` — are unchanged); repoint
   `mapping_for_tid`, `_read_tid_mappings`, system `_latest_tid_mapping_entries`,
   `_latest_tid_runtime_handle`; **delete** `_latest_tid_runtime_handles`
   (zero callers). `_lookup_manager_pid` today means "newest row with a
   live host PID", not "newest row" — repointing changes
   `stop_manager --force` when the newest row's handle has no live PID
   but an older row does; adopt newest-row semantics per [OBS.6] and
   record it as an accepted change (after plan 1 every row carries the
   manager's own PID, so the live case still resolves). **Every** current-state mapping consumer is repointed to the canonical fold, not only the four named above (Codex round 3 inventory, verified): `TaskMonitor._nonterminal_mapping_row_tids` (task_monitor.py ~:3473 — adopts any dict with a non-empty `full`, so a newer malformed row with `terminal: true` and no `short` strips [OBS.13.7] destruction protection from a TID whose newest *valid* row is non-terminal: red test — older valid non-terminal row plus that malformed row → protection must remain and the family must not be disposed), `weft/core/heartbeat.py::_heartbeat_runtime_handle_is_live` (~:92, duplicate-startup decision), `Manager._managed_pids_for_child` (~:6402, force-kill PID source), and `commands/tasks.py::_read_tid_mapping_entries` (:107, feeds `task_tid --pid`); the residual private folds in `_latest_tid_mapping_entries`/`_read_tid_mappings` go with them (grep gate: no direct `WEFT_TID_MAPPINGS_QUEUE` newest-row reduction outside `endpoints.py` and the reaper). Ambiguity rule: `resolve_full_tid` raises
   `CommandUsageError` (exists in `weft/_exceptions.py`; `cli/app.py::task_tid`
   already maps it) naming candidates when more than one full TID
   matches; apply the same rule in `weft/commands/system.py::_resolve_tid_filters`
   (~:359–376, `weft status --tid`); in the `stop_tasks`/`kill_tasks`
   loops (`commands/tasks.py` ~:1654 and the other `resolve_full_tid(...)
   or tid.strip().lstrip("T")` sites — ten in the file) resolve **all**
   TIDs before any control write so a collision mid-batch cannot abort
   after STOPs were sent — **and** in `commands/tasks.py::_task_control_result`,
   the path the Python client's `stop_many`/`kill_many` actually use,
   add a resolution-only first pass with a firing client-batch test
   (`stop_many([valid_full, ambiguous_short])` sends nothing). Batch semantics stated precisely: an ambiguous short is batch-fatal — `CommandUsageError` raised before any control write; every other per-item failure (unknown TID, terminal task, control error) keeps the [PY-2] `accepted`/`failures` partition exactly as today. No [PY-2] change. 7b
   *collision resolution* is that error plus these preflights; 7c
   `_lookup_manager_pid` newest-row semantics **plus a code change in
   `stop_manager`** (~:1846): when `--force` finds no controllable PID it
   must return an unconfirmed/failure result instead of the current
   `return True, None` fall-through, with a regression proving it does
   not report success while a manager remains alive. Resolution skips
   non-derivable `full` values. Differential fixture: republished rows,
   terminal-after-active rows, short collision, malformed row,
   empty-string TID. Verify the reaper's `_reconcile_mapping_rows` agrees
   with the canonical fold on malformed/newest handling — expect this
   STOP to fire: the endpoints fold adopts any dict with a non-empty
   `full` while the reaper's `decode_tid_mapping_row` rejects
   `invalid_tid_mapping_shape`; check whether `strict=True` closes the
   gap before repointing; if not, report.
8. **Custody proof and traceability reconciliation** (renumbered: the
   behavioral custody tests formerly task 7 land here alongside the
   mapping claims, backlinks, deviation-log close, and gate rerun).

## 6. Testing Plan

`WeftTestHarness` for CLI-shaped paths, `broker_env` for registry
contents. Real broker queues and real public selection paths; narrow
fakes are permitted only for clocks, process-inspection results (the
namespace/identity cells), and the exact write interleaving the B1 test
needs — never for queue semantics or selection logic. Regression names: "readers never
delete", "replace converges under the B1 interleaving", "startup-window
endpoint row survives resolution", "short-TID collision errors".

## 7. Verification and Gates

Per task: `tests/core/test_manager.py`, `tests/commands/test_run.py`,
`tests/commands/test_manager_commands.py`, `tests/tasks/test_task_endpoints.py`,
`tests/commands/test_task_commands.py`, `tests/commands/test_task_snapshot_reducer.py`,
`tests/commands/test_task_evidence.py`, `tests/cli/test_cli_system.py`,
`tests/commands/test_runtime_prune.py` (the short-form owner touches 17 test files in all — listed in the
derivation plan). Final: full suite + mypy + ruff.
Rollback: revertable; no persisted-format change (a spec carrying
nothing new).

## 8. Independent Review Loop

Different agent family. Read the deltas, [MA-3], and the cited symbols.
Stance: for each removed reader-side delete, construct the scenario where
it was the only thing preventing wrong selection or stuck startup
(`ensure_manager`/`start_manager` proof loops); confirm the B1 re-check is
retained; check the ambiguity-error wording is implementable.

## 9. Out of Scope

Admission-control behavior; pruning-engine predicates other than the two added in task 4b (non-`active` manager rows; managed-service TTL expiry); tid_mappings custody
(owned by LivenessMonitor, landed); `weft.state.streaming` / `pipelines`
groups.

## 10. Fresh-Eyes Review

Author pass 2026-08-31: replaced "newest-wins" with the ambiguity error
after the reviewer showed the cited error did not exist and `kill`
safety argued for failing loudly; scoped the [MA-1] rule to deletion so
[MA-3] appends stay legal; replaced the AST custody test with behavioral
proof; made the plan-1 dependency explicit.

## Review Record (append-only)

**2026-08-31 — independent pre-promotion review of the deltas (Claude-family subagent; the cross-family review §8 asks for has NOT yet been run).** The core
claim held: no removed reader-side delete is load-bearing for selection
or startup proof (the "omit" disposition already exists; `start_manager`,
`_await_manager_start_settlement`, and `ensure_manager`'s uncertain path
read only included rows; `_mark_manager_stopped` append-only is safe
because disposition applies only to `active` rows and ids are
monotonic). Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — terminal manager rows would have no deleter (pruner protects the newest row per TID unconditionally and classifies only stale `active` rows) | Applied: terminal-row predicate added to `_manager_candidates`, in scope; test :~6142 named for rewrite; [MA-1] delta names the pruner's two classes |
| S1 — "rows it wrote" contradicts retained owner-TID deletes and [MA-3] | Applied: delta reworded to `owner_tid` |
| S2 — every crashed host-pid row pays the 0.5 s PING per read for 1–2 h | Applied: `create_time`-mismatch is definitively stale (in scope, [LIVENESS.R5]); numbers recorded |
| S3 — ambiguity rule sites and wording | Applied: `_resolve_tid_filters`, resolve-all-before-control-writes, "any row live or terminal", [OBS.5] cross-reference only |
| S4 — one-shot error condition underspecified | Applied: "while a claim is held"; retry after failed append and re-register after unregister are legal; no `None`-fallback scan |
| S5 — "four replays to one" ambiguous; B1 test exists at :~8815 | Applied: one pre-write + one post-write scan; test updated, not added |
| S6 — `_lookup_manager_pid` semantics differ; `_latest_tid_runtime_handles` has zero callers | Applied: newest-row semantics adopted and recorded; dead fold deleted |
| Notes — [CC-2.4.1] "at a time" wording; task-6 STOP gate will likely fire (fold vs reaper malformed handling) | Applied |

**2026-08-31 — owner decision (Van): short TIDs get both the loud
ambiguity error and a folded derivation** `(microseconds + counter ×
10⁷) mod 10¹⁰`, added as task 7 with the [OBS.5] delta. Verified before
adding: no short-form helper exists (every site slices digits); the
reaper's `valid_tid_mapping_payload` checks only that `short` is a
non-empty string, so the format change does not touch mapping-row
validity; the stored `short` becomes display-only and resolution derives
from `full`.

**2026-08-31 — Codex (cross-model) pre-promotion review.** Verdict: not
promotable as first written; every reader-side delete confirmed
non-load-bearing for selection and startup proof; `_mark_manager_stopped`
append-only judged *safer* than today. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — [OBS.5] used the wrong timestamp model (`tid >> 12` is a 4,096 ns grain, not µs; `× 10⁷` fold made counters 0 and 1000 collide) | Applied: §1, [OBS.5] delta, task 7 rewritten with the verified encoder; fold constant `⌊10¹⁰/4096⌋` keeps all 4,096 counters distinct; red tests corrected |
| B2 — custody rule contradicted `discard_v1_service_registry_rows` (spec-mandated reader-side migration delete) and managed-service history rows (`owner_tid` = child TID, no author field) | Applied: three custody classes in the [MA-1] delta; managed-service deletion by retained exact ids; v1 sweep an explicit exception |
| B3 — boolean "no match" conflated identity mismatch with unobservable namespace; would start duplicate managers from containers; only one of two sites was changed | Applied: tri-state rule in the delta; both `_manager_record_stale_status` and `_manager_record_liveness` in scope |
| B4 — batch atomicity missed `_task_control_result` (the client path) | Applied: resolution-only first pass + client-batch test |
| B5 — canonical fold validity unresolved (the predicted STOP fires) | Applied: adopt `decode_tid_mapping_row` validity; "any valid newest row"; tuple fold + payload-map view |
| B6 — terminal pruning omitted `draining` and did not define the keep-recent override | Applied |
| S — inventory (`_task_snapshot_reducer`, `commands/task_monitor`, `liveness/host.py` title matcher, `core/pipelines.py` default names, `cli/app.py`); tuple exposure not one-line; existing reader-deletion tests need dispositions; split tasks 4 and 6; compatibility wording (all displayed shorts change); "five spec files"; `_constants.py` docstring | All applied |

**2026-08-31 — Codex (cross-model) second pre-promotion review, on the
revised plan.** Verdict: still blocked; every reader-side delete again
confirmed non-load-bearing. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — min-age-only terminal pruning could delete a fresh `superseded` row (reversing `--replace`) or a live `draining` row | Applied: pruner predicate = owner definitively stale **and** aged, for all statuses; live/unknown owners never pruned; tests for `--min-age 0` and `draining` |
| B2 — "valid mapping row" admits non-19-digit `full`; a raising helper would deny all short-TID commands | Applied: non-derivable `full` is skipped, never fatal; helper contract stated |
| B3 — `inspect_host_process` cannot distinguish dead from invisible-namespace | Applied: explicit reduction incl. `detect_container_runtime()`, multi-process combination, missing `create_time` |
| B4 — custody delta contradicted itself on manager-row deletion authority | Applied: owner may exact-delete; pruner only under the named predicate; operator-written `superseded` rows owned by the incumbent |
| S1 — managed-service exact-id custody had no storage contract | Applied: owner-local id store, lifecycle, restart loss accepted, tests named |
| S2 — endpoint test dispositions in the wrong task | Applied: moved to task 5 |
| S3 — failing-test inventory incomplete; 17 short-form test files; glob mismatch | Applied |
| S4 — `stop_manager --force` fall-through `return True, None` | Applied: code change in 6c |
| S5 — zero-padding | Moved with the derivation to the short-TID derivation plan |
| S6 — "nothing mocked" too absolute | Applied: narrow fakes permitted for clocks, process inspection, interleaving |
| Notes — "four spec files"; "every displayed short changes" overstated; **split the folded derivation into its own Class-5 plan** | Applied: derivation moved to `2026-08-31-short-tid-derivation-plan.md`; this plan introduces the owner helper with today's formula |

**2026-09-04 — retirement-rule reconciliation (author pass, every path
verified in code).** Rule: every record retirable under today's code must
have a named rule after the plans. Twenty-two paths keep one; three did
not, all here. Dispositions:

| Finding | Disposition |
|---------|-------------|
| `external-supervisor` manager rows never retired: the plan said "definitively stale", but `_manager_record_stale_status` reports their staleness by age with `definitive=False`; today the peer TTL sweep this plan removes deletes them | Applied: predicate reworded to the boolean of `manager_registry_record_is_stale` with both branches named; [MA-1] delta updated; 4b test added |
| managed-service rows with lost custody (restart, `--replace`) had no deleter under the retained-id store: the pruner's `_service_candidates` never expires and protects every newest live-status row; today the manager TTL-expires them each convergence | Applied: id store **not built**; `_prune_managed_service_registry_history` deleted outright; pruner passes the reader TTL (one argument) — one deleter, the predicate readers already apply, no new state |
| `unknown` owners have no time bound (the mapping reaper has one) | Recorded as an open owner decision in §4; not implemented |

**2026-09-04 — Codex (cross-model) third pre-promotion review, on the
reconciled plan.** Verdict: still blocked on four contract points; every
deletion again confirmed non-load-bearing (`_prune_managed_service_registry_history`
explicitly: `reduce_service_ownership` already TTL-filters the same rows).
Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — the reconciliation text made an aged unprobeable `external-supervisor` row both prunable (age branch) and never-prunable (`unknown`); [MA-1] ~:167 defines missing/inconclusive probes as `unknown` | Applied: age→stale conversion removed in 4c; one bounded `unknown` rule (aged past both windows **and** unanswered keyed PING at prune time), replacing the earlier open decision; [MA-1] delta rewritten; **owner-confirm** |
| B2 — mapping-fold inventory missed `_nonterminal_mapping_row_tids`, `_heartbeat_runtime_handle_is_live`, `_managed_pids_for_child`, `task_tid --pid`; the first strips [OBS.13.7] protection on a malformed newer row | Applied: all four repointed in 7a with the red test; grep gate added |
| B3 — `short` "display-only" contradicts `decode_tid_mapping_row` validity; "mapping-row shape rule" undefined | Applied: shape defined inline in the [CLI-1.2.3] delta; "required row shape, not resolution authority" |
| B4 — pruner deletes malformed v2 service rows while [MF-5] ~:1372 says preserve malformed rows; the plan named no such predicate | Applied: custody class (iv); [MF-5] and [OBS.13.6] deltas authorize schema-tagged-but-invalid rows behind `min_age`; behavior-preserving; **owner-confirm** |
| S — age-only superseded-manager arm left intact; helper after its consumers; [PY-2] batch semantics; `tests/commands/test_runtime_prune.py` missing; "four files"; 4d replay optimization unrelated risk | All applied: arm replaced; tasks 6/7 swapped; batch-fatal only for ambiguity; test file added; five files; 4d reduced to the test update and copy deletion |

**2026-09-04 — owner decisions (Van) closing the two round-3 open
items.** (1) "Unknown owner past all timeouts is pruned": the
PING-at-prune-time guard is dropped; `min_age` and the external-supervisor
window are the timeouts. (2) "Malformed rows should be pruned (but if
logging is on, logged as an error)": the [MF-5]/[OBS.13.6] deltas stand;
the pruner logs each malformed deletion at ERROR through the
`send_log` facade, which plan 5 keeps (Van; it implements the documented
`WEFT_LOGGING_ENABLED`/`WEFT_DEBUG` switches — `weft.helpers` itself is
private per [PY-1]). Rows with no recognized
schema tag remain preserved (stated assumption: a future schema is not
malformed).


**2026-09-07 — review corrections (plan text only).** Governing code/spec
baseline remains `bcea628e`; these dispositions do not claim implementation
or a final independent review pass.

| Finding | Disposition and review evidence |
|---------|---------------------------------|
| Service TTL passed seconds to a nanosecond parameter | Corrected both instructions to `int(MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000)`, matching `Manager._manager_registry_retention_ns`. A review probe using the real `plan_service_owner_history_prune` selected a 60-second-old active row with `ttl_ns=300.0` and preserved it with `ttl_ns=300_000_000_000`; task 4b now tests that row with `--min-age 0 --apply`, where the default minimum-age protection cannot mask the defect. |
| Malformed-row log call passed unsupported logger keywords; partial batch results could hide actual deletions | The review probe raised `TypeError: Logger._log() got an unexpected keyword argument 'queue'` with logging enabled. Fields now use `extra`; the existing apply helper's `exact_status` branch confirms each deletion for passes containing malformed candidates. A second real-broker review probe deleted one of two candidates before apply: `exact_status=True` returned `(False, None)` for the missing row and `(True, None)` for the remaining row, and the corrected facade call emitted one ERROR with the actual deleted id. Task 4b requires an actual ERROR record and a mixed present/missing real-broker apply test. This corrects the mechanism for the owner's existing logging decision. |
| “32× rarer” confused birthday capacity with per-pair probability | Corrected to 1,024× the counter-zero residue space and approximately 32× the population at equal birthday-collision probability, explicitly conditioned on independent uniform sampling. The derivation plan records the temporal period and probability assumptions. |

## Implementation Record (2026-09-07)

Class 5, hardened. Baseline: plan 3's reviewed worktree atop `0b20d4d1`;
plan 3's full gate runs in the main worktree while this plan is isolated.
Commits remain ordered, one per plan; the user's instruction supersedes the
internal sub-slice commit suggestions. Same-family independent pre-promotion
review passed after these accepted corrections:

- Strict short-form helper input is a 19-digit numeric full TID. Durable
  task-log/status folds skip malformed IDs and continue valid neighbors.
  Host title inspection with a malformed expected TID preserves exact
  identity evidence and reports title-unconfirmed; it does not raise. Public
  invalid selectors retain explicit usage errors.
- Stale-and-aged and unknown-past-both-windows predicates apply to all
  manager statuses, including every newest row. Both override keep-newest.
- MA-1 explicitly states reader behavior: omit expired unknown rows without
  keyed PING, preserve bounded rescue for younger unknown rows. Tests pin
  both sides of the reader and pruning windows.
- Preserve exact malformed deletion accounting/logging, TTL nanosecond
  units, the v1 migration sweep, Manager's post-write supersession recheck,
  and all non-ambiguity batch per-item error semantics.

Strategy A promotion follows this review, before implementation. Root owns
specs, short-form helper, commands and residual mapping consumers. Services,
pruning, and endpoint slices have disjoint file owners and agreed shared
interfaces. Full tests including slow tests, Ruff, mypy and independent
completed-work review remain commit gates.

Promotion baseline: governing specs from the plan 3 reviewed worktree plus
the promoted MF-3.1/MF-5, MA-1, CC-2.4.1, CLI-1.2.3 and OBS.5/OBS.13.6
text in this worktree. No implementation mapping claim is promoted yet.

Endpoint custody clarification from implementation review: failed unregister
retains its existing message id/name so another claim cannot be appended while
the old one remains held. Successful or confirmed-absent exact deletion
releases custody; a later independent unregister may retry the same exact id.
No new tracking state or scan fallback. Regression covers failure, rejected
reregistration, successful retry and subsequent legal registration.

Implementation review clarified missing runtime identity: absent/invalid handles
and empty scoped host-process sets yield unknown, not vacuous stale evidence.
This protects both reader selection and pruning with `min_age=0`. Stop
confirmation now preserves the unfiltered target evidence and requires actual
exit/stale evidence when reader filtering omits the row; missing PID alone
is insufficient. Existing terminal and foreground-serve proofs are retained.
These rules are mapped beside MA-3 and covered by exact evidence cells.

Plan 3 committed as `f3ad6741` after 4,439 tests passed; plan 4 remains
isolated until its source and focused verification are frozen.

Completed-work review found and fixed three additional boundaries: bounded
spawn reconciliation validates mapping shape via the canonical predicate;
full-TID filters retain only the full selector so a collision cannot admit
a neighbor; reverse lookup rejects Unicode digits before strict derivation.
The bounded existence scan remains bounded because it proves existence, not
current owner selection. All other current-owner mapping readers use the
newest-valid fold. No raw digit slicing remains in production short producers.

The real Monitor-store malformed-newest regression fails against plan 3's
method (one summary emitted instead of zero) and passes against the canonical
fold. Its fixture now supplies nonterminal row-presence evidence without the
pytest process PID: the test harness's older cleanup fold otherwise selects
the fixture TID after seeing the malformed row and the canonical kill path
then targets the test process. TaskMonitor deliberately does not probe PIDs.

Focused verification: 26 root short/fold/Monitor tests passed; full-marker
manager, run, and manager-command modules passed with three PostgreSQL skips.
Endpoint and pruning implementations passed independent same-family review,
including the all-status age matrix, exact malformed-deletion logging, TTL
units, exact append-ID custody, failed-unregister retry, and no reader deletes.
Services review passed, including the later startup and stopped-row corrections.
Suppression inventory: 219 groups, 353 directives; C901 134 and SIM102 3.
SUP006, SUP016, SUP033 retired; SUP241 retains its one terminal-stop guard.
Final root full suite, Ruff, mypy and metadata gates follow.

Final root verification: 4,542 passed, 16 skipped in the full suite including
slow tests (`pytest -m '' -n 2`). The skips are 11 opt-in live-provider tests
and five PostgreSQL-only cases under the SQLite backend. Ruff, full mypy
(192 source files), and suppression reconciliation passed.

The first full run found 13 fixtures that expected reader deletion, treated
missing identity as stale, supplied incomplete mapping rows, or omitted exact
host create-time/PONG evidence. They now assert the promoted custody contract:
retained stale history, unconfirmed force-stop failure, canonical mapping
shape, and positive selection evidence. All 13 focused regressions passed
before the clean full rerun. No source was edited during the final full gate.
