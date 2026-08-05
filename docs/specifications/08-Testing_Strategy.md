# Testing Strategy

This document records the tests that exist now and why they are split the way
they are. Deferred test surfaces live in
[08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md).

## Why This Shape Exists [TS-0]

Weft tests through the repo-managed environment and a small set of shared
harnesses:

- `.envrc` and the in-repo `.venv` keep verification deterministic.
- `WeftTestHarness` in `tests/helpers/weft_harness.py` owns isolated project
  roots, live runtime tracking, and cleanup.
- `run_cli()` in `tests/conftest.py` drives the real subprocess CLI surface.
- `broker_env`, `queue_factory`, and `task_factory` in `tests/conftest.py`
  provide broker-backed fixtures for queue and task tests.
- `shared` vs `sqlite_only` keeps backend-neutral coverage separate from
  SQLite-specific coverage.
- Hypothesis is a dev-only dependency used for focused property-based
  invariant sweeps. Property tests use the `property` marker and remain in the
  normal domain modules they exercise.
- `tests/specs/test_test_audit_policy.py` enforces the classification tables,
  and `tests/test_harness_registration.py` guards harness-registration plumbing.
- The Postgres-backed check is `bin/pytest-pg --all` for backend-sensitive
  changes.
- The benchmark scripts in `tests/long_session_surface_benchmark.py` and
  `tests/multiqueue_polling_benchmark.py` are dev-only measurement tools, not
  part of the canonical test contract.

The point is not to maximize suite count. The point is to keep the current
contract exercised where it matters and to make backend-sensitive drift easy to
see.

Current classification rule:

- test modules should declare backend scope explicitly through `shared` or
  `sqlite_only`, either directly or through the central classification tables in
  `tests/conftest.py`
- property-based modules should also declare `property`; this marker is a test
  style marker, not a backend-scope marker
- broker-heavy tests run under normal xdist scheduling; parallel contention is
  part of the test signal, and isolation bugs should be fixed in the harness or
  implementation instead of hidden through broad serialization
- broad directory-level audit exemptions are temporary migration scaffolding
  and should disappear once a subtree has been reviewed
- any remaining unaudited debt should stay module-scoped, explicit, and
  reviewable rather than becoming the default home for new tests

Coverage policy:

- patch coverage is the active regression gate for new work and should stay
  materially higher than the legacy project baseline
- project coverage remains at the historical floor until defensive exception
  arms, generated paths, and backend-specific slow paths are classified well
  enough for the number to be meaningful
- after one release cycle with clean pragma/narrowing hygiene, raise project
  coverage to the observed baseline minus a small stability buffer
- broad defensive catches must either be tested, narrowed, or explicitly
  marked `# pragma: no cover - <reason>` so coverage does not confuse
  intentional process-boundary code with missing tests

The terminal handoff reducer requires exhaustive table-driven tests. Its test
table must equal the Cartesian product of all declared states and event kinds,
contain no duplicate cells, and assert the exact next state, action, and
transition ID for valid cells plus the exact rejection for invalid cells.
Every selected reason must be non-empty. Structural reachability and aggregate
coverage helpers do not replace this cell-by-cell proof.

The terminal handoff same-turn selector has one strict order per declared
adapter policy across all eight event kinds. Its table tests cover every
non-empty observation subset under both policies, 510 cases, and each host
adapter routes all 28 unordered event pairs through its declared policy.
Expected priorities are independent test data, not values derived from the
production priority table. Multi-turn cases prove already-reduced stop and
producer-exit level signals cannot starve outcome, seal, or drain expiry.

### Repository Static-Analysis Gate [TS-3]

Weft's Python lint gate uses the rule families selected in `pyproject.toml`
across every tracked first-party `.py`/`.pyi` file and Python-shebang
repository tool. Ruff owns Python file discovery; configuration must include
tracked extensionless Python tools explicitly and must not parse Bash tools
as Python.

Owner: `pyproject.toml` owns rule selection and discovery; the root CI lint
job enforces it. Boundary: `weft/`, `tests/`, `integrations/`, `extensions/`,
and Python tools under `bin/`. Verification:
`tests/specs/test_ruff_policy.py` invokes the real repo-managed Ruff binary,
compares effective discovery and rule selection with reviewed inventories,
and proves each behavior-affecting policy setting fires. Required action: a
Ruff version or rule-selection change must intentionally review and update
the enabled-rule inventory before changing the lock or configuration.

Requirements:

- the root lint job uses repository discovery (`ruff check .`)
- preview rules remain opt-in
- global ignores remain limited to documented repository-wide conflicts;
  per-file ignores are empty; other suppressions are local and narrow
- formatter paths remain explicit; widening lint discovery must not
  silently widen formatter ownership
- the policy gate and source changes land atomically when activating a rule
  with existing findings

_Implementation mapping_: `pyproject.toml` owns Ruff rule selection, the
McCabe threshold, ignores, and extensionless-Python discovery;
`.github/workflows/test.yml` owns the ordered normal-Ruff,
suppression-index, formatter, and mypy CI steps;
`bin/ruff_suppression_index.py` parses [TS-3.1], invokes normal and raw Ruff,
enforces C901 registration completeness and cardinality, and checks or
atomically rewrites only the generated index;
`tests/specs/test_ruff_policy.py` proves effective configuration, discovery,
mutation behavior, and CI wiring; and
`tests/specs/test_ruff_suppression_index.py` proves parser, reconciliation,
symbol attribution, byte preservation, honest exits, and failure safety.
The repository-tool module docstring cites [TS-3] and [TS-3.1] as its
reciprocal implementation reference. The two policy-test module docstrings
cite the section they prove.

Ruff `C901` is enabled repository-wide with
`lint.mccabe.max-complexity = 10`. The score is a visibility signal, not a
design verdict. Each finding must either be simplified at a real ownership
seam or carry a narrow local `C901` suppression registered in [TS-3.1]. The
registry must explain the protected coupling, debugging locality, or
semantic risk; name real behavioral proof; record rejected decompositions
and approval; and assign a stable suppression-group ID. A cohesive parser,
state owner, lifecycle frame, reducer, checklist, test protocol, or
concurrency proof must not be fragmented merely to lower its score.

The policy gate runs normal Ruff and a raw audit with `--ignore-noqa`.
Source directives, human-owned [TS-3.1] groups, the generated symbol index,
and raw findings at tagged locations using Ruff's `noqa_row` must reconcile
exactly, including each group's approved directive and raw-diagnostic
cardinalities. In addition, every raw `C901` diagnostic must resolve to a
tagged, approved [TS-3.1] directive at its `noqa_row`; the global aggregate
is not sufficient proof of registration. A new unsuppressed finding, an
untagged or unregistered `C901` suppression, an unregistered tagged
directive, an unknown or empty group, a cardinality change, a stale
directive, a stale generated index, or a mismatched raw finding fails
verification.

A separate global raw-diagnostic inventory covers every enabled-rule local
`noqa`, including reasoned suppressions outside [TS-3.1]. It is an exact
aggregate by rule code. Aggregate changes fail verification; a same-code
remove/add swap remains a source-review concern rather than receiving false
identity semantics. Per-file ignores, global C901 ignores, threshold raises,
blanket file directives, and baseline allowlists are prohibited.

### Approved Ruff Suppression Registry [TS-3.1]

This section owns approved local exceptions to [TS-3]. A plan may propose or
review a candidate, but it must not become the lasting source of truth for
an adopted exception.

_Implementation mapping_: approved local source directives across `weft/`,
`tests/`, `bin/`, `integrations/`, and `extensions/` point back to this
registry; `bin/ruff_suppression_index.py` reconciles those directives with the
human rows, raw Ruff findings, and generated symbol index; and
`tests/specs/test_ruff_policy.py` plus
`tests/specs/test_ruff_suppression_index.py` prove the live policy and
check/write failure boundaries.

Owner: this section owns each stable suppression group, human-reviewed
rationale, and approved cardinality. The local directive owns rule codes and
the stable group pointer. The generated index owns only derived paths,
qualified symbols, actual directive counts, and raw-diagnostic counts.
Boundary: only the rules, cardinality, invariant, and locations covered by
the approved group. Verification: the named real proof, `ruff check .`, and
`./.venv/bin/python bin/ruff_suppression_index.py --check`. Required action:
obtain explicit review before adding, regrouping, growing, or shrinking a
suppression; update the human row, cardinality, and source pointer together;
then regenerate only the delimited derived index with
`./.venv/bin/python bin/ruff_suppression_index.py --write`.

The approved local form is
`# noqa: <codes> approved [TS-3.1] [RUFF-SUP-NNN] exception`. The stable group
points to the single durable full reason; source comments do not duplicate
it. Group IDs are unique and match `RUFF-SUP-[0-9]{3}`. Every group has at
least one live source directive. Human rows contain `Group`, `Rules`,
`Approved cardinality`, `Protected invariant`, `Real proof`, `Rejected
alternatives`, and `Approval`.

The section also owns one lexically sorted
``Global raw-`noqa` inventory:`` line containing backticked `CODE=count`
entries for every diagnostic exposed by `--ignore-noqa`. The backticks
around `noqa` are part of the canonical parser grammar. This aggregate is a
tripwire, not a second identity registry.

The generated index is enclosed by unique begin/end markers. It renders one
deduplicated `path::qualified_symbol` site per group, sorted by group ID and
site. A symbol is the outermost enclosing function, qualified by class names,
or `<module>`; decorator lines belong to their function. Physical line
remains the internal identity for matching Ruff diagnostics, duplicate
detection, and error messages. Content outside the generated markers is
human-owned and must remain byte-for-byte unchanged during regeneration.

The repository tool must refuse to write if normal Ruff is unclean, a source
or spec marker is malformed, a group is unknown or empty, a rule or
cardinality differs, a raw diagnostic does not match its directive, the
global inventory differs, any raw `C901` diagnostic lacks a tagged approved
directive at the same `noqa_row`, or discovered Python source is unreadable
or syntactically invalid. Policy mismatches exit 1. Anticipated invocation,
decoding, Ruff, and atomic-replacement failures exit 2 with a one-line
diagnostic and no traceback. Both classes leave the spec byte-for-byte
unchanged. Unexpected programming defects retain a traceback as bug
evidence.

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|-------|-------|----------------------|---------------------|------------|-----------------------|----------|
| `RUFF-SUP-001` | `C901` | `1` directives; raw: `C901=1` | Explicit mounts override provider defaults; env, runtime-home, copied-file, and diagnostic resolution remain one ordered result. | `tests/core/test_provider_cli_container_runtime.py::test_resolve_provider_container_runtime_respects_explicit_mount_target_override` | Split env, mount, and copy passes into remote helpers; rejected because it scatters one resolution contract. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-002` | `C901` | `2` directives; raw: `C901=2` | Codex option types, enums, authority limits, and sandbox precedence stay explicit at the provider boundary. | `tests/core/test_provider_cli_backend.py::test_provider_cli_runtime_rejects_raw_options_that_conflict_with_tool_profile` | Generic option-schema engine; rejected because option-specific security conflicts become indirect. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-003` | `C901` | `2` directives; raw: `C901=2` | Exact one-shot and session argv order, including first-turn-only and resume flags, remains locally reviewable. | `tests/core/test_provider_cli_backend.py::test_provider_cli_runtime_executes_one_shot_request`; `tests/core/test_provider_cli_session_backend.py::test_provider_cli_session_continues_across_turns` | One generalized argv builder; rejected because it hides turn-sensitive differences. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-004` | `C901` | `1` directives; raw: `C901=1` | Endpoint history reduction, live-owner proof, stale pruning, and lowest-TID canonicalization remain one operation. | `tests/tasks/test_task_endpoints.py::test_task_reregistration_replaces_prior_endpoint_claim` | Separate discovery and destructive pruning now; rejected pending a lifecycle plan because it can change deletion authority. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-005` | `C901` | `5` directives; raw: `C901=5` | Manager registration preserves lower-TID suppression, supersede-race repair, latest-self retention, terminal publish, and snapshot order. | `tests/core/test_manager.py::test_manager_registry_prunes_expired_rows_on_refresh`; `tests/core/test_manager.py::test_manager_active_heartbeat_race_preserves_superseded_record` | Generic registry repository or fragmented scans; rejected because it widens races and hides owner predicates. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-006` | `C901` | `1` directives; raw: `C901=1` | Host-PID and external-supervisor evidence preserve live, stale, and unknown semantics, including namespace ambiguity. | `tests/core/test_manager.py::test_manager_leadership_keeps_namespace_ambiguous_host_row_after_ping_timeout` | Authority strategy framework; rejected because the short proof ladder is clearer in one owner. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-007` | `C901` | `2` directives; raw: `C901=2` | Leadership resolution preserves stale pruning, unknown fail-closed behavior, launch fences, reservation requeue, persistent-child protection, and drain order. | `tests/core/test_manager.py::test_manager_leadership_yields_to_canonical_lower_manager`; `tests/core/test_manager.py::test_manager_leadership_yield_waits_while_persistent_children_exist` | Extract election and yield into a second control path; rejected because it could split dispatch authority. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-008` | `C901` | `2` directives; raw: `C901=2` | Child exit and terminal proof preserve multiprocessing-versus-OS precedence, startup/proof grace, ctrl-out-first evidence, and log fallback. | `tests/core/test_manager.py::test_cleanup_children_waits_for_terminal_proof_after_clean_exit`; `tests/core/test_manager.py::test_child_has_exited_trusts_live_host_pid_before_process_view` | Independent PID and queue-proof services; rejected because evidence precedence would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-009` | `C901` | `2` directives; raw: `C901=2` | Child cleanup preserves bounded reap, reservation acknowledgement, service terminalization, and one-deadline STOP-to-kill escalation over process trees. | `tests/core/test_manager.py::test_manager_child_termination_uses_one_deadline_for_multiple_children`; `tests/core/test_manager.py::test_manager_cleanup_terminates_worker_descendants` | Split reap and termination stages opportunistically; rejected because budgets and descendant ownership can drift. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-010` | `C901` | `2` directives; raw: `C901=2` | Reserved messages honor KEEP, CLEAR, and REQUEUE exactly while stale internal reservations never delete live-manager work. | `tests/core/test_manager.py::test_manager_stop_mid_handler_requeues_reserved_work_unlaunched`; `tests/core/test_manager.py::test_manager_keeps_internal_reserved_when_manager_liveness_unknown` | Generic queue cleanup abstraction; rejected because internal protection and reserved-policy semantics differ. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-011` | `C901` | `1` directives; raw: `C901=1` | Broker reservation remains dispatch authority across control fences, validation, error policy, launch, and acknowledgement. | `tests/core/test_manager.py::test_manager_stop_mid_handler_requeues_reserved_work_unlaunched` | Split validation and launch into another dispatch path; rejected because it weakens reservation ownership. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-012` | `C901` | `4` directives; raw: `C901=4` | Singleton service convergence preserves evidence priority, canonical-owner selection, duplicate termination, pending-spawn handling, and diagnostic reasons. | `tests/core/test_manager.py::test_reconcile_reuses_tracked_service_candidates`; `tests/core/test_manager.py::test_task_monitor_duplicate_tracked_child_force_kills_owned_process` | Separate evidence, convergence, and termination controllers; rejected because Manager must remain the sole supervision owner. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-013` | `C901` | `1` directives; raw: `C901=1` | Autostart compilation preserves task/pipeline distinction, defaults order, service metadata, lifecycle, and input fallback. | `tests/core/test_manager.py::test_manager_autostart_pipeline_target_launches_pipeline_run` | Reuse a generic nested-default merger; rejected because manifest semantics and TaskSpec defaults are not interchangeable. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-014` | `C901` | `1` directives; raw: `C901=1` | One reactor turn preserves the exact ordering of worker drain, registration, leadership, control, child cleanup, dispatch, service convergence, and idle shutdown. | `tests/core/test_manager.py::test_process_once_reconciles_internal_services_before_user_spawn_work` | Fragment the turn into phase objects; rejected because it reduces schedule locality and can create a second spine. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-015` | `C901` | `1` directives; raw: `C901=1` | Runtime registry snapshots preserve normalization, PONG rescue, namespace ambiguity, latest-row selection, and optional stale pruning. | `tests/commands/test_manager_commands.py::test_list_command_rescues_unreachable_host_pid_with_pong` | Split view construction from pruning without a runtime plan; rejected because the selected snapshot controls deletion. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-016` | `C901` | `1` directives; raw: `C901=1` | Diagnostic proof precedence and exact liveness, source, detail, eligibility, and canonical-candidate fields remain stable. | `tests/commands/test_manager_commands.py::test_list_command_diagnostic_marks_lowest_live_manager_canonical` | Generic liveness strategy framework or authority extraction; rejected because the full proof ladder is clearer in one diagnostic owner. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-017` | `C901` | `3` directives; raw: `C901=3` | Manager stop preserves STOP-first confirmation, PID/process proof, optional force escalation, registry terminalization, and external-supervisor refusal. | `tests/commands/test_manager_commands.py::test_stop_command_waits_for_pid_exit_after_stopped_status`; `tests/commands/test_manager_commands.py::test_stop_command_force_replaces_active_registry_record` | Independent signal and registry cleanup flows; rejected because they can claim stopped before both boundaries agree. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-018` | `C901` | `1` directives; raw: `C901=1` | Detached manager startup preserves competing-manager convergence, acknowledgement recovery, liveness checks, settlement, and launcher cleanup. | `tests/commands/test_run.py::test_start_manager_treats_post_proof_ack_failure_as_nonfatal` | Break the startup race into generic polling helpers; rejected because handshake state becomes dispersed. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-019` | `C901` | `1` directives; raw: `C901=1` | Managed-service reduction keeps deterministic priority among terminal evidence, live owner, pending spawn, uncertainty, lifecycle limits, and backoff. | `tests/core/test_manager_services.py::test_all_managed_service_actions_have_table_coverage` | Meta-state-machine framework; rejected because the ordered pure reducer is already the inspectable policy table. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-020` | `C901` | `1` directives; raw: `C901=1` | Lifetime reports preserve TaskSpec summary authority while filling only absent state and metadata evidence. | `tests/core/monitor/test_lifetime_report.py::test_collation_lifetime_report_promotes_taskspec_and_monitor_context` | Generic mapping overlay helper; rejected because authoritative versus fallback fields become unclear. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-021` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: config key/default precedence, derived reserved age, enum rules, processor constraints, and mode cross-checks stay unchanged. | `tests/core/test_task_monitoring.py::test_runtime_config_reserved_gate_tracks_configured_retention` | Generic config framework; rejected in favor of narrow adjacent typed readers subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-022` | `C901` | `1` directives; raw: `C901=1` | Monitor-store update chunks commit before the checkpoint advances, with per-TID merge and message refs in the same transaction sequence. | `tests/core/test_monitor_store.py::test_monitor_store_batch_ingest_updates_tasks_and_checkpoint` | Split checkpoint writing from ingestion orchestration; rejected because it obscures the commit boundary. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-023` | `C901` | `1` directives; raw: `C901=1` | Collation chooses the oldest eligible anchor, applies its age gate, collects only that TID, and stops at its first terminal row. | `tests/core/test_task_monitor_cleanup.py::test_task_monitor_cleanup_collates_terminal_task_log_for_anchor_tid` | General grouping iterator; rejected because FIFO anchor and stop semantics become implicit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-024` | `C901` | `2` directives; raw: `C901=2` | Worker snapshots exhaustively classify copy/share/reset ownership and close every worker-local resource exactly once with ordered errors. | `tests/tasks/test_task_monitor.py::test_task_monitor_worker_local_snapshot_owns_mutable_runtime_resources`; `tests/tasks/test_task_monitor.py::test_task_monitor_worker_close_attempts_all_resources_and_reports_failure` | Reset registries or reflection-driven cleanup; rejected because explicit field and resource ownership is safer to audit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-025` | `C901` | `2` directives; raw: `C901=2` | Forward and pre-checkpoint recovery preserve bounded selection, store-before-delete, report-before-delete, active protection, child-ref retirement, and checkpoint rules. | `tests/core/test_task_monitor_cleanup.py::test_task_monitor_cleanup_deletes_malformed_task_log`; `tests/tasks/test_task_monitor.py::test_task_monitor_jsonl_then_delete_recovers_precheckpoint_service_rows` | Unify both flows now; rejected because their checkpoint authority and eligibility differ. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-026` | `C901` | `1` directives; raw: `C901=1` | Summary emission precedes family disposition, with stale-open destruction protection and terminal-control bookkeeping preserved. | `tests/tasks/test_task_monitor.py::test_task_monitor_failed_summary_disposition_blocks_processor_delete` | Independent summary and disposition workers; rejected because deletion must not outrun durable reporting. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-027` | `C901` | `1` directives; raw: `C901=1` | Terminal-control cleanup preserves terminal versus suspect protection, report-before-delete, absolute slice deadlines, durable disposal/control marks, retirement, and next-slice scheduling. | `tests/tasks/test_task_monitor.py::test_task_monitor_terminal_control_cleanup_does_not_wait_for_retention` | Generic runtime queue sweeper or sharing with reserved cleanup; rejected because terminal-control proof and disposition are distinct. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-028` | `C901` | `1` directives; raw: `C901=1` | Manager `task_spawned` row trim preserves report-before-delete, exact reconciliation, child-ref retirement, and the open manager family. | `tests/tasks/test_task_monitor.py::test_task_monitor_trims_manager_task_spawned_rows_without_closing_manager_family` | Share with family retirement or orphan recovery; rejected because this is row-level compaction that must not close the family. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-029` | `C901` | `1` directives; raw: `C901=1` | Outbox decoding preserves raw fallback, base64 stream handling, stdout accumulation, stderr exclusion, final emission, and result transforms. | `tests/commands/test_result.py::test_process_outbox_message_handles_malformed_base64` | Separate envelope handlers; rejected because one wire-format decoder is easier to reason about end to end. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-030` | `C901` | `1` directives; raw: `C901=1` | Pipeline stage defaults preserve deep-copy isolation and ordered args, keyword args, env, and IO overlay without replacing explicit task values. | `tests/core/test_pipelines.py::test_pipeline_compiler_sets_defaults_input_override_for_interior_edge` | Generic nested-map merge; rejected because domain precedence becomes hidden. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-031` | `C901` | `1` directives; raw: `C901=1` | Exact-prune application preserves queue grouping, report-only rules, exact status, batch reconciliation, per-candidate errors, result order, and closure. | `tests/core/test_pruning_apply.py::test_exact_id_apply_reconcile_verifies_per_id_on_batch_under_deletion` | Backend-specific or caller-specific deletion loops; rejected because this is the single shared exact-delete authority. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-032` | `C901` | `1` directives; raw: `C901=1` | Retention selection visibly applies per-TID recency, newest-terminal, age, nonterminal, active-manager, and force-override protections. | `tests/commands/test_retention_prune.py::test_task_log_dry_run_reports_superseded_rows_without_deleting` | Declarative protection engine; rejected because override reasons and ordering become indirect. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-033` | `C901` | `1` directives; raw: `C901=1` | Manager registry pruning preserves malformed-row age, canonical normalization, per-manager recency, and active-manager liveness proof. | `tests/commands/test_runtime_prune.py::test_manager_prune_reports_superseded_and_stale_active_rows` | Generic runtime-registry selector or sharing with endpoints; rejected because manager liveness and grouping are distinct. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-034` | `C901` | `1` directives; raw: `C901=1` | Host terminal handoff preserves observation batching, reducer authority, first accepted stop, drain deadline, transport/process races, metrics, and diagnostics. | `tests/tasks/test_runner.py::test_real_pipe_exit_then_outcome_uses_production_handoff_driver`; `tests/tasks/test_runner.py::test_one_shot_stop_effect_cannot_reset_absolute_drain_deadline` | Opportunistic phase extraction or a new state framework; rejected pending a dedicated execution-path plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-035` | `C901` | `2` directives; raw: `C901=2` | Host session startup preserves argv/env/cwd validation, pipe ownership, platform flags, optional monitors, readiness, handles, and complete rollback. | `tests/tasks/test_runner.py::test_interactive_session_collects_immediate_exit_stream_tail`; `tests/tasks/test_runner.py::test_agent_session_spawn_failure_closes_queue_and_response_endpoints` | Generic session factory; rejected because command and agent resource ownership differs. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-036` | `C901` | `1` directives; raw: `C901=1` | Subprocess monitoring preserves stream draining and cancellation, timeout, limit, exit, monitor, diagnostic, and final-output priority. | `tests/core/test_subprocess_runner.py::test_completed_process_at_timeout_wake_boundary_returns_ok`; `tests/tasks/test_runner.py::test_run_monitored_subprocess_emits_live_chunks_before_exit` | Local helper fragmentation or reducer conversion; rejected pending a dedicated execution-path plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-037` | `C901` | `2` directives; raw: `C901=2` | Incremental stream decoding preserves CR/LF normalization across chunks, raw/text fallback, decoder flush, and exactly one sentinel. | `tests/tasks/test_runner.py::test_run_monitored_subprocess_emits_live_chunks_before_exit` | Move normalization and decoder state to remote utilities; rejected because it reduces stream-protocol locality. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-038` | `C901` | `1` directives; raw: `C901=1` | State-machine definitions validate state/action/transition presence, terminal/sink rules, IDs, sources, targets, actions, reasons, and outgoing coverage together. | `tests/core/test_state_machines.py::test_duplicate_transition_ids_fail`; `tests/core/test_state_machines.py::test_terminal_and_sink_states_cannot_overlap` | Meta-schema or fragmented validators; rejected because the complete definition contract is clearer in one pass. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-039` | `C901` | `1` directives; raw: `C901=1` | Known-TID evidence preserves log, local, runtime, stale, claimed-output, and PONG precedence plus timestamp reconciliation. | `tests/commands/test_task_evidence.py::test_known_tid_ping_pong_updates_task_status` | Independent evidence-provider pipeline; rejected because which proof wins would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-040` | `C901` | `1` directives; raw: `C901=1` | Reactor topology rejects noncanonical roles, support routes, empty names, and unapproved aliases before broker I/O. | `tests/tasks/test_task_execution.py::test_base_task_rejects_duplicate_queue_roles_before_broker_side_effects` | Generic graph validator; rejected because the small canonical role contract should remain explicit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-041` | `C901` | `1` directives; raw: `C901=1` | Task drive preserves single-owner and reentrancy guards, termination priority, worker activity, bounded waits, and exactly-once finalization. | `tests/tasks/test_task_execution.py::test_base_task_run_until_stopped_finalizes_when_turn_raises` | Split stop checks and waiting into another loop object; rejected because it can alter reactor shutdown. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-042` | `C901` | `1` directives; raw: `C901=1` | Every runner outcome preserves its distinct TaskSpec state, event, pipeline status, diagnostics, deferred-control precedence, and raised error. | `tests/tasks/test_consumer_terminal_events.py::test_consumer_terminal_outcome_emits_one_state_event`; `tests/tasks/test_consumer_terminal_events.py::test_consumer_unknown_runner_outcome_status_fails_task` | Data-driven terminal dispatch; rejected because outcome-specific side effects become harder to audit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-043` | `C901` | `1` directives; raw: `C901=1` | Heartbeat emission preserves stale-heap removal, ownership checks, supersede stop, failure reporting, rescheduling, and activity restoration. | `tests/tasks/test_heartbeat.py::test_heartbeat_late_wake_coalesces_to_one_emit` | Separate scheduling and ownership loops; rejected because one due-emission transaction is easier to reason about. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-044` | `C901` | `1` directives; raw: `C901=1` | Interactive finalization preserves prior terminal authority, reserved error policy, stdout/stderr final envelopes, terminal envelope, stream teardown, and close order. | `tests/commands/test_interactive_client.py::test_interactive_client_failure_overrides_stdout_final` | Separate state and transport finalizers; rejected because protocol ordering can diverge. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-045` | `C901` | `2` directives; raw: `C901=2` | Topology mutation preserves drive ownership, FIFO, generation fencing, waiter swap, atomic publish/rollback, fatal propagation, signalling, and exact closure. | `tests/tasks/test_multiqueue_watcher.py::test_background_post_replace_publication_failure_restores_old_waiter`; `tests/tasks/test_multiqueue_watcher.py::test_owner_fatal_exit_signals_every_queued_mutator` | Generic mutation transaction abstraction; rejected because resource ownership and SIGINT critical state become dispersed. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-046` | `C901` | `1` directives; raw: `C901=1` | Service-worker shutdown sends one stop per worker under bounded queue pressure and drains under the caller's deadline without deadlock. | `tests/tasks/test_service_task.py::test_service_task_full_sentinel_queue_respects_cleanup_deadline` | Generic thread-pool stop helper; rejected because Weft worker-result and queue-pressure semantics are owner-specific. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-047` | `C901` | `1` directives; raw: `C901=1` | Readiness preserves boot, ready, and startup-error interpretation, handshake diagnostics, process-exit late drain, sealed-channel join, and final response handling. | `tests/tasks/test_runner.py::test_agent_session_startup_error_survives_immediate_child_exit`; `tests/tasks/test_runner.py::test_agent_session_reports_hang_after_boot_handshake` | Generic session protocol framework or payload extraction; rejected because timing, process state, and payload meaning are one handshake. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-048` | `C901` | `1` directives; raw: `C901=1` | Agent-session handoff preserves reducer decisions, cancellation/timeout/limit priority, channel/process races, drain, metrics, diagnostics, and session invalidation. | `tests/tasks/test_runner.py::test_agent_session_routes_all_event_pairs_through_persistent_policy` | Share the host handoff implementation now; rejected pending a dedicated execution-path plan because outcome types and ownership differ. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-049` | `C901` | `1` directives; raw: `C901=1` | Agent termination preserves deadline-aware process-tree teardown, reap, kill fallback, and ordinary TERM-to-KILL escalation. | `tests/tasks/test_task_execution.py::test_agent_session_deadline_preserves_process_tree_kill_escalation` | Split deadline and ordinary termination without proof; rejected because absolute-budget semantics can drift. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-050` | `C901` | `1` directives; raw: `C901=1` | TaskSpec resolution preserves deep-copy isolation, TID and queue rewriting, defaults, runner/context inheritance, and the template-to-resolved boundary. | `tests/taskspec/test_taskspec_properties.py::test_resolve_taskspec_payload_does_not_mutate_input`; `tests/taskspec/test_taskspec_properties.py::test_resolved_taskspec_freezes_spec_and_io` | Generic recursive defaulting; rejected because canonicalization order and ownership become unclear. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-051` | `C901` | `1` directives; raw: `C901=1` | Agent validation preserves output coupling, unique tools, bounded LLM authority, and provider-CLI field, mode, scope, and tool restrictions. | `tests/specs/taskspec/test_agent_taskspec.py::test_validate_taskspec_reports_agent_errors` | Runtime-rule table; rejected because provider-specific errors and constraints become less local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-052` | `C901` | `1` directives; raw: `C901=1` | Target validation preserves required and forbidden fields plus persistent provider-agent conversation constraints and exact errors. | `tests/specs/taskspec/test_agent_taskspec.py::test_agent_spec_rejects_function_target`; `tests/specs/taskspec/test_agent_taskspec.py::test_agent_per_task_conversation_requires_persistent_task` | Declarative target matrix; rejected because error ownership and cross-field conditions become indirect. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-053` | `C901` | `1` directives; raw: `C901=1` | State validation preserves timestamp ordering and created, running, spawning, and terminal field constraints. | `tests/specs/taskspec/test_state_transitions.py::test_generated_status_operation_sequences_never_leave_terminal_state` | Generic field-rule engine; rejected because the state contract is clearer as visible branches. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-054` | `C901` | `1` directives; raw: `C901=1` | Strict resolved TaskSpecs preserve template exemption and aggregated top-level, target, IO, state, and metadata requirement errors. | `tests/taskspec/test_taskspec_properties.py::test_resolved_taskspec_freezes_spec_and_io` | Split final validation among section helpers; rejected because the resolved-spec completion contract would be scattered. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-055` | `C901` | `1` directives; raw: `C901=1` | Declared-option parsing preserves long-option grammar, passthrough grouping, duplicates, normalization, choices, defaults, and required errors. | `tests/core/test_spec_run_input.py::test_parse_declared_run_input_args_parses_string_and_path_values`; `tests/core/test_spec_run_input.py::test_parse_declared_run_input_args_rejects_missing_required_option` | Separate tokenizer and validator phases; rejected because token consumption and declaration ownership are tightly coupled. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-056` | `C901` | `1` directives; raw: `C901=1` | Reserved cleanup preserves destruction-protected selection, report-before-delete, age and family limits, deadlines, durable checked marks, retirement, and progress. | `tests/tasks/test_task_monitor.py::test_task_monitor_reserved_cleanup_respects_min_age_gate` | Generic runtime queue sweeper or sharing with terminal-control cleanup; rejected because reserved eligibility and checked-state semantics differ. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-057` | `C901` | `1` directives; raw: `C901=1` | Orphan task-log recovery preserves summary gating, bounded family selection, re-ingest before exact deletion, child-ref retirement, and checked marks. | `tests/tasks/test_task_monitor.py::test_task_monitor_recovers_orphan_raw_task_log_rows_after_bad_raw_mark` | Share with ordinary ingest or dead-TID cleanup; rejected because orphan recovery starts from inconsistent Monitor state. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-058` | `C901` | `1` directives; raw: `C901=1` | Dead-TID coalescing preserves fetch, re-ingest, summary, family disposition, then exact child-ref deletion order for proven-dead tasks. | `tests/tasks/test_task_monitor.py::test_task_monitor_repair_path_exports_summary_before_deleting_rows` | Share with orphan recovery; rejected because dead-TID proof and mandatory summary/disposition sequence differ. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-059` | `C901` | `1` directives; raw: `C901=1` | Endpoint registry pruning preserves per-owner recency before task-status and TID-mapping live-owner proof, with superseded rows excluded from stale selection. | `tests/commands/test_runtime_prune.py::test_endpoint_prune_preserves_live_duplicate_claimants` | Generic runtime-registry selector or sharing with managers; rejected because endpoint grouping and owner proof are distinct. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-102` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: task status preserves snapshot authority while `--process` adds only scoped and live managed PIDs and JSON/plain output retains exact exits and fields. | `tests/cli/test_status.py::test_task_status_process_json_reports_dead_pid_stale_liveness`; `tests/cli/test_status.py::test_task_status_not_found` | Move status reconstruction into the CLI adapter or duplicate runner-liveness policy; rejected in favor of adjacent process-augmentation and rendering helpers subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-104` | `C901` | `1` directives; raw: `C901=1` | Import preview ordering preserves source metadata, bounded alias/queue/warning samples, totals, and the message-ID range. | `tests/commands/test_dump_load.py::test_import_report_formatting` | One helper per optional section; rejected because the linear rendering checklist is clearer and its final order remains local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-105` | `C901` | `1` directives; raw: `C901=1` | Dump parsing preserves first-and-unique header rules, exact format/version, line-numbered errors, positive message IDs, and deduplicated runtime-only exclusions. | `tests/commands/test_dump_load.py::test_cmd_load_rejects_legacy_weft_dump_format`; `tests/commands/test_dump_load.py::test_cmd_load_rejects_reserved_zero_id_before_writes`; `tests/commands/test_dump_load.py::test_cmd_load_dry_run` | Record dispatch helpers carrying the mutable plan, header state, skipped-runtime set, and line context; rejected because they reduce streaming-parser locality. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-106` | `C901` | `1` directives; raw: `C901=1` | One-shot result waiting preserves task-local and log terminal-proof precedence, wrapper-loss fallback, visible/emitted output, quiet/completion grace, timeout, and queue cleanup. | `tests/commands/test_result.py::test_await_one_shot_result_retains_terminal_ctrl_out_proof`; `tests/commands/test_result.py::test_await_one_shot_result_prefers_task_completed_over_manager_wrapper_lost`; `tests/commands/test_result.py::test_await_one_shot_result_accepts_emitted_stream_when_log_event_is_missed` | Opportunistic helper extraction or merging with persistent waits; rejected pending a dedicated transition-model plan because the mutable temporal frame and evidence ordering must remain coherent. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-107` | `C901` | `1` directives; raw: `C901=1` | Realtime observation never consumes result/control queues and preserves snapshot, stream, state, terminal grace, result, end, cancellation, timeout, and cleanup order across three cursors. | `tests/core/test_ops_shared.py::test_realtime_events_uses_terminal_state_seen_during_materialization`; `tests/core/test_ops_shared.py::test_realtime_events_emits_state_when_terminal_derived_from_snapshot`; `tests/commands/test_result.py::test_iter_task_realtime_events_falls_back_on_malformed_io` | Separate queue-scanner helpers carrying cursor and terminal state; rejected pending a dedicated state-machine plan because readers would reconstruct one protocol across scopes. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-108` | `C901` | `1` directives; raw: `C901=1` | Result materialization preserves precedence among TaskSpec metadata, default/custom/pipeline queue activity, log-derived metadata, terminal proof, batch boundaries, timeout, and cleanup. | `tests/commands/test_result.py::test_await_result_materialization_waits_for_taskspec_after_activity_event`; `tests/commands/test_result.py::test_await_result_materialization_falls_back_on_malformed_io`; `tests/core/test_ops_shared.py::test_realtime_events_uses_terminal_state_seen_during_materialization` | Parallel evidence discovery or detached return-payload builders; rejected because acquisition order is the justification for the selected result surface. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-109` | `C901` | `1` directives; raw: `C901=1` | Persistent result waiting returns exactly one work-item batch, tolerates documented boundary skew, avoids stream replay, leaves later batches queued, and preserves terminal/deadline/cleanup precedence. | `tests/commands/test_result.py::test_await_single_result_persistent_returns_one_work_item_batch`; `tests/commands/test_result.py::test_await_single_result_persistent_stream_mode_keeps_next_batch`; `tests/commands/test_result.py::test_await_single_result_tolerates_materialized_boundary_timestamp_skew` | Pass the cursor, quiet-window, boundary, completion, and deadline frame among helpers or merge it with one-shot semantics; rejected pending a dedicated transition-model plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-110` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: result-command validation, claimed-result recovery, materialization, public JSON/plain shapes, and exit mapping remain exact: timeout 124, failure 1, not found/usage 2. | `tests/commands/test_result.py::test_cmd_result_reports_claimed_outbox_without_waiting`; `tests/commands/test_result.py::test_cmd_result_rejects_stream_json_combination`; `tests/commands/test_result.py::test_cmd_result_zero_timeout_reports_materialization_timeout` | Move evidence selection into render helpers or change the public tuple contract; rejected in favor of adjacent validation/rendering seams subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-111` | `C901` | `1` directives; raw: `C901=1` | Interactive sessions preserve queue-mediated IO, prompt/piped modes, terminal fallbacks, STOP-to-KILL escalation, quit normalization, thread exit, client/log cleanup, and final outbox collection. | `tests/cli/test_cli_run.py::test_cli_run_interactive_command_streams`; `tests/commands/test_interactive_client.py::test_interactive_client_control_stop_is_terminal`; `tests/commands/test_interactive_client.py::test_interactive_client_failure_overrides_stdout_final` | Cosmetic callback extraction, PTY substitution, or helpers carrying the live session frame; rejected pending a dedicated lifecycle plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-112` | `C901` | `1` directives; raw: `C901=1` | Run rendering preserves verbose-before-terminal order, queued/completed/failure JSON and plain shapes, error categories, and timeout-versus-failure exit codes. | `tests/commands/test_run.py::test_run_renderer_preserves_terminal_handoff_categories`; `tests/commands/test_run.py::test_run_renderer_keeps_timeout_exit_124` | Separate success and failure renderers; rejected because output selection and exit mapping are clearer together. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-113` | `C901` | `1` directives; raw: `C901=1` | Inline execution preserves target validation, context, argument/env/stdin normalization, TaskSpec construction, post-TID resolution, interactive/noninteractive wait selection, submission recovery, and structured receipt. | `tests/commands/test_run.py::test_execute_run_inline_returns_structured_result_without_rendering`; `tests/commands/test_run.py::test_run_inline_enqueues_task_before_ensuring_manager`; `tests/cli/test_cli_run.py::test_cli_run_reads_stdin` | New orchestration object or extraction of the completion closure with a large state carrier; rejected because template and input ownership would become indirect. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-114` | `C901` | `1` directives; raw: `C901=1` | Run-mode dispatch keeps pipeline, stored spec, command, and function mutually exclusive, rejects mode-specific options before side effects, and reaches exactly one execution path. | `tests/cli/test_cli_run.py::test_cli_run_requires_target`; `tests/cli/test_cli_run.py::test_cli_run_command_and_function_conflict`; `tests/cli/test_cli_run.py::test_cli_run_interactive_json_conflict` | Mode registry or scattered per-mode validators; rejected because mutual-exclusion grammar and error precedence belong together. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-115` | `C901` | `1` directives; raw: `C901=1` | Public submit overrides preserve section-specific merge rules, limits/runner nesting, persistent endpoint-claim validation after renaming, TaskSpec validation, and bundle-root identity. | `tests/core/test_client.py::test_submit_spec_and_pipeline_references_return_tasks`; `tests/commands/test_run.py::test_run_spec_via_manager_explicit_name_overrides_name_and_claims_endpoint`; `tests/commands/test_run.py::test_run_pipeline_explicit_name_overrides_pipeline_task_name` | Generic deep merge or per-section helpers carrying most optional arguments; rejected because boundary validation and cross-section precedence become hidden. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-116` | `C901` | `1` directives; raw: `C901=1` | Queue-first recovery never falsely rolls back a committed submission: spawned, rejected, queued, and reserved outcomes preserve deletion confirmation, bounded re-observation, claimed-row protection, and startup-error causality. | `tests/commands/test_run.py::test_run_inline_deletes_spawn_request_when_ensure_manager_fails`; `tests/commands/test_run.py::test_reconcile_submitted_spawn_can_wait_past_reserved_claim`; `tests/core/test_ops_shared.py::test_run_adapter_routes_manager_recovery_through_shared_submission` | Generic retry/dequeue-first recovery or detached outcome handlers; rejected because the queued-to-delete-to-reconcile transition and exception cause must remain visible. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-117` | `C901` | `1` directives; raw: `C901=1` | Stale-liveness classification preserves terminal authority and the precedence of host PID/runtime evidence, stale windows, live-manager records, newer canonical service owners, and same-owner registry proof. | `tests/commands/test_status.py::test_terminal_log_status_wins_over_weak_live_host_pid`; `tests/commands/test_status.py::test_cmd_status_marks_stale_internal_service_without_owner_failed`; `tests/commands/test_status.py::test_status_tasks_treat_fresh_runtime_less_internal_service_log_as_superseded` | Separate host and service classifiers that duplicate stale/runtime facts; rejected because evidence precedence would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-118` | `C901` | `1` directives; raw: `C901=1` | Internal-service snapshots collect child, manager-log, registry, pending/reserved queue, manager-desire, and mapping-diagnostic evidence before selecting one ranked candidate per known service. | `tests/commands/test_status.py::test_status_services_prefer_live_duplicate_over_terminal_duplicate`; `tests/commands/test_status.py::test_status_services_report_pending_internal_spawn_request`; `tests/commands/test_status.py::test_status_services_report_task_monitor_external_log_diagnostics` | Additional collector orchestration beyond existing leaf helpers; rejected because completeness and the inputs to final ranking become harder to audit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-119` | `C901` | `1` directives; raw: `C901=1` | Status watch preserves monotonic log cursors, TID/status filtering before output, JSON flushing, monitor-backed waits, interrupt success, honest failure, and resource closure. | `tests/commands/test_status.py::test_watch_task_events_uses_queue_monitor` | Detached record-building and rendering helpers; rejected because cursor advancement and filter/output order are clearer in one tail loop. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-120` | `C901` | `1` directives; raw: `C901=1` | A foreground task-monitor pass validates before work, scans once, writes the selected sink before advancing a monotonic checkpoint, and returns stable summaries and errors. | `tests/commands/test_task_monitor.py::test_monitor_checkpoint_advances_after_successful_sink_write`; `tests/commands/test_task_monitor.py::test_monitor_restart_does_not_duplicate_after_checkpoint`; `tests/commands/test_task_monitor.py::test_monitor_crash_window_duplicate_has_stable_summary_id` | Sink-specific orchestration classes or checkpoint writes hidden inside sinks; rejected because transaction-like scan/write/checkpoint order must remain explicit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-121` | `C901` | `1` directives; raw: `C901=1` | Control waiting rebuilds monitors when custom/pipeline queues appear, accepts terminal ctrl-out proof, never promotes KILL ACK alone, preserves public-signal and kill-ack grace, and closes every queue. | `tests/commands/test_task_commands.py::test_await_control_surface_uses_queue_monitor`; `tests/commands/test_task_commands.py::test_await_control_surface_does_not_promote_kill_ack_to_terminal`; `tests/commands/test_task_commands.py::test_await_control_surface_accepts_terminal_ctrl_out_without_log_replay` | Helpers detaching monitor rebuilding from queue ownership or treating acknowledgements as terminal; rejected pending a dedicated transition-model plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-122` | `C901` | `1` directives; raw: `C901=1` | Bounded stdin applies one UTF-8 byte limit to buffered-byte and text-only streams, treats closed/unreadable input as empty, and decodes only after the complete bounded read. | `tests/cli/test_cli_run.py::test_cli_run_reads_stdin`; `tests/cli/test_cli_run.py::test_cli_run_rejects_oversized_piped_stdin` | Generic reader adapter; rejected because it risks changing encoding, byte counting, and exception behavior across the two stream protocols. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-123` | `C901` | `1` directives; raw: `C901=1` | Queue iteration prefers modern timestamp bounds, supports older broker signatures, reapplies exact open bounds, skips malformed rows, returns empty on best-effort open failure, and always closes the generator. | `tests/system/test_helpers.py::test_iter_queue_entries_closes_underlying_generator_on_early_close`; `tests/core/test_task_log_scanner.py::test_task_log_scanner_selects_complete_family_behind_open_prefix` | Separate API negotiation and iteration layers; rejected because generator lifetime and bound semantics would span owners. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-124` | `C901` | `1` directives; raw: `C901=1` | Process-tree termination snapshots descendants, signals leaves before root, waits boundedly, optionally kills survivors, reaps, and reports only confirmed terminated PIDs while tolerating process races. | `tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants`; `tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants`; `tests/commands/test_task_commands.py::test_stop_tasks_terminates_active_process_tree` | Split signaling, waiting, and reporting phases into remote helpers; rejected because shared process lists and leaf-before-root ordering would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-125` | `C901` | `1` directives; raw: `C901=1` | File writes preserve text/bytes exclusivity, same-directory temp creation, replacement retries, temp cleanup, and the documented fallback with permission retries. | `tests/system/test_helpers.py::TestAtomicFileWriting::test_write_file_atomically_text_success`; `tests/system/test_helpers.py::TestAtomicFileWriting::test_write_file_atomically_fallback_on_replace_error`; `tests/system/test_helpers.py::TestAtomicFileWriting::test_write_file_atomically_invalid_args` | Generic writer extraction or removal of fallback during this migration; rejected because FD/temp ownership is delicate and fallback removal changes behavior. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-126` | `C901` | `1` directives; raw: `C901=1` | Relative-duration formatting preserves sub-millisecond, millisecond, short-second, pluralization, descending-unit, rounding, and `max_units` behavior. | `tests/commands/test_status.py::test_cmd_status_text_output`; `tests/cli/test_status.py::test_status_reports_running_task_json` | One helper per duration band; rejected because a single visible threshold checklist is easier to comprehend and avoids duplicated rounding rules. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-201` | `C901` | `2` directives; raw: `C901=2` | Docker agent protocol tests preserve the prepared image identity and the ordered cancellation-to-container-kill-to-cancelled-outcome path. | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image`; `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_reports_cancel_requested_as_cancelled` | Extract shared fake Docker classes or protocol steps; rejected because each stateful fake and its assertions form one temporal proof. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-202` | `C901` | `1` directive; raw: `C901=1` | Docker provider execution preserves preparation, mount binding, runtime-handle publication, streaming, cancellation, outcome mapping, and cleanup order. | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image`; `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_reports_cancel_requested_as_cancelled` | Pass live lifecycle state among score-driven helpers; rejected pending a separate hardened execution-path plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-203` | `C901` | `2` directives; raw: `C901=2` | Work-item mounts preserve exact schema defaults and errors, required/optional reference semantics, absolute-path and filesystem-kind checks, and canonical output. | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_mounts_default_to_read_only`; `extensions/weft_docker/tests/test_agent_runner.py::test_resolve_work_item_mounts_reads_document_path_from_metadata`; `extensions/weft_docker/tests/test_agent_runner.py::test_resolve_work_item_mounts_rejects_relative_paths` | Per-field or staged validators; rejected because indexed mount context, error order, and resolved-path state would be duplicated or scattered. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-204` | `C901` | `1` directive; raw: `C901=1` | Docker command construction preserves shell-free argv order across limits, network policy, mounts, working directory, image, and inner invocation. | `tests/tasks/test_command_runner_parity.py::test_docker_command_runner_build_profile_materializes_mounts`; `extensions/weft_docker/tests/test_docker_plugin.py::test_command_runner_uses_container_workdir_without_mounting_host_workdir` | One option-appender helper per flag family; rejected because final argv precedence and completeness become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-205` | `C901` | `2` directives; raw: `C901=2` | Docker validation preserves the exact supported command and one-shot provider-agent lanes, mutual exclusions, profile materialization, descriptor/recipe requirements, mounts, preflight, and error precedence. | `extensions/weft_docker/tests/test_docker_plugin.py::test_docker_runner_accepts_one_shot_provider_cli_agent_with_recipe`; `extensions/weft_docker/tests/test_docker_plugin.py::test_docker_runner_rejects_conflicting_agent_mount_targets`; `extensions/weft_docker/tests/test_container_profiles.py::test_container_profile_materializes_defaults_paths_and_env_precedence` | Distribute checks by option family; rejected because cross-field restrictions and boundary error order would become implicit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-206` | `C901` | `2` directives; raw: `C901=2` | Docker observation preserves ordered container lookup fallbacks and a complete, stable reduction from Docker state to `RunnerRuntimeDescription`. | `extensions/weft_docker/tests/test_docker_plugin.py::test_describe_runtime_falls_back_to_container_id_when_name_lookup_misses`; `extensions/weft_docker/tests/test_docker_plugin.py::test_describe_runtime_falls_back_to_container_list_when_name_get_misses` | Separate field projectors or lookup strategies; rejected because fallback precedence and the full external-to-domain mapping would be harder to audit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-207` | `C901` | `1` directive; raw: `C901=1` | macOS sandbox validation preserves command-only, noninteractive, nonpersistent constraints plus profile, environment, platform, executable, and path preflight errors. | `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py::test_macos_sandbox_runner_requires_profile`; `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py::test_sandbox_env_passthrough_must_be_string_list` | Extract platform preflight without another caller; rejected because the boundary checklist and its error order are clearer together. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-208` | `C901` | `1` directive; raw: `C901=1` | Microsandbox option parsing preserves the accepted vocabulary, mode/target coupling, image and executable requirements, mounts, environment, limits, and all cross-field errors in one canonical value. | `extensions/weft_microsandbox/tests/test_options.py::test_unknown_option_fails`; `extensions/weft_microsandbox/tests/test_options.py::test_max_connections_zero_conflicts_with_network_allow`; `extensions/weft_microsandbox/tests/test_options.py::test_workspace_mount_requires_guest_cwd` | Split normalization by option family; rejected because cross-field validation and error precedence would span helpers. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-209` | `C901` | `1` directive; raw: `C901=1` | Django public overrides preserve JSON-copy isolation, exact metadata/spec/limits/runner mapping, merge precedence, and decorated-task host-runner restriction. | `integrations/weft_django/tests/test_weft_django.py::test_as_taskspec_for_call_applies_public_submit_overrides`; `integrations/weft_django/tests/test_weft_django.py::test_deferred_native_payload_is_snapshotted_at_registration` | Generic deep merge or per-section override helpers; rejected because the public vocabulary and cross-section precedence become hidden. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-210` | `C901` | `1` directive; raw: `C901=1` | Import parsing preserves relative-target resolution, function scope, and `TYPE_CHECKING` scope attribution in one AST visitor. | `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries`; `tests/architecture/test_import_boundaries.py::test_type_checking_runner_backedge_does_not_create_runtime_cycle` | Externalize visitor state; rejected because scope counters and edge attribution would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-211` | `C901` | `1` directive; raw: `C901=1` | Parallel manager-reuse acceptance preserves concurrent submission, terminal waits, stable convergence, and complete failure snapshots in one ordered live scenario. | `tests/cli/test_cli_run.py::test_cli_run_parallel_no_wait_adopts_active_manager`; `tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`; `tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse` | Extract protocol phases carrying results, deadlines, harness, and observations; rejected because temporal causality and diagnostics become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-212` | `C901` | `1` directive; raw: `C901=1` | A real manager process exposes the required running process title and the acceptance test performs bounded polling and reliable cleanup. | `tests/cli/test_manager_proctitle.py::test_manager_proctitle_updates_to_running` | Shared process-title polling fixture; rejected because platform guards, launched process, assertion, and cleanup form one small live protocol. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-213` | `C901` | `1` directive; raw: `C901=1` | The canonical CLI subprocess harness preserves backend-aware interpreter/environment selection, root preparation, timeout diagnostics, and output normalization. | `tests/cli/test_cli_run.py::test_cli_run_function_inline`; `tests/cli/test_cli_run.py::test_harness_wait_for_completion_reports_cancelled_task` | Split command construction and timeout handling into generic helpers; rejected because test execution policy becomes harder to audit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-214` | `C901` | `1` directive; raw: `C901=1` | Manager termination kills a descendant process tree despite trapped `SIGTERM` and performs bounded reliable cleanup. | `tests/core/test_manager.py::test_manager_terminate_children_kills_sigterm_trapping_descendant_tree` | Reuse a generic process fake; rejected because the test encodes one exact OS-level process topology and cleanup sequence. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-215` | `C901` | `1` directive; raw: `C901=1` | The MCP stdio fixture preserves framing, initialization, tool listing/calls, errors, and shutdown as one stateful wire-protocol emulator. | `tests/tasks/test_agent_execution.py::test_consumer_processes_provider_cli_with_explicit_mcp_tool_profile`; `tests/tasks/test_agent_execution.py::test_consumer_live_provider_cli_mcp_smoke` | Build a generic fixture protocol framework or extract request handlers; rejected because protocol state and sequencing would be obscured. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-216` | `C901` | `1` directive; raw: `C901=1` | Test CLI context resolution preserves precedence among explicit context, manager spec context, run spec context, init target, and resolved cwd fallback. | `tests/cli/test_cli_run.py::test_cli_run_spec_path`; `tests/cli/test_cli_init.py::test_cmd_init_accepts_in_process_config_overrides` | Separate command-specific scanners; rejected because option precedence and shared path resolution would become dispersed. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-217` | `C901` | `1` directive; raw: `C901=1` | Completion waiting preserves reordered-log scanning, terminal-event recognition, cursor progress, timeout diagnostics, and queue closure. | `tests/test_harness_registration.py::test_wait_for_completion_treats_control_stop_as_terminal_event`; `tests/test_harness_registration.py::test_wait_for_completion_timeout_includes_tid_debug_snapshot`; `tests/test_harness_registration.py::test_wait_for_completion_records_polling_stats` | Extract polling steps carrying cursor, high-watermark, and deadline state; rejected because terminal interpretation becomes non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-218` | `C901` | `2` directives; raw: `C901=2` | The long-session benchmark gives CLI/API surfaces equivalent run inputs and measures persistent work, aliases, submissions, observations, and cleanup over one explicit sequence. | `tests/cli/test_cli_run.py::test_cli_run_persistent_spec_no_wait_consumes_initial_piped_stdin`; `tests/cli/test_cli_run.py::test_cli_run_parallel_no_wait_adopts_active_manager` | Per-option argv builders or extracted benchmark phases; rejected because they add no reusable owner and hide what lies inside the measured interval. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-219` | `C901` | `1` directive; raw: `C901=1` | One deployable function target can independently or jointly exercise memory, CPU, sockets, files, output size, and duration while reliably cleaning resources. | `tests/tasks/test_task_execution.py::test_cleanup_on_exit_process_target`; `tests/tasks/test_runner.py::test_function_timeout_reports_timeout_when_no_result_is_ready` | Split each load dimension into separate targets; rejected because composition is the fixture's purpose and would complicate function-target deployment. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-220` | `C901` | `1` directive; raw: `C901=1` | A fatal watcher-owner exit releases every queued synchronous topology mutator rather than deadlocking callers. | `tests/tasks/test_multiqueue_watcher.py::test_owner_fatal_exit_signals_every_queued_mutator` | Extract event, waiter, or thread setup into shared concurrency fixtures; rejected because the deterministic schedule and causality would be hidden. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-221` | `C901` | `1` directive; raw: `C901=1` | Closing an AgentSession releases process and queue handles without enqueuing a late stop request. | `tests/tasks/test_runner.py::test_agent_session_close_releases_multiprocessing_handles` | Share generic fake process and queue classes; rejected because locally visible cleanup calls and forbidden late writes are the proof. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-222` | `C901` | `1` directive; raw: `C901=1` | TaskMonitor maintenance workers receive isolated snapshots/facades, cannot mutate reactor-owned state, and close every worker-owned resource. | `tests/tasks/test_task_monitor.py::test_task_monitor_worker_local_snapshot_owns_mutable_runtime_resources` | Split exhaustive state and identity assertions; rejected because omissions become easier and the ownership comparison becomes non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-223` | `C901` | `1` directive; raw: `C901=1` | DOM-15 fixtures cover every task class and `+P`, low-class fixtures carry required negative facts, and cumulative/subsume wording remains present. | Real invocation: `bin/check-dom15-fixtures`; governing contract: `docs/agent-context/decision-hierarchy.md` [DOM-15] | One checker per governance condition; rejected because a single finite checklist is clearer and needs no dispatch layer. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-224` | `C901` | `1` directive; raw: `C901=1` | Coalescing evidence reports whether SHAs and retrieval cues resolve locally or name retrievable foreign evidence, including local-only ancestry, with honest exits. | Real invocation: `bin/coalesce-check`; governing evidence: `docs/coalescing.md` | Separate parse, reachability, attribution, and report owners; rejected because they share the same evidence positions, repositories, and resolution state. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-225` | `C901` | `1` directive; raw: `C901=1` | The Postgres test runner preserves mode validation, dependency checks, temporary container setup, pytest invocation, cleanup, and honest exit propagation. | `tests/system/test_pytest_pg_script.py::test_build_pytest_command_defaults_to_full_tests_tree`; `tests/system/test_pytest_pg_script.py::test_start_postgres_container_keeps_container_until_cleanup` | Further split `main` after existing Docker helpers; rejected because it would scatter the operator-visible lifecycle without a new owner. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-226` | `C901` | `2` directives; raw: `C901=2` | Release policy preserves safe local/remote tag decisions plus dirty-tree refusal, extension planning, prechecks, commit/tag/publish ordering, and dry-run behavior. | `tests/system/test_release_script.py::test_plan_tag_action_rejects_existing_tag_on_different_commit`; `tests/system/test_release_script.py::test_main_dry_run_retags_remote_when_requested`; `tests/system/test_release_script.py::test_build_precheck_commands_cover_release_gate_and_quality_gates` | Split the decision table or refactor release orchestration during lint activation; rejected because precedence would hide and mutation/rollback risk requires a separate hardened plan. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-227` | `C901` | `1` directive; raw: `C901=1` | Import-cycle detection preserves deterministic strongly connected components, including self-cycles, from one local Tarjan traversal. | `tests/architecture/test_import_boundaries.py::test_weft_has_no_eager_import_cycles`; `tests/architecture/test_import_boundaries.py::test_restored_runner_function_import_cycle_is_detected` | Extract index, stack, or low-link state; rejected because the algorithm becomes harder to verify and reason about. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-228` | `C901` | `1` directive; raw: `C901=1` | One repository-wide edge stream enforces core, commands, CLI, and client direction plus Rich and Typer ownership with complete violation reporting. | `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries` | Split layer checks into separately executed tests; rejected because traversal is duplicated and partial enforcement becomes easier. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-229` | `C901` | `1` directive; raw: `C901=1` | Static imports, aliases, attributes, and dynamic `getattr` reaches remain on exported SimpleBroker surfaces under one alias interpretation. | `tests/architecture/test_import_boundaries.py::test_simplebroker_surface_guard_fires_for_import_forms`; `tests/architecture/test_import_boundaries.py::test_weft_uses_only_supported_simplebroker_surfaces` | Separate scanners per syntax form; rejected because alias state would be duplicated or interpreted inconsistently. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-230` | `C901` | `1` directive; raw: `C901=1` | Recursive CLI JSON registration preserves nested task/manager TIDs, scoped runtime PIDs, caller PIDs, and manager-role context. | `tests/test_harness_registration.py::test_register_from_json_routes_manager_tids_to_worker_tracking` | Separate dict/list walkers or role-specific traversals; rejected because recursion and inherited role context would be duplicated. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-231` | `C901` | `1` directive; raw: `C901=1` | A concurrent active heartbeat refresh cannot overwrite a superseded manager registry record. | `tests/core/test_manager.py::test_manager_active_heartbeat_race_preserves_superseded_record` | Replace the local interleaving queue with a generic queue fake; rejected because the decisive race schedule would be hidden. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-232` | `C901` | `4` directives; raw: `C901=4` | Each provider CLI fixture preserves its supported argv/input/output grammar, tool behavior, failures, framing, and deliberate provider differences. | `tests/tasks/test_agent_execution.py::test_task_runner_executes_provider_cli_agent_successfully`; `tests/core/test_provider_cli_backend.py::test_provider_cli_runtime_executes_one_shot_request`; `tests/core/test_provider_cli_session_backend.py::test_provider_cli_session_continues_across_turns` | Build cross-provider option handlers or a generic synthetic CLI framework; rejected because provider-specific parsed state and differences become indirect. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-233` | `C901` | `1` directive; raw: `C901=1` | Harness shutdown preserves manager-first signalling, task discovery, graceful-to-force escalation, PID settlement, and optional registry draining without task fan-out to worker identities. | `tests/test_harness_registration.py::test_harness_stop_active_managers_stops_registered_task_and_manager_tids`; `tests/test_harness_registration.py::test_harness_stop_active_managers_does_not_fan_out_worker_tid_as_task`; `tests/test_harness_registration.py::test_harness_stop_active_managers_does_not_fan_out_in_process_task_tid` | Extract escalation phases carrying issued-stop sets and live PID state; rejected because shutdown order and idempotence become implicit. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-234` | `C901` | `1` directive; raw: `C901=1` | Preserve-database cleanup uses linked deadlines to stop lingering work, reach process quiescence, prove database release, and report exact residual resources without deleting the database. | `tests/test_harness_registration.py::test_harness_cleanup_preserve_database_waits_for_database_release`; `tests/test_harness_registration.py::test_harness_cleanup_preserve_database_extends_windows_release_budget`; `tests/test_harness_registration.py::test_harness_cleanup_preserve_database_raises_if_database_stays_locked` | Split polling, stop escalation, and final diagnosis; rejected because deadlines could reset and cleanup precedence would span helpers. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-235` | `C901` | `1` directive; raw: `C901=1` | Database, WAL, and SHM releasability probes safely rename candidates and restore every moved file, surfacing restoration failure. | `tests/test_harness_registration.py::test_locked_database_cleanup_accepts_windows_short_path`; `tests/test_harness_registration.py::test_locked_database_cleanup_uses_configured_artifacts_when_candidate_removed` | Separate candidate mutation from restoration; rejected because transactional rollback ownership would be split. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-236` | `C901` | `1` directive; raw: `C901=1` | Dead-producer and sealed-channel evidence is reduced before a stale resource monitor can replace it with a limit result. | `tests/tasks/test_runner.py::test_agent_session_does_not_poll_limits_after_producer_exit` | Share generic process, receiver, or monitor fakes; rejected because the exact same-turn observation order is the proof. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-237` | `C901` | `1` directive; raw: `C901=1` | Persistent cleanup cannot extend the absolute drain deadline accepted by the AgentSession terminal protocol. | `tests/tasks/test_runner.py::test_session_stop_effect_cannot_reset_absolute_drain_deadline` | Extract fake-clock or session steps; rejected because clock advancement and deadline causality would become non-local. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-238` | `C901` | `1` directive; raw: `C901=1` | A production-shaped terminal backlog either retires within three cycles or reports the exact binding predicate with representative family evidence. | `tests/tasks/test_task_monitor.py::test_retirement_backlog_identifies_binding_stage` | Split workload seeding, monitor driving, and per-arm diagnosis; rejected because workload shape and diagnostic interpretation could drift. | Task 2 clean outside review PASS; owner approved 2026-08-05. |

Global raw-`noqa` inventory: `C901=150`, `E402=22`, `F401=5`

<!-- BEGIN GENERATED RUFF SUPPRESSION INDEX -->
| Group | Locations | Directives | Raw diagnostics |
|-------|-----------|-----------:|-----------------|
| `RUFF-SUP-001` | `weft/core/agents/provider_cli/container_runtime.py::resolve_provider_container_runtime` | 1 | `C901=1` |
| `RUFF-SUP-002` | `weft/core/agents/provider_cli/registry.py::CodexProvider.resolve_options`; `weft/core/agents/provider_cli/registry.py::CodexProvider.validate_options` | 2 | `C901=2` |
| `RUFF-SUP-003` | `weft/core/agents/provider_cli/registry.py::CodexProvider.build_invocation`; `weft/core/agents/provider_cli/registry.py::CodexProvider.build_session_invocation` | 2 | `C901=2` |
| `RUFF-SUP-004` | `weft/core/endpoints.py::list_resolved_endpoints` | 1 | `C901=1` |
| `RUFF-SUP-005` | `weft/core/manager.py::Manager._prune_expired_manager_registry_entries`; `weft/core/manager.py::Manager._prune_older_self_registry_entries`; `weft/core/manager.py::Manager._register_manager`; `weft/core/manager.py::Manager._self_registry_status_timestamp`; `weft/core/manager.py::Manager._unregister_manager` | 5 | `C901=5` |
| `RUFF-SUP-006` | `weft/core/manager.py::Manager._manager_record_liveness` | 1 | `C901=1` |
| `RUFF-SUP-007` | `weft/core/manager.py::Manager._active_dispatch_manager_records`; `weft/core/manager.py::Manager._maybe_yield_leadership` | 2 | `C901=2` |
| `RUFF-SUP-008` | `weft/core/manager.py::Manager._child_has_exited`; `weft/core/manager.py::Manager._child_terminal_proof_visible` | 2 | `C901=2` |
| `RUFF-SUP-009` | `weft/core/manager.py::Manager._cleanup_children`; `weft/core/manager.py::Manager._terminate_children` | 2 | `C901=2` |
| `RUFF-SUP-010` | `weft/core/manager.py::Manager._apply_spawn_reserved_policy`; `weft/core/manager.py::Manager._cleanup_stale_internal_reserved_queues` | 2 | `C901=2` |
| `RUFF-SUP-011` | `weft/core/manager.py::Manager._handle_work_message` | 1 | `C901=1` |
| `RUFF-SUP-012` | `weft/core/manager.py::Manager._managed_service_convergence_active_reasons`; `weft/core/manager.py::Manager._reconcile_managed_services`; `weft/core/manager.py::Manager._service_candidate_from_service_owner_record`; `weft/core/manager.py::Manager._terminate_duplicate_service_candidates` | 4 | `C901=4` |
| `RUFF-SUP-013` | `weft/core/manager.py::Manager._build_autostart_spawn_payload` | 1 | `C901=1` |
| `RUFF-SUP-014` | `weft/core/manager.py::Manager._process_reactor_turn` | 1 | `C901=1` |
| `RUFF-SUP-015` | `weft/core/manager_runtime.py::_snapshot_registry` | 1 | `C901=1` |
| `RUFF-SUP-016` | `weft/core/manager_runtime.py::_manager_record_diagnostic` | 1 | `C901=1` |
| `RUFF-SUP-017` | `weft/core/manager_runtime.py::_await_manager_stop_confirmation`; `weft/core/manager_runtime.py::_mark_manager_stopped`; `weft/core/manager_runtime.py::_stop_manager` | 3 | `C901=3` |
| `RUFF-SUP-018` | `weft/core/manager_runtime.py::_start_manager` | 1 | `C901=1` |
| `RUFF-SUP-019` | `weft/core/manager_services.py::reduce_managed_service_state` | 1 | `C901=1` |
| `RUFF-SUP-020` | `weft/core/monitor/lifetime_report.py::_taskspec_from_collation` | 1 | `C901=1` |
| `RUFF-SUP-021` | `weft/core/monitor/runtime.py::TaskMonitorRuntimeConfig.from_config` | 1 | `C901=1` |
| `RUFF-SUP-022` | `weft/core/monitor/store.py::MonitorStore.record_task_log_updates` | 1 | `C901=1` |
| `RUFF-SUP-023` | `weft/core/monitor/task_log_collation.py::collate_next_task_log_group` | 1 | `C901=1` |
| `RUFF-SUP-024` | `weft/core/monitor/task_monitor.py::TaskMonitor._close_worker_local_resources`; `weft/core/monitor/task_monitor.py::TaskMonitor._worker_local_monitor_clone` | 2 | `C901=2` |
| `RUFF-SUP-025` | `weft/core/monitor/task_monitor.py::TaskMonitor._ingest_retained_task_log_rows`; `weft/core/monitor/task_monitor.py::TaskMonitor._recover_pre_checkpoint_task_log_rows` | 2 | `C901=2` |
| `RUFF-SUP-026` | `weft/core/monitor/task_monitor.py::TaskMonitor._emit_monitor_store_summaries` | 1 | `C901=1` |
| `RUFF-SUP-027` | `weft/core/monitor/task_monitor.py::TaskMonitor._run_terminal_control_cleanup_slice` | 1 | `C901=1` |
| `RUFF-SUP-028` | `weft/core/monitor/task_monitor.py::TaskMonitor._trim_manager_task_spawned_task_log_rows` | 1 | `C901=1` |
| `RUFF-SUP-029` | `weft/core/outbox.py::process_outbox_message` | 1 | `C901=1` |
| `RUFF-SUP-030` | `weft/core/pipelines.py::_merge_stage_defaults` | 1 | `C901=1` |
| `RUFF-SUP-031` | `weft/core/pruning/apply.py::apply_exact_prune_candidates` | 1 | `C901=1` |
| `RUFF-SUP-032` | `weft/core/pruning/retention.py::_task_log_candidates` | 1 | `C901=1` |
| `RUFF-SUP-033` | `weft/core/pruning/runtime.py::_manager_candidates` | 1 | `C901=1` |
| `RUFF-SUP-034` | `weft/core/runners/host.py::HostTaskRunner._run_one_shot_terminal_handoff` | 1 | `C901=1` |
| `RUFF-SUP-035` | `weft/core/runners/host.py::HostTaskRunner.start_agent_session`; `weft/core/runners/host.py::HostTaskRunner.start_session` | 2 | `C901=2` |
| `RUFF-SUP-036` | `weft/core/runners/subprocess_runner.py::run_monitored_subprocess` | 1 | `C901=1` |
| `RUFF-SUP-037` | `weft/core/runners/subprocess_runner.py::_start_stream_reader` | 2 | `C901=2` |
| `RUFF-SUP-038` | `weft/core/state_machines.py::StateMachine._validate` | 1 | `C901=1` |
| `RUFF-SUP-039` | `weft/core/task_evidence.py::known_tid_evidence` | 1 | `C901=1` |
| `RUFF-SUP-040` | `weft/core/tasks/base.py::BaseTask._validate_reactor_topology` | 1 | `C901=1` |
| `RUFF-SUP-041` | `weft/core/tasks/base.py::BaseTask.run_until_stopped` | 1 | `C901=1` |
| `RUFF-SUP-042` | `weft/core/tasks/consumer.py::Consumer._ensure_outcome_ok` | 1 | `C901=1` |
| `RUFF-SUP-043` | `weft/core/tasks/heartbeat.py::HeartbeatTask._emit_due_registrations` | 1 | `C901=1` |
| `RUFF-SUP-044` | `weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_finalize_session` | 1 | `C901=1` |
| `RUFF-SUP-045` | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._apply_pending_topology_mutations`; `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._apply_topology_mutation_on_owner` | 2 | `C901=2` |
| `RUFF-SUP-046` | `weft/core/tasks/service.py::ServiceTask._stop_service_worker` | 1 | `C901=1` |
| `RUFF-SUP-047` | `weft/core/tasks/sessions.py::AgentSession.wait_ready` | 1 | `C901=1` |
| `RUFF-SUP-048` | `weft/core/tasks/sessions.py::AgentSession.execute` | 1 | `C901=1` |
| `RUFF-SUP-049` | `weft/core/tasks/sessions.py::AgentSession.terminate` | 1 | `C901=1` |
| `RUFF-SUP-050` | `weft/core/taskspec/model.py::resolve_taskspec_payload` | 1 | `C901=1` |
| `RUFF-SUP-051` | `weft/core/taskspec/model.py::AgentSection.validate_runtime_constraints` | 1 | `C901=1` |
| `RUFF-SUP-052` | `weft/core/taskspec/model.py::SpecSection.validate_target` | 1 | `C901=1` |
| `RUFF-SUP-053` | `weft/core/taskspec/model.py::StateSection.validate_state_consistency` | 1 | `C901=1` |
| `RUFF-SUP-054` | `weft/core/taskspec/model.py::TaskSpec._validate_strict_requirements` | 1 | `C901=1` |
| `RUFF-SUP-055` | `weft/core/taskspec/run_input.py::parse_declared_option_args` | 1 | `C901=1` |
| `RUFF-SUP-056` | `weft/core/monitor/task_monitor.py::TaskMonitor._run_reserved_cleanup_slice` | 1 | `C901=1` |
| `RUFF-SUP-057` | `weft/core/monitor/task_monitor.py::TaskMonitor._recover_orphan_task_log_rows` | 1 | `C901=1` |
| `RUFF-SUP-058` | `weft/core/monitor/task_monitor.py::TaskMonitor._coalesce_and_delete_dead_task_log_rows_for_tids` | 1 | `C901=1` |
| `RUFF-SUP-059` | `weft/core/pruning/runtime.py::_endpoint_candidates` | 1 | `C901=1` |
| `RUFF-SUP-102` | `weft/cli/app.py::task_status` | 1 | `C901=1` |
| `RUFF-SUP-104` | `weft/commands/_load_support.py::ImportReport.format_preview` | 1 | `C901=1` |
| `RUFF-SUP-105` | `weft/commands/_load_support.py::_parse_import_file` | 1 | `C901=1` |
| `RUFF-SUP-106` | `weft/commands/_result_wait.py::await_one_shot_result` | 1 | `C901=1` |
| `RUFF-SUP-107` | `weft/commands/events.py::iter_task_realtime_events` | 1 | `C901=1` |
| `RUFF-SUP-108` | `weft/commands/result.py::_await_result_materialization` | 1 | `C901=1` |
| `RUFF-SUP-109` | `weft/commands/result.py::_await_single_result` | 1 | `C901=1` |
| `RUFF-SUP-110` | `weft/commands/result.py::cmd_result` | 1 | `C901=1` |
| `RUFF-SUP-111` | `weft/commands/run.py::_run_interactive_session` | 1 | `C901=1` |
| `RUFF-SUP-112` | `weft/commands/run.py::render_run_execution_result` | 1 | `C901=1` |
| `RUFF-SUP-113` | `weft/commands/run.py::_execute_inline` | 1 | `C901=1` |
| `RUFF-SUP-114` | `weft/commands/run.py::execute_run` | 1 | `C901=1` |
| `RUFF-SUP-115` | `weft/commands/submission.py::apply_submit_overrides` | 1 | `C901=1` |
| `RUFF-SUP-116` | `weft/commands/submission.py::ensure_manager_after_submission` | 1 | `C901=1` |
| `RUFF-SUP-117` | `weft/commands/system.py::_stale_liveness_reason` | 1 | `C901=1` |
| `RUFF-SUP-118` | `weft/commands/system.py::_collect_internal_service_snapshots` | 1 | `C901=1` |
| `RUFF-SUP-119` | `weft/commands/system.py::_watch_task_events` | 1 | `C901=1` |
| `RUFF-SUP-120` | `weft/commands/task_monitor.py::run_task_monitor` | 1 | `C901=1` |
| `RUFF-SUP-121` | `weft/commands/tasks.py::_await_control_surface` | 1 | `C901=1` |
| `RUFF-SUP-122` | `weft/helpers/__init__.py::read_limited_stdin` | 1 | `C901=1` |
| `RUFF-SUP-123` | `weft/helpers/__init__.py::iter_queue_entries` | 1 | `C901=1` |
| `RUFF-SUP-124` | `weft/helpers/__init__.py::terminate_process_tree` | 1 | `C901=1` |
| `RUFF-SUP-125` | `weft/helpers/__init__.py::write_file_atomically` | 1 | `C901=1` |
| `RUFF-SUP-126` | `weft/helpers/__init__.py::_format_duration` | 1 | `C901=1` |
| `RUFF-SUP-201` | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_reports_cancel_requested_as_cancelled`; `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image` | 2 | `C901=2` |
| `RUFF-SUP-202` | `extensions/weft_docker/weft_docker/agent_runner.py::DockerProviderCLIRunner.run_with_hooks` | 1 | `C901=1` |
| `RUFF-SUP-203` | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_work_item_mounts`; `extensions/weft_docker/weft_docker/agent_runner.py::_resolve_work_item_mounts` | 2 | `C901=2` |
| `RUFF-SUP-204` | `extensions/weft_docker/weft_docker/plugin.py::DockerCommandRunner._build_docker_command` | 1 | `C901=1` |
| `RUFF-SUP-205` | `extensions/weft_docker/weft_docker/plugin.py::DockerRunnerPlugin._validate_agent_taskspec`; `extensions/weft_docker/weft_docker/plugin.py::DockerRunnerPlugin.validate_taskspec` | 2 | `C901=2` |
| `RUFF-SUP-206` | `extensions/weft_docker/weft_docker/plugin.py::_describe_runtime`; `extensions/weft_docker/weft_docker/plugin.py::_lookup_container` | 2 | `C901=2` |
| `RUFF-SUP-207` | `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py::MacOSSandboxRunnerPlugin.validate_taskspec` | 1 | `C901=1` |
| `RUFF-SUP-208` | `extensions/weft_microsandbox/weft_microsandbox/_options.py::parse_options` | 1 | `C901=1` |
| `RUFF-SUP-209` | `integrations/weft_django/weft_django/client.py::_apply_taskspec_payload_overrides` | 1 | `C901=1` |
| `RUFF-SUP-210` | `tests/architecture/test_import_boundaries.py::_parse_import_edges` | 1 | `C901=1` |
| `RUFF-SUP-211` | `tests/cli/test_cli_run.py::_run_parallel_manager_reuse_cycle` | 1 | `C901=1` |
| `RUFF-SUP-212` | `tests/cli/test_manager_proctitle.py::test_manager_proctitle_updates_to_running` | 1 | `C901=1` |
| `RUFF-SUP-213` | `tests/conftest.py::run_cli` | 1 | `C901=1` |
| `RUFF-SUP-214` | `tests/core/test_manager.py::test_manager_terminate_children_kills_sigterm_trapping_descendant_tree` | 1 | `C901=1` |
| `RUFF-SUP-215` | `tests/fixtures/mcp_stdio_fixture.py::main` | 1 | `C901=1` |
| `RUFF-SUP-216` | `tests/helpers/test_backend.py::cli_context_root` | 1 | `C901=1` |
| `RUFF-SUP-217` | `tests/helpers/weft_harness.py::WeftTestHarness.wait_for_completion` | 1 | `C901=1` |
| `RUFF-SUP-218` | `tests/long_session_surface_benchmark.py::CliSurface.run_task`; `tests/long_session_surface_benchmark.py::_run_long_session` | 2 | `C901=2` |
| `RUFF-SUP-219` | `tests/tasks/process_target.py::run_task` | 1 | `C901=1` |
| `RUFF-SUP-220` | `tests/tasks/test_multiqueue_watcher.py::test_owner_fatal_exit_signals_every_queued_mutator` | 1 | `C901=1` |
| `RUFF-SUP-221` | `tests/tasks/test_runner.py::test_agent_session_close_releases_multiprocessing_handles` | 1 | `C901=1` |
| `RUFF-SUP-222` | `tests/tasks/test_task_monitor.py::test_task_monitor_worker_local_snapshot_owns_mutable_runtime_resources` | 1 | `C901=1` |
| `RUFF-SUP-223` | `bin/check-dom15-fixtures::check` | 1 | `C901=1` |
| `RUFF-SUP-224` | `bin/coalesce-check::main` | 1 | `C901=1` |
| `RUFF-SUP-225` | `bin/pytest-pg::main` | 1 | `C901=1` |
| `RUFF-SUP-226` | `bin/release.py::main`; `bin/release.py::plan_tag_action` | 2 | `C901=2` |
| `RUFF-SUP-227` | `tests/architecture/test_import_boundaries.py::_strongly_connected_components` | 1 | `C901=1` |
| `RUFF-SUP-228` | `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries` | 1 | `C901=1` |
| `RUFF-SUP-229` | `tests/architecture/test_import_boundaries.py::_simplebroker_surface_violations` | 1 | `C901=1` |
| `RUFF-SUP-230` | `tests/conftest.py::_register_from_json` | 1 | `C901=1` |
| `RUFF-SUP-231` | `tests/core/test_manager.py::test_manager_active_heartbeat_race_preserves_superseded_record` | 1 | `C901=1` |
| `RUFF-SUP-232` | `tests/fixtures/provider_cli_fixture.py::_run_claude`; `tests/fixtures/provider_cli_fixture.py::_run_codex`; `tests/fixtures/provider_cli_fixture.py::_run_gemini_or_qwen`; `tests/fixtures/provider_cli_fixture.py::_run_opencode` | 4 | `C901=4` |
| `RUFF-SUP-233` | `tests/helpers/weft_harness.py::WeftTestHarness._stop_active_managers` | 1 | `C901=1` |
| `RUFF-SUP-234` | `tests/helpers/weft_harness.py::WeftTestHarness._cleanup_preserving_database` | 1 | `C901=1` |
| `RUFF-SUP-235` | `tests/helpers/weft_harness.py::WeftTestHarness._database_files_releasable` | 1 | `C901=1` |
| `RUFF-SUP-236` | `tests/tasks/test_runner.py::test_agent_session_does_not_poll_limits_after_producer_exit` | 1 | `C901=1` |
| `RUFF-SUP-237` | `tests/tasks/test_runner.py::test_session_stop_effect_cannot_reset_absolute_drain_deadline` | 1 | `C901=1` |
| `RUFF-SUP-238` | `tests/tasks/test_task_monitor.py::test_retirement_backlog_identifies_binding_stage` | 1 | `C901=1` |
<!-- END GENERATED RUFF SUPPRESSION INDEX -->

Promotion baseline: `3ada6ccb2c3c419119c44ed4426818aa43fc4abf`.

## Current Coverage [TS-1]

- `tests/cli/` covers subprocess CLI behavior and operator-visible output.
- `tests/commands/` covers command-layer helpers, including direct handler paths,
  queue/output boundaries, and command-control reducer tables such as
  `weft/commands/control_convergence.py`.
- `tests/context/` covers context discovery and backend-aware project setup.
- `tests/core/` covers manager behavior, pipelines, agent/runtime code,
  provider CLI adapters, target execution helpers, and related validation
  surfaces. Pure reducer helpers such as `weft/core/state_machines.py` are
  covered here with table tests that assert structural reachability,
  transition-ID coverage, state coverage, and action coverage. Focused
  property tests also cover pure queue-name classification and read-only task
  evidence queue fallback helpers.
  Terminal handoff coverage pairs the full pure state/event table with real
  spawn/IPC examples. It includes both `outcome -> exit` and
  `exit -> outcome`, channel seal without outcome, transport/serialization
  failure, timeout/result orderings, every non-empty same-turn observation
  subset, abrupt child exit, persistent session error-then-exit, and the public
  CLI fast-function and stored-spec regressions. Installed-workflow coverage
  invokes the environment's `weft` console script from a fresh initialized
  external directory with no test-added `PYTHONPATH`; it covers a
  standard-library function, a local module before and after manager reuse, a
  stored spec, and no-wait/result collection. Preloaded queues, target sleeps,
  retry-only assertions, and `python -m` test adapters are not substitutes for
  those paths.
- `tests/specs/` covers spec-level invariants and cross-surface contracts. This
  tree already includes focused subdirectories such as
  `manager_architecture/`, `message_flow/`, `quick_reference/`,
  `resource_management/`, and `taskspec/`, plus root-level guard tests like
  `test_command_queue_seam.py`, `test_plan_metadata.py`, and
  `test_test_audit_policy.py`.
- `tests/system/` holds repository-level checks for constants, helper behavior,
  backend test plumbing, and release-script invariants. It also contains pure
  property tests for finite configuration-parser boundaries and a live parity
  guard between default-backed environment-loader keys and explicit
  override-normalizer branches.
- `tests/tasks/` covers execution, reservation flow, control messages, process
  titles, observability, interactive behavior, pipeline runtime, and
  task-endpoint behavior. Real queue tests verify custom inbox, outbox, and
  control routing while the reserved lane remains TID-derived.
- `tests/taskspec/` covers TaskSpec validation, immutability, defaults, and
  state transitions. Property tests supplement the examples for generated
  TaskSpec payload resolution, immutable `spec`/`io` sections, resource-limit
  validation, metric peaks, and timestamp coherence. Generated transition
  sequences include one guaranteed-legal prefix so invariant assertions cannot
  pass without exercising a transition.
- `tests/helpers/` and `tests/fixtures/` provide shared harness, backend, and
  scenario setup for the above suites. They are support code, not their own
  test contract.
- `tests/test_harness_registration.py` is a root-level guard for harness
  cleanup and registration behavior.

## What Is Not Canonical [TS-2]

- There is no dedicated `tests/integration/` tree yet. Integration-style
  coverage already lives inside the existing CLI, command, core, task, and
  spec suites.
- There is no dedicated `tests/performance/` tree yet. Current performance work
  is in the dev-only benchmark modules under `tests/`, but those modules are not
  part of the canonical pytest contract.
- There is no dedicated `tests/property/` tree yet. Property-style checks remain
  embedded in normal pytest modules where they are needed.
- Property tests are not the proof mechanism for live Manager, Consumer,
  SimpleBroker reservation, process execution, or wall-clock lifecycle
  behavior. Those paths remain covered by deterministic table tests and real
  harness-backed examples.
- Deferred test surfaces stay in the companion planned doc instead of being
  mixed into this canonical file.

## Related Plans

- [`docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`](../plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md)
- [`docs/plans/2026-08-01-terminal-handoff-reducer-plan.md`](../plans/2026-08-01-terminal-handoff-reducer-plan.md)
- [`docs/plans/2026-07-29-deduplication-and-test-integrity-plan.md`](../plans/2026-07-29-deduplication-and-test-integrity-plan.md)
- [`docs/plans/2026-06-18-hypothesis-property-testing-plan.md`](../plans/2026-06-18-hypothesis-property-testing-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)

## Related Documents

- [08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md)
- [07-System_Invariants.md](07-System_Invariants.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [Internal State Machine Helper Plan](../plans/2026-05-13-internal-state-machine-helper-plan.md)
