# Dead Generation Retirement Plan

Status: completed
Source specs: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]; docs/specifications/07-System_Invariants.md [OBS.13], [LIVENESS.R5]; docs/specifications/00-Quick_Reference.md (queue table)
Superseded by: none

Class: 5 — solely because one sub-slice corrects normative text in
00-Quick_Reference (the manager control-queue rows, strategy D); every
other sub-slice is a zero-production-caller deletion or a
behavior-preserving consolidation (Class 3 if split off). Hardening
**applies** to 5.7 (BaseTask construction) and 5.8 (`TaskRunner`, a public
Protocol's implementations across three backends, and liveness evidence
that can authorize retirement); the other sub-slices carry
`hardening: N/A — no risky trigger`. Plan type: implementation with
clarification-only spec deltas (Quick Reference correction plus mapping
corrections in 10 and 03).
Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

Retire the code that migrations left behind. Every symbol below was
verified on 2026-08-31 to have zero production callers (grep across
`weft/`, `bin/`, `extensions/`, `integrations/`), or to be a duplicate of a
live owner. Each sub-slice re-runs the grep before deleting and records it
in the commit message. Behavioral assertions in retired tests are
preserved by repointing; rendering assertions move to CLI adapter tests.

## 2. Source Documents

- 14 [PY-1] (`weft.core.*` is private; leaf command helpers are private),
  [PY-2] (typed commands surface is the supported generation; "An empty
  selection is a successful zero-count outcome" — so `stop_many`/`kill_many`
  are **not** changed).
- 02 [TS-1] (templates may carry empty IO), [TS-1.3] (runner selection;
  `validate_runner_capabilities` is the capability gate).
- 07 [OBS.13] ("Dead-TID cleanup … must not … trigger raw task-log
  coalescing"; "must not mark Monitor collation families").
- 00-Quick_Reference queue table lines ~36–37 (`weft.manager.ctrl_in`,
  `weft.manager.ctrl_out`) and `CLAUDE.md` §2 queue-naming block.

## 3. Sub-slices (each independently landable; files verified)

**5.1 Legacy tuple-returning commands — one commit per module.**
`weft/commands/system.py::_legacy_cmd_status` (:1646–1724),
`_watch_task_events` (:1569–1643) and renderers reachable only from them
(:286, :1439, :1464, :1551); `weft/commands/result.py::_legacy_cmd_result`
(:946–1035), `_collect_all_results` (:441–491), `_result_request_error`,
`_claimed_result_response`, `_single_result_response` (:872–935);
`weft/commands/prune.py::cmd_prune` (:88–187), `render_runtime_prune_human`,
`render_retention_prune_human` (:434–500), `_runtime_prune_command`,
`_retention_prune_command` (:527–601); `weft/commands/manager.py::start_command`,
`stop_command` (:312–366); `weft/commands/serve.py::serve_command` (:27–49);
`weft/commands/load.py::cmd_load` (:555–583). Tests pinning them:
`tests/commands/test_status.py:33` (aliases `_legacy_cmd_status as
cmd_status`), `tests/commands/test_result.py:36`,
`tests/commands/test_task_evidence.py` (four sites). Rule: semantic
assertions → the typed `cmd_*` test; exact text/JSON assertions →
`tests/cli/`. **Stop** if a legacy test asserts behavior the typed path
lacks — that is a gap in the typed surface; report before deleting.
**That stop-gate has already fired once (Codex review):** [CLI-1.2.1]
requires broker-backed identifiers in status JSON to be [SB-0.2]
*strings*; the legacy `_render_json_payload`/`_task_snapshot_to_json_dict`
projection implements that, while the live adapter
`app.py::_render_status_snapshot` uses generic `_jsonable` and emits
integers — shipped `weft status --json` is out of conformance. Fix: move
the string projection into the CLI adapter and repoint
`test_status_json_projects_only_owned_broker_identity_fields` at it
**before** deleting the legacy renderer (it is the only complete
reference implementation). Serve custody: retain `cmd_manager_serve` as
the owner, delete `serve_command`, and repoint the client seam
`manager.py::serve_manager` — implement 5.1 and 5.2 for serve in that
order, not the reverse.

**5.2 Client/CLI forked bodies.** `weft/commands/tidy.py` (:19–51: three
entry points for one `vacuum(compact=True)`), `dump.py::dump_system`
(:97–156, re-derives the default path and converts typed errors to
`RuntimeError`), `serve.py::cmd_manager_serve` (:27–85 duplicates
`serve_command`), `commands/manager.py::cmd_manager_stop` (:187–263
duplicates `stop_manager`). Shape to copy: `load.py::load_system`
(:634–646) — a thin call into the typed function. **Preserve client return
types and absent-manager semantics** — `cmd_manager_stop` is *not* a thin
equivalent of `stop_manager`: it returns `None` when no manager is active
and reloads or synthesizes the terminal `ManagerSnapshot` [PY-2] requires,
while `stop_manager` raises when absent. Specify the sequence: preselect
and return `None` if absent; invoke the shared stop; reload or synthesize
the terminal snapshot. Test custody: `tests/helpers/weft_harness.py:1026`
calls `stop_command(..., stop_if_absent=True)`; the typed facade has no
such option — repoint the harness to `manager_runtime.stop_manager(...,
stop_if_absent=True)` directly. Affected-test list also includes
`test_manager_commands.py`, `test_serve.py`, `test_dump_load*.py`, and
the prune tests. `client/_namespaces.py::stop_many/kill_many`
(:134–169) are **unchanged** ([PY-2]); optionally rename
`tasks._task_control_result` to a public name — it is the
spec-implementing seam and the `cmd_task_*` facades cannot accept a TID
sequence or a live context.

**5.3 Dead validate module.** `weft/cli/validate_taskspec.py::cmd_validate_taskspec`,
`_resolve_taskspec_source` (:40–119; zero callers). Move the render
helpers the live `app.py:1056–1103` imports next to their caller; delete
the rest. Do **not** port the dead stored-name resolution into the live
file-only command (unspecced behavior).

**5.4 Dead helpers.** `weft/helpers/__init__.py`: `format_tid`/`parse_tid` (:677–713), `resolve_cli_command` (:214) + `CommandNotFoundError` (:59). **The logging facade is kept** (Van, 2026-09-04 — "retire the logging-gated helper? Why?"): `send_log`, `debug_print`, `log_*`, `is_logging_enabled`, `is_debug_enabled` (:533–731), together with `_config` (:50) and `reload_config` (:951) that back them. Zero in-repo callers was the only evidence for deleting them (`weft.helpers` is private per [PY-1], so this is not a public-surface argument); the facade is the in-Weft implementation of two documented switches — `WEFT_LOGGING_ENABLED` ("Enable Weft logging output", Quick Reference ~:165) and `WEFT_DEBUG` ("Enable debug output in Weft helpers and CLI surfaces", ~:164) — which would otherwise become SimpleBroker pass-throughs with no reader inside Weft. Plan 4 gives `send_log` a production caller (the runtime pruner's malformed-row error log). **The
zero-caller claim was wrong for one symbol** (Codex): `write_file_atomically`
calls `log_debug(...)` on its success path (~:795), and it is live via
`write_json_atomically` → `weft/context.py` and provider settings —
deleting the name would raise `NameError` *after* the file was replaced.
Keep that call through `log_debug`: with the facade retained, replacing
it with `logger.debug` serves no deletion and bypasses the
`WEFT_LOGGING_ENABLED` gate in `send_log`. Keep the
`WEFT_DEBUG`/`WEFT_LOGGING_ENABLED` env keys (forwarded to SimpleBroker,
`_constants.py:2397–2398`).

**5.5 Dead monitor pipeline.** `task_monitor.py::_coalesce_and_delete_dead_task_log_rows_for_tids`
(:4800–4924, zero callers), `_delete_monitor_store_task_log_rows_for_tids`
(:4926–4963), `store.py::list_deletable_task_log_messages_for_tids`
(:2968–2985, :2132–2161) + its `sql.py` builder, result fields
`dead_tid_log_refs_selected`/`dead_tid_log_rows_deleted`
(`policies/runtime_control.py:64–65, 110–111`; test
`tests/tasks/test_task_monitor.py:5082–5083` asserts them 0 — delete the
assertion), `store.list_unemitted_terminal_tasks` (:2666) + `sql.py:632`,
hardcoded-zero `dead_tid_control_rows_estimated_deleted` (:3368). [OBS.13]
already forbids what the chain would do. (The test-only reserved scan
engine goes with plan 6.)

**5.6 Dead configuration and vocabulary — split into four commits:
queue-contract correction, diagnostics-field ownership, terminal
vocabulary, inert constants.**
- `yield_strategy` on `MultiQueueWatcher` — **kept** (Van, 2026-08-31):
  in-repo it is stored and never read, but external embedders use the
  watcher. Not deleted; its ledger entry stays.
- `DEFAULT_CPU_PERCENT`, `DEFAULT_MAX_FDS`, `DEFAULT_MAX_CONNECTIONS`
  (:891–898), `STATUS_RUNNING`/`STATUS_FAILED`/`STATUS_CANCELLED`
  (:1739–1749) — delete.
- `RUNNER_DIAGNOSTICS_FIELD` (:729) — **repoint** the hardcoded
  `"runner_diagnostics"` in `runner_diagnostics.py:202`,
  `task_evidence.py:326`, `tasks/consumer.py:996`, `monitor/collation.py:255`
  so the constant becomes the owner.
- `DEFAULT_FUNCTION_TARGET` (:84) — keep; docstring: "internal service
  TaskSpec placeholder satisfying `SpecSection`; never dereferenced
  (services dispatch by `INTERNAL_RUNTIME_TASK_CLASS_KEY`)". Do not
  invent a `weft.tasks` module.
- Terminal vocabulary: `TERMINAL_TASK_LIFECYCLE_STATUS_VALUES` (:116)
  becomes `= TERMINAL_TASK_STATUSES` (:1754) or its one consumer
  (`core/task_lifecycle.py`) is repointed; inline literals at
  `base.py:2068`, `:2102`, `interactive.py:405–411`, `taskspec/model.py:1252`
  replaced with the constant. Grep gate: zero inline five-status sets.
- `WEFT_MANAGER_CTRL_IN_QUEUE`/`WEFT_MANAGER_CTRL_OUT_QUEUE` (:1610–1613)
  — zero consumers outside `_constants.py` (verified across `weft/`,
  `extensions/`, `integrations/`); re-verify including dump fixtures, then
  delete; update `tests/core/test_manager.py` registry-record fixtures;
  land the Quick Reference + CLAUDE.md §2 correction (§4c). Generic
  record-field compatibility at `manager_runtime.py:828–842` is untouched.
- `tests/system/test_constants.py` updated per removal — it pins the
  inventory and is the done signal.

**5.7 TaskSpec boundary re-validation.** `taskspec/model.py::_validate_strict_requirements`
(:1459–1535): only :1500–1511 (outbox/ctrl_in/ctrl_out presence for a
resolved spec with empty IO) is reachable; every other branch is
precluded by field declarations. Reduce to that check, renamed
`_validate_runtime_ready_io`, still invoked from `BaseTask.__init__`
(:247) — **not** moved into Pydantic, because [TS-1] templates legitimately
carry empty IO. Delete `validate_required_elements` (:1355–1365, no-op)
and the `metadata is None` guards (:1782–1795). Test: a resolved spec
with empty IO is rejected at `BaseTask` construction with the same
message; a template with empty IO still validates.

**5.8 Plugin re-validation — split per runner, hardening applies.**
Docker `plugin.py:129–157` and macOS `plugin.py:62–94` runner `__init__`
option re-checks → a shared parse function called by both
`validate_taskspec` and `create_runner` (the microsandbox
`_options.py::parse_options` shape); fix the drifted docker image/build
message by having one source. **Capability re-checks inside
`create_runner` are KEPT** (reversal per Codex): `weft.ext.RunnerPlugin`
is public under [PY-1] and neither the Protocol nor [TS-1.3]/[CC-3.3]
states a prior-validation precondition, so an embedder calling
`create_runner(..., persistent=True)` directly is rejected today and
would construct an unsupported backend after deletion. A docstring is
not a normative replacement, and adding a public precondition is a
[PY-1]/[CC-3.3] contract change this plan does not make. Remove only the
duplicated *option* re-validation. (The microsandbox exemplar's own
capability re-check at `_options.py:132–135` therefore also stays.)
`_liveness_probe_registered` globals (macos :342, :350–358; micro :552,
:560–568; docker :716, :724–729) deleted — registration is
replace-idempotent. Strict `handle.runner != <name>` probe guards
(macOS :368–369, microsandbox :575–576) removed: the governing text is
[CC-2.3], which explicitly permits alias handles to select a probe
through `observations.liveness_provider`, and `registry.py::liveness_provider_key`
implements it. After removal the probe trusts the registry's routing and
returns `"unknown"` only when the handle's evidence is unreadable — do
**not** describe this as "the Docker shape" (Docker adds container
heuristics unrelated to routing trust). Required tests per plugin:
alias-routed handle (foreign `runner`, matching `liveness_provider`)
yields a real `live`/`stale` verdict; malformed evidence yields
`"unknown"`. **Keep** the two-call preflight (`runner_validation.py:55–65`)
— the Protocol does not promise the superset property. Extension tests
show each rejection still fires through the real validate→create sequence.

## 4. Invariants and Constraints

- No public CLI shape change; no `client` return-type change; [PY-2]
  empty-selection semantics unchanged. Exception types from
  `tidy_system`/`dump_system` become `CommandExecutionError` (a
  `RuntimeError` per 14:~180, "translated at the command seam") — spec-
  aligned; release-note it.
- No new abstraction (explicitly: no shared registration-guard helper; no
  `_require_mapping` consolidation — trivial, left alone).
- Every deletion commit message carries the pre-deletion zero-caller grep.
- **External embedding caution (Van, 2026-08-31):** `weft/core/tasks/`
  surfaces are embedded by other projects even where [PY-1] calls them
  private (`MultiQueueWatcher.add_queue`/`remove_queue` is kept for that
  reason). Before deleting anything under `weft/core/tasks/` — here,
  `yield_strategy` on `MultiQueueWatcher` — confirm with the owner that
  no embedding project passes it. That item is **owner-confirm** before
  deletion.
- Ruff suppression registry gate (review S2): `bin/ruff_suppression_index.py --check`
  runs in CI and `tests/specs/test_ruff_policy.py` enforces the registry.
  Deleted code carries directives whose rows must be retired and the
  index regenerated (`--write`) in the same commit: RUFF-SUP-054
  (`_validate_strict_requirements`), 058 (`_coalesce_and_delete_dead_task_log_rows_for_tids`),
  119/336 (`_watch_task_events`), 309 (`_collect_all_results`), 337
  (`_legacy_cmd_status`), 351 (`_resolve_taskspec_source`), 353
  (`_legacy_cmd_result`); possibly 245 and 250 if those lines are
  rewritten.
- PONG summary shape (review S6): `to_summary()` in
  `policies/runtime_control.py` (~:88–111) emits the always-zero dead-TID
  keys into the cached `weft task ping` extended block; no spec
  enumerates them, so removal is allowed — state the shape change in the
  commit.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 14, 02, 07, 00-Quick_Reference at plan authoring time
  (2026-08-31).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/00-Quick_Reference.md | D — correction of shipped behavior | queue table rows ~36–37 |
| docs/specifications/10-CLI_Interface.md | D — mapping corrections, exact after-text per section | [CLI-0] ~:15 (drop `weft/cli/validate_taskspec.py` from the module list); [CLI-1.1.2] ~:297 `serve_command()` → `weft/commands/serve.py::cmd_manager_serve`; [CLI-1.2.2] ~:470 `_collect_all_results` → the typed all-results path (`_collect_all_task_results`/`await_task_result`); [CLI-1.4.1] ~:662 `cmd_validate_taskspec()` → semantic owner `weft/commands/specs.py::cmd_spec_validate`, with rendering and exit adaptation staying in `weft/cli/app.py` (name the moved render helpers) per [PY-2]; [CLI-6] ~:872–877 `cmd_tidy()/cmd_dump()/cmd_load()/cmd_prune()` → `cmd_system_tidy/dump/load/prune` |
| docs/specifications/03-Manager_Architecture.md | D — mapping correction | ~:567 (`serve_command`) |

(Review S1 corrected the first draft's claim that Quick Reference was the
only spec touched: deleting a symbol named in an `_Implementation
mapping_` block without editing the block is the traceability debt
CLAUDE.md §4.5 forbids.)

### 00-Quick_Reference queue table (three-column global-queue table; strategy D)

Remove the two rows `weft.manager.ctrl_in` and `weft.manager.ctrl_out`
(`weft.manager.outbox` is live and its row stays), and add this exact
paragraph directly below the table:

> Manager control queues are task-local: a manager is addressed through
> its own `T{manager_tid}.ctrl_in` / `T{manager_tid}.ctrl_out`, located
> through the `ctrl_in`/`ctrl_out` fields of its `weft.state.services`
> record. Readers honor whatever queue names a live manager's record
> advertises, so an older manager still registered under the legacy
> global names `weft.manager.ctrl_in`/`ctrl_out` remains controllable
> during an in-place upgrade. Those legacy names are not created by
> current managers; a queue with such a name appearing in a dump has no
> implicit live consumer.

**The plan's own fallback condition fired** (Codex): `manager_runtime.py::_manager_ctrl_queue_name`
/ `_manager_ctrl_out_queue_name` (~:828–842) deliberately read the record
fields before deriving `T{tid}.*`, `_send_stop` uses that path, and
`tests/core/test_manager.py:~8710` exercises records and PONGs carrying
the legacy names — an in-place upgrade can have an old live manager
advertising them. "Runtime-only" means excluded from dumps, not that old
managers cannot coexist. The constants are still deletable (the
compatibility path reads record fields generically). Update the
CLAUDE.md §2 queue-naming block in the same commit. Same-commit test/doc
pins: `tests/specs/quick_reference/test_queue_names.py` (~:9–10, :24–25),
`tests/specs/manager_architecture/test_manager_state_events.py`
(~:12–13, :53–54), `README.md` ~:656–657. Literal-string uses in
`tests/tasks/test_heartbeat.py`, `tests/core/test_control_probe.py`,
`tests/core/monitor/policies/test_dead_task*.py`, `test_runtime_control.py`
are "non-task queue name" examples and need no change.

Land only inside sub-slice 5.6 after its re-verification; if any
legacy-record consumer of those names is found, replace this correction
with wording marking them as legacy record fields with no live queue.

## 5. Tasks

Sub-slices 5.1–5.8 in any order, each its own commit(s); 5.1 is one
commit per command module. Per-slice done signal: zero-caller grep
recorded, tests repointed (not deleted) where they asserted behavior,
`tests/system/test_constants.py` green, suite green.

## 6. Testing Plan

Repointed tests must keep their observable assertions. New tests only
for 5.7 (boundary) and 5.8 (rejections through the real sequence). No
mocks introduced.

## 7. Verification and Gates

Per slice: the touched test modules. Final: full suite + mypy + ruff +
`tests/specs/`. Rollback: each slice revertable.

## 8. Independent Review Loop

Required for the completed work (Class 3+); for 5.7 and 5.8 before
implementation (construction-time and plugin boundary changes). Stance:
prove a deleted helper had a production caller the grep missed
(dynamic import, entry point, `__all__` re-export consumed by
`integrations/`).

## 9. Out of Scope

`stop_many`/`kill_many` semantics; preflight call collapse; the
window-scan engine (plan 6); `_WORKER_SNAPSHOT_EXPECTED_FIELDS` ledger
design.

## 10. Fresh-Eyes Review

Author pass 2026-08-31: removed the `stop_many` change after [PY-2] was
read; removed the macOS probe item (stale); split 5.1 per module;
corrected 5.7 to keep the check at the BaseTask boundary per [TS-1];
kept the double preflight call.

## Review Record (append-only)

**2026-08-31 — independent pre-promotion review (Claude-family subagent; the cross-family review §8 asks for has NOT yet been run).** No blocker: the
reviewer re-ran every zero-caller check (including `pyproject.toml`
entry points, `weft.commands.__init__` exports, `weft.helpers`, and
`integrations/weft_django`) and could not produce a missed production
caller. Dispositions:

| Finding | Disposition |
|---------|-------------|
| S1 — spec `_Implementation mapping_` blocks name deleted symbols (10-CLI ×4 sections, 03 ×1) | Applied: strategy-D mapping corrections added to the delta table |
| S2 — ruff suppression registry rows and index regeneration | Applied to §4 |
| S3 — 5.1 stop-gate fires: `test_status_json_projects_only_owned_broker_identity_fields` pins legacy status-JSON projection through `format_message_id`; the typed path emits ints (and shipped `weft status --json` already emits ints while `weft task list --json` emits strings); no spec requires projection | **Decided:** delete the test as unspecced legacy rendering (consistent with "no CLI shape change"); the shipped int/string inconsistency between `status --json` and `task list --json` is recorded as a follow-up, not fixed here |
| S4 — 5.6 same-commit pins (`tests/specs/quick_reference/test_queue_names.py`, `test_manager_state_events.py`, README ~:656–657); literal examples unaffected | Applied |
| S5 — `reload_config` is called by three redaction tests in `tests/tasks/test_task_observability.py` (:760, :793, :824); `_config` has no other reader after 5.4 | Applied at the time: delete `_config` and `reload_config` together; drop the three calls; keep those tests. **Superseded 2026-09-04 (Van):** the logging facade, `_config`, and `reload_config` are kept — see 5.4 |
| S6 — line drift (`dead_tid_control_rows_estimated_deleted` at :3388 and re-aggregated at :4331, :4360–4361, :4388–4389; `_coalesce…` at :4832; test asserts at :5166–5167); PONG summary shape changes | Applied (symbols govern; shape change stated) |
| S7 — five more `"runner_diagnostics"` literals (`cli/app.py:1253`, `commands/system.py:1940–1941`, `_task_snapshot_reducer.py:88, :291`) | Applied: all nine sites plus a zero-literal grep gate |
| S8 — the microsandbox exemplar's `_options.py:132–135` contains the capability re-check being deleted elsewhere; `test_plugin_validation.py:105` pins it | Applied: those lines go too; test repointed through the core sequence |
| Notes — Quick Reference: replace rows with one explanatory line; `weft.manager.outbox` stays; `cmd_tidy`/`cmd_dump` are explicit deletions; add a precondition docstring on `RunnerPlugin.create_runner` (`weft/ext.py` exports no plugin loader, so there is no supported route to `create_runner` without prior validation); `TERMINAL_TASK_LIFECYCLE_STATUS_VALUES` alias must be defined below :1754; expected 5.7 message is the joined three-error string | Applied |
| Owner (Van) — external projects embed `weft/core/tasks/` surfaces | Applied: `yield_strategy` is **kept** by owner decision; caution recorded in §4 |

**2026-08-31 — Codex (cross-model) pre-promotion review.** Verdict: not
promotable as first written (four blockers); 5.5 and 5.7 confirmed
implementable; no data-loss scenario found in any deletion. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — 5.1 stop-gate fired: [CLI-1.2.1] requires string identifiers in status JSON; the typed path emits ints; the legacy test is the firing assertion | Applied: **reverses the Claude-round S3 disposition** — projection moves into the CLI adapter and the test is repointed, not deleted |
| B2 — 5.8 created an unstated precondition on the public `RunnerPlugin.create_runner` | Applied: capability re-checks in `create_runner` are kept; only option re-validation is consolidated |
| B3 — manager-control fallback condition fired (record-advertised legacy names honored during in-place upgrade) | Applied: Quick Reference wording per the plan's own alternate clause; constants still deleted |
| B4 — Quick Reference delta not implementable as a table row | Applied: exact paragraph below the table |
| S — `write_file_atomically` calls `log_debug`; `cmd_manager_stop` semantics; harness `stop_if_absent`; serve custody order; [CLI-0] and exact mapping after-text; probe guard governed by [CC-2.3] with alias-routed tests; hardening applies to 5.7/5.8; split 5.6 and 5.8; `yield_strategy` owner-confirm | All applied |

**2026-09-07 — review against code/spec baseline `bcea628e`; plan correction.**

| Finding | Disposition |
|---------|-------------|
| F2 — 5.4 retained the logging facade but still instructed its live caller to bypass the logging switch | Accepted: keep `write_file_atomically` calling `log_debug`; remove the stale direct-logger instruction, preserving the September 4 owner decision. This supersedes the earlier instruction to change that call before deleting the facade. |

Probe evidence: with a DEBUG-level handler attached to the existing
helper logger and `_config["WEFT_LOGGING_ENABLED"] = False`, `log_debug`
emitted no record while the proposed direct `logger.debug` call emitted
one. The gate is `weft/helpers/__init__.py::send_log` (:557–558), used by
`log_debug` (:613) and the live atomic-write caller (:795–797). Code and
specs are unchanged by this revision; it does not claim an implementation
or final-review pass.

## Implementation Record (2026-09-07)

Class 5. User requires one commit per plan, superseding all internal
per-module commit instructions. Plan 4 lands first; this worktree is isolated.
Independent pre-implementation review passed after these corrections: Docker
and macOS direct create_runner do not currently repeat all capability checks.
Preserve their actual capability gates and all microsandbox checks; do not add
a new direct-construction precondition or rejection behavior. Consolidate only
option parsing. The live status JSON adapter retains its complete current
shape while projecting only owned broker identifiers to strings. Preserve
logging facade/config, watcher yield_strategy, typed client return shapes,
absent-manager None, terminal fallback and selected manager identity.
TaskSpec keeps empty-template validation and runtime construction IO checks.
Strategy D queue text and mapping corrections accompany their source changes.

Implementation inventory corrections and stop-gate dispositions:

- The runner_diagnostics literal in `core/runner_diagnostics.py::__all__` is an
  exported function name, not a payload key. It remains; all payload-key sites
  use RUNNER_DIAGNOSTICS_FIELD.
- Removing the old result adapter exposed a second mandated CLI gap:
  [CLI-1.2.2] requires reconciliation metadata for claimed residue. The typed
  TaskResult now carries optional default-None reconciliation, preserving its
  existing required fields/class, and the live renderer emits the specified
  failure JSON. The test was moved, not dropped. Spec 14 records that carrier.
- Live status also lacked legacy external-log health/deferred-write warnings
  despite carrying the typed diagnostics. Their rendering moved into the live
  service loop. Owned watch-event timestamps now use string projection as
  required. Current live JSON shapes and formatting otherwise remain intact.
- The dead validation module's stored-name resolver had no live caller or spec;
  its two dead-path tests retire. File validation, summaries and preflight
  assertions remain. Current CLI option-conflict precedence is unchanged.
- Dead helper/protocol/SQL caller searches covered weft, bin, extensions,
  integrations, tests, exports and entrypoints. The result queue iterator became
  unused after its sole legacy caller was removed and retires with that caller.
- Monitor PONG summary drops only the three named always-zero fields. The live
  orphan recovery fetch/delete result and processor scan remain for later work.

Independent same-family completed review passed root helpers/TaskSpec/Monitor/
vocabulary changes (193 pure tests) and plugin changes (119 pure tests), with
all actual direct-create capability behavior retained. Full process tests,
metadata, Ruff, mypy and suppression inventory remain final gates.

Final commands review caught a context-preservation regression in the proposed
thin serve wrapper: passing only context.root discarded an explicit client's
broker/config. A public-client probe observed interval137 replaced by default5.
The concrete shared `_serve_manager_context` owner now accepts the already
resolved context; the typed path builds once and the client passes its exact
context. Red-before/green-after regression checks context and broker identity,
custom config and None return. Root independent review passed the fix.

Status/validation, result, plugin, root helper/Monitor/boundary, and command
retirements have completed independent same-family review. No process tests
were run concurrently with the preceding plan's root full gate. Suppression
reconciliation retires SUP054/058/119/245/309/336/337/351/353: 210 groups,
344 directives, 131 C901. Metadata/static gates pass; the full root suite
remains before commit.

Final root verification: 4,539 passed, 16 skipped in the full suite including
slow tests (`pytest -m '' -n 2 -v`); Ruff, full mypy (191 source files),
and suppression reconciliation passed. Skips are 11 opt-in live-provider
cases and five PostgreSQL-only cases under SQLite.

The first full run found six stale test seams: four harness tests patched the
retired tuple stop adapter, one Docker parity test directly used the retired
constructor argument, and one leadership fixture sent PONG to the legacy
shared queue despite advertising task-local queues. Tests now use the runtime
stop seam, the public plugin factory, and the advertised reply queue. Routing,
mount/build, and leadership assertions remain intact. The full harness module
passed all 39 cases; the two other focused cases passed before the clean full
rerun. Independent read-only integration review and final test-fix review
passed. No source or test edits occurred during the final full gate.
