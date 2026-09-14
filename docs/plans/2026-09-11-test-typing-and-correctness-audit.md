# Test Typing and Correctness Audit

Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-3]
Superseded by: none

Class: 3+P (effective 5): suite-wide typing and a stronger static-analysis gate. Hardening: N/A - no production execution or public contract changes.

## Goal

Enable mypy over all Python in tests, give tests meaningful types, and review every test for useful correctness evidence. Preserve or improve correctness coverage while correcting weak assertions and eliminating proved redundant or harmful checks. Record each substantive finding and its disposition.

## Source Documents and Baseline

Baseline: a1e7d496437545579985f63d2fa130666b64a162 plus the pre-existing worktree edits captured in /tmp/weft-preexisting-tests-typing.patch. Preserve all existing edits. Governing behavior is in docs/specifications/08-Testing_Strategy.md [TS-0], [TS-3] and the domain spec cited by each test. Read matching production code before assessing assertions. Read docs/agent-context/runbooks/testing-patterns.md and review-loops-and-agent-bootstrap.md.

## Outcomes

- [x] All tests and test helpers are in the mypy gate with existing strict settings.
- [x] Tests, fixtures, callbacks, and helpers have accurate annotations; no broad Any, ignore-errors, or untyped-def exemptions to make the gate pass.
- [x] Every test module receives a correctness review with a recorded disposition and coverage rationale for changes/removals.
- [x] Focused tests, full suite, lint, typing, and independent review supply evidence.

## Proposed Spec Delta

Promotion strategy A: add a paragraph to 08-Testing_Strategy.md [TS-3] documenting the mypy scope including tests and the existing strict definition checking. Type-invalid input tests retain deliberately invalid runtime values through narrow documented typing escapes, never blanket suppression. No product behavior changes. Add the reciprocal plan backlink. Review exact text before promotion.

Proposed text: "The mypy gate includes all Python modules under `tests/`, including fixtures, helpers, and development benchmark scripts, alongside the existing production and repository-tool scopes. Test functions and fixtures declare complete parameter and return types under the repository's existing strict definition checks. Deliberately invalid inputs remain runtime validation tests; any necessary typing escape is local to the invalid value and explains the boundary under test. Directory-wide test exclusions and test-wide disabling of definition or body checking are not permitted."

## Ownership and Work Slices

1. Root captures baseline mypy diagnostics, collection and coverage; owns pyproject.toml, gate wiring/documentation, tests/conftest.py, tests/helpers, tests/fixtures, and all test subtrees not assigned below. Existing test helper types remain the shared source. No new production abstractions.
2. Agent core owns tests/core recursively: annotate and review every test; read corresponding code/spec; record findings and per-module audit coverage in /tmp/weft-audit-core.md.
3. Agent tasks owns tests/tasks recursively with the same obligations, report /tmp/weft-audit-tasks.md.
4. Agent adapters owns tests/cli and tests/commands recursively with the same obligations, report /tmp/weft-audit-adapters.md.
5. Root integrates findings into this plan, runs all gates, and obtains independent cross-slice review. Agents may not delegate further or modify shared files. Request shared helper changes from root. Do not commit, push, or overwrite unrelated edits.

## Review Criteria and Invariants

For every test identify the production/tooling behavior it proves, whether the oracle is independent, whether assertions can pass if that behavior is broken, and whether coupling to internals is essential. Tests of explicitly required tooling contracts can be valuable; AST/source-text or inventory tests must justify the real failure they prevent. Delete only when equivalent stronger coverage is identified or no required behavior exists; otherwise strengthen the oracle. Preserve invalid-input cases, parameterized edges, real broker/process tests, cleanup, forward-only lifecycle, queue names and immutable TaskSpecs. Do not change production behavior to satisfy mypy. Prefer actual fixture types, Protocol for real callable interfaces, narrowing, TypedDict for stable shapes, and Mock types for mocks. Any is acceptable only at genuinely dynamic boundaries, with explanation. No new dependencies without demonstrating necessity and discussing with the user. No file deletions without explicit approval; redundant individual tests may be removed within this audit's authorized scope.

## Verification

Baseline failure: full mypy against a temporary copy of pyproject.toml with only the tests exclusion removed; output /tmp/weft-test-mypy-baseline.txt. This is the failing-first proof for typing. Collect test IDs and baseline coverage before behavioral edits. Annotation-only changes use unchanged tests plus mypy as correction evidence; weakened assertions need a concrete counterexample or mutation proof and a strengthened replacement.

Load .envrc; use .venv/bin binaries. Run focused tests on each edited slice with bounded xdist workers; root owns the full run to avoid overlapping heavy suites. Final gates: mypy over weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox; ruff check .; pytest (including slow tests where feasible), plan metadata and spec hygiene; compare production line/branch coverage and enumerate justified removed tests. CI and release/local mypy commands must include tests. Keep formatter ownership explicit: format only edited Python files. Live external-platform omissions must be reported precisely.

## Deviation Log

- User approved types-PyYAML as a development dependency; added to the existing dev extra and lockfile.
- Independent cross-reviews found additional oracle defects in root/system/CLI suites; implemented within the original test-only scope.
- The worktree had substantial existing edits and continued receiving external production changes. Those changes are preserved and explicitly separated from this audit in validation conclusions.

## Execution and Review Log

- Initial self-review: ownership is disjoint; shared helpers are root-owned; invalid inputs must not be replaced by valid data merely to satisfy typing. Residual risk is the large dirty baseline and live process tests. Independent plan review pending.
- Available reviewers: native same-family subagents; different-family review availability will be checked before final review.

- Independent plan review: core agent PASS; accepted clarification that before/after coverage uses identical backend/markers, and measured coverage does not replace semantic oracle review.
- Spec promotion: exact reviewed [TS-3] paragraph applied against a1e7d496437545579985f63d2fa130666b64a162 plus the recorded initial worktree. Product specs unchanged by this slice.

## Detailed Audit Appendices

These ledgers record every reviewed module and the rationale for deletions, replacements and retained structural checks. Counts are definitions at review time, not expanded cases. Final verification below supersedes intermediate pending notes. Independent CLI recommendations were evaluated rather than accepted wholesale: nontrivial helper recovery/cleanup tests remain; the historical manager-reuse flag discussion did not authorize unrelated production changes.

### Root audit

### Root correctness audit

Scope: every test definition in the remaining architecture, liveness, specs, system, root and helper test modules. Context/TaskSpec bodies received a separate complete review recorded below. All test helpers, fixtures and development benchmark scripts are in mypy. Counts refer to definitions, not parameterized cases. This is a test-by-test oracle review, not mutation testing every case.

#### Implemented findings

- `test_lifecycle_machine_pair_matrix_matches_transition_table`: the old oracle reused the production allowed-target function, allowing a wrong implementation to bless its own transitions. Replaced it with an independent literal STATE.1 graph and checks of all 64 source/target pairs, including terminal rejection. A coordinated running-to-created mutation passed the original and fails the replacement.
- `test_live_task_tids_ignore_terminal_log_events` and three cleanup/fan-out tests patched an obsolete mapping loader that the code no longer called. They could pass with no mapping data. Now they feed the current loader or a real canonical per-task state queue, and establish visible/live candidate evidence before asserting terminal suppression or no worker fan-out. The original terminal test passed with mapping discovery disabled. Cleanup polling checks now retain retry bounds and positive waits without pinning private 50ms tuning.
- `test_run_cli_timeout_keeps_primary_failure_when_harness_dump_fails`: parameterized text, bytes, invalid UTF-8 bytes and absent stderr, asserting preserved primary exception and decoded partial diagnostics. This covers the bytes narrowing fix in the typed CLI helper. Updated the task-factory failure spy to patch the actual imported constructor so its injected cleanup failure fires.
- `test_raw_diagnostic_multiplicity_is_preserved`: searched the entire registry for `PYI036=3`, which already appeared in its human-owned fixture. Now checks the generated row, proving actual multiplicity is rendered.
- `test_resource_monitor_does_not_open_broker_queue_for_metrics`: absence of an attribute could not prove absence of broker activity, and the old body never sampled metrics. It now forbids Queue construction while starting and sampling the real live process, requires a timestamp and real memory evidence, then stops twice.
- Resource metric assertions retain limit category/value and exact recursive totals without locking prose or redundant internal call counts. Manager and agent spawning tests now check event order; manager records are scoped to the expected TID. Consumer/manager tests and correlation/backend tests now close resources in finally/context managers even after failure.
- Backend-root isolation now proves the source contains the payload before checking the target is empty, and closes both persistent connections before schema cleanup.
- `test_every_cli_adapter_applies_the_public_error_exit_map`: a parsing error could produce exit 2 without invoking the patched command. Each case now requires exactly one call to its intended export and the injected diagnostic as well as the specified exit code.
- Two allowlist checks required debt to remain nonempty. Removed that requirement while retaining validation of every present rationale and bounded caller ownership.
- Constants tests no longer lock uncontracted internal polling/drain/queue-size values. Public defaults, queue names, environment translation and exit codes remain; shutdown timing has a real budget inequality. These constant-only deletions remove no executable behavior coverage.
- Release workflow test selects the upload action by its machine identifier instead of human display name and compares artifact membership without incidental order. Release/PG tool tests have additional independent improvements recorded in the tasks report.
- New mypy firing tests run the actual configured checker: valid code passes; untyped definitions, invalid body assignment and incorrect fixture return fail. The local wrapper test proves tests are included and failure exit status propagates.

#### Removed test definitions

- `test_monitor_justification_remains_in_system_invariants`: pinned historical prose, not monitor behavior. Runtime monitor suites remain.
- `test_obs13_is_decomposed_into_sub_invariants`: pinned Markdown heading spelling. Actual observability/liveness behavior remains covered.
- `test_effective_ruff_rules_match_reviewed_inventory`: duplicated the enabled-rule comparison in `test_real_ruff_settings_match_repository_policy`; nonempty inventory assertion moved there.
- `test_extensionless_policy_guard_fires_when_one_tool_is_omitted`: one duplicate case of the retained parameterized guard for every omitted extensionless path.
- `test_resource_monitor_exposes_only_the_current_method_family`: prohibited harmless extra private method names. Real monitor loading, sampling, limits and cleanup tests cover the documented protocol. The single built-in class and constructor-error propagation checks remain because RM-5.1 specifies those boundaries.

#### Retention decisions and limits

Structural tests are not uniformly useless. Public API inventories, CLI-to-command routing, one-way imports, exact queue names, state transition graphs, invalid-input rejection, exit codes and declared Ruff/tooling policy protect explicit contracts and remain. Nontrivial test-helper recovery and cleanup tests also remain: a broken fixture can make product tests falsely pass or leak processes. In particular, the independent CLI review's suggestion to delete outbox-helper retry/close coverage was rejected for this reason. Exact query-shape tests with a PostgreSQL-specific constraint require backend evidence before removal; the cross-review calls this out explicitly.

No production behavior changes were made for typing. Existing and concurrent production/spec edits are outside this audit; the full run validates the combined worktree but cannot attribute their coverage changes to these test edits. Dynamic JSON, plugin and script import boundaries retain local Any where the shape is actually dynamic; concrete fixtures, generators and callbacks are annotated rather than hidden behind Any. There are no no-untyped-def/call ignores or directory-wide mypy bypasses.

#### Per-module review ledger

| Module | Current test definitions reviewed | Disposition |
| --- | ---: | --- |
| tests/architecture/test_import_boundaries.py | 29 | Strengthen oracle/cleanup as listed above |
| tests/architecture/test_liveness_boundaries.py | 3 | Retain specified behavior; annotate where needed |
| tests/helpers/test_message_ids.py | 2 | Retain specified behavior; annotate where needed |
| tests/liveness/test_analysis.py | 6 | Retain specified behavior; annotate where needed |
| tests/liveness/test_host.py | 4 | Retain specified behavior; annotate where needed |
| tests/liveness/test_policy.py | 5 | Retain specified behavior; annotate where needed |
| tests/liveness/test_registry.py | 4 | Retain specified behavior; annotate where needed |
| tests/specs/manager_architecture/test_agent_spawn.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/manager_architecture/test_manager_state_events.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/manager_architecture/test_spawn_retry.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/manager_architecture/test_tid_correlation.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/message_flow/test_agent_spawning_transition.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/message_flow/test_spawning_transition.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/quick_reference/test_queue_names.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/resource_management/test_monitor_compat.py | 5 | Strengthen oracle/cleanup as listed above |
| tests/specs/resource_management/test_resource_limit_killed.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/resource_management/test_resource_metrics.py | 11 | Remove incidental pins or duplicates; retain contract checks |
| tests/specs/resource_management/test_timeout_return_code.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/taskspec/test_agent_taskspec.py | 13 | Retain specified behavior; annotate where needed |
| tests/specs/taskspec/test_peak_metrics.py | 1 | Retain specified behavior; annotate where needed |
| tests/specs/taskspec/test_process_target.py | 2 | Retain specified behavior; annotate where needed |
| tests/specs/taskspec/test_state_transitions.py | 11 | Strengthen oracle/cleanup as listed above |
| tests/specs/test_command_queue_seam.py | 2 | Remove incidental pins or duplicates; retain contract checks |
| tests/specs/test_mypy_policy.py | 2 | New real checker/wrapper firing tests |
| tests/specs/test_plan_metadata.py | 4 | Retain specified behavior; annotate where needed |
| tests/specs/test_ruff_policy.py | 24 | Remove incidental pins or duplicates; retain contract checks |
| tests/specs/test_ruff_suppression_index.py | 33 | Strengthen oracle/cleanup as listed above |
| tests/specs/test_spec_hygiene.py | 2 | Remove incidental pins or duplicates; retain contract checks |
| tests/specs/test_test_audit_policy.py | 6 | Remove incidental pins or duplicates; retain contract checks |
| tests/system/test_builtin_contract.py | 3 | Retain specified behavior; annotate where needed |
| tests/system/test_constants.py | 64 | Remove incidental pins or duplicates; retain contract checks |
| tests/system/test_constants_properties.py | 7 | Retain specified behavior; annotate where needed |
| tests/system/test_django_fixture_cleanup.py | 2 | Retain specified behavior; annotate where needed |
| tests/system/test_helpers.py | 46 | Strengthen oracle/cleanup as listed above |
| tests/system/test_launch_manager_script.py | 2 | Retain specified behavior; annotate where needed |
| tests/system/test_manager_detached_launcher.py | 7 | Retain specified behavior; annotate where needed |
| tests/system/test_manager_process.py | 8 | Retain specified behavior; annotate where needed |
| tests/system/test_optional_extras.py | 6 | Retain specified behavior; annotate where needed |
| tests/system/test_pytest_live_providers.py | 15 | Retain specified behavior; annotate where needed |
| tests/system/test_pytest_pg_script.py | 23 | Independent full-body review and improvements: tasks report |
| tests/system/test_reactor_driver.py | 9 | Retain specified behavior; annotate where needed |
| tests/system/test_release_script.py | 34 | Independent full-body review and improvements: tasks report |
| tests/system/test_release_workflow.py | 1 | Remove incidental pins or duplicates; retain contract checks |
| tests/system/test_run_diagnostics.py | 3 | Retain specified behavior; annotate where needed |
| tests/system/test_runtime_fixtures.py | 8 | Retain specified behavior; annotate where needed |
| tests/system/test_short_tid.py | 8 | Retain specified behavior; annotate where needed |
| tests/system/test_test_backend.py | 6 | Strengthen oracle/cleanup as listed above |
| tests/system/test_uv_wrapper.py | 1 | Retain specified behavior; annotate where needed |
| tests/test_harness_pg_connections.py | 2 | Retain specified behavior; annotate where needed |
| tests/test_harness_registration.py | 45 | Strengthen oracle/cleanup as listed above |
| tests/test_long_session_surface_benchmark.py | 4 | Retain specified behavior; annotate where needed |
| tests/test_multiqueue_polling_benchmark.py | 3 | Retain specified behavior; annotate where needed |

#### Final architecture cross-review

Removed nine historical implementation-shape tests: `test_dead_command_handlers_module_is_deleted`, `test_managed_callable_module_is_deleted`, `test_manager_lifecycle_mirror_module_is_deleted`, `test_old_liveness_owners_are_removed_without_shims`, `test_pruning_package_initializer_is_a_marker`, `test_retained_runner_facades_keep_identity`, `test_run_support_mirror_module_is_deleted`, `test_transitional_core_ops_package_is_deleted`, `test_transitional_core_types_module_is_deleted`. [PY-1] declares core paths private; [PY-4] constrains actual layer imports and the cli/core initializers, not every internal filename, subtree initializer or identity-stable internal re-export. The real import graph, public inventory, callback routing and single deletion authority checks remain. Existing removed-public-compatibility checks remain under the explicit single-current-contract policy, rather than being misclassified as tautologies. The CLI parsing false-pass was independently reproduced before and rejected after the call/output assertions.

Documentation issue flagged: Overview implementation mapping still calls root/core lazy compatibility facades, contrary to its own current-contract section and [PY-4]. The authoritative current constraints and behavior tests were retained; this audit does not change runtime structure to fit the stale note.

Final review also removed `test_agent_backend_package_exports_registration_only`; import-without-loading and host-registration behavior remain. The liveness guard now resolves relative imports with three firing cases. The all-command decorator marker check is retained strictly as application/wiring evidence alongside `tests/commands/test_boundary.py` eager/lazy conversion and cleanup tests; it is not represented as behavioral proof by itself.


### Core audit

### Core test typing and correctness audit

Scope: all 59 test modules under `tests/core/`. Snapshot before this agent's edits: `/tmp/weft-core-before`. No production changes or commits. Review examined each test's oracle and called behavior using the per-test AST index (`/tmp/core-review-index`), with full test and production/spec reads for disputed or changed cases. Counts below are test functions, not expanded parametrized cases. Initial dirty edits were preserved. Generic engine, invalid-input, fault-injection, and real broker/process tests are retained when they establish a contract even if they use private entry points. Assertions introduced solely to narrow optional/dynamic values are not claimed as added correctness coverage.

#### Typing

All fixtures, test functions, nested callbacks, fake methods and returned values now have complete annotations. Shared BrokerEnv and record_and_return come from root-owned `tests.helpers.typing`. Partial process/handler fault doubles use local casts at injection boundaries, not blanket disables. Dynamic JSON payloads retain Any only where nested decoded JSON is the actual boundary; input validation uses object or the exact intentionally invalid union. The runtime fake now accepts the real AgentRuntime protocol's bundle_root parameter. Root added package markers so cross-test imports resolve under no_namespace_packages.

#### Findings implemented

- Removed `test_ops_shared.test_run_submission_bridge_drops_inert_verbose_parameter`: checked absence of an inert private signature parameter. `test_run_adapter_routes_manager_recovery_through_shared_submission` still proves recovery is routed through the shared submission operation. Also removed two assertions comparing independently constructed fake-context dictionaries: those only proved the local fake initializer, while preserving terminal event/state assertions.
- Removed `test_client.test_client_api_omissions_are_explicitly_classified` and its test-local omissions dictionary: asserted only that the local dictionary and explanations were nonempty. Real public API/parity and unsupported-name tests remain.
- Replaced client dump/load/tidy smoke truthiness with isolated source/destination brokers: dry-run imports no queue contents, actual load restores the exact message, and tidy preserves it. This adds a real data preservation oracle.
- Replaced the test-local lifecycle replica in `test_state_machines` with the actual `task_lifecycle_machine`, retaining literal transition cases and action/transition coverage checks. The 17 generic state-machine tests still use arbitrary independent machines to test the generic engine. Before: both lifecycle-labelled tests passed while production decide raised a mutant failure. After: both fail on that same injected mutant. Root's separate TaskSpec transition matrix adds canonical pair coverage; this change also checks lifecycle action/transition identifiers.
- Runtime-control property test expected reserved queue names now come directly from generated inactive TIDs, rather than using the production parser under test as part of the oracle.
- Queue-change waiter failure test now proves one wake only: after joining the producer thread, a second immediate wait must be false. Previously its name claimed once-only behavior but it only observed the first wake.
- Environment profile loader assertion now requires callable rather than merely non-None; adjacent tests invoke profile behavior.
- Removed three `not hasattr(manager_mod, 'send_keyed_ping_probe')` assertions from behavioral leadership/PING tests. They enforced the name of a removed private helper; the queue, timing and leadership assertions remain.
- Renamed manager cleanup test to `test_manager_cleanup_reaps_running_children`, removing `messages == [] or contains(STOP)`: the empty branch allowed success without sending STOP, and the real worker could consume STOP before inspection. It continues to require actual child termination. `test_manager_stop_command_drains_nonpersistent_children`, `test_manager_drain_timeout_force_finishes_stubborn_children`, and `test_manager_drain_reissues_stop_for_child_added_after_stop` retain graceful-drain/control behavior coverage.
- Removed subprocess runner private-signature absence test. Actual subprocess launch/result/timeout/callback/failure identity tests retain the behavior; absence of db_path/config parameters was refactor history, not proof of no broker activity.
- Removed TaskMonitor old module-path/source scan: it asserted a private file and import spelling had disappeared. Runtime TaskMonitor queue processing tests still import and execute the real implementation.
- Removed runner-plugin legacy mapping-style entry_points test. It injected an obsolete return shape outside the supported stdlib EntryPoints contract and pinned AttributeError('select'). Supported entry-point loading, mismatched names, missing plugins and install hints remain.
- Removed `test_spec_parameterization_request_is_constructible`, which asserted a plain dataclass stored the argument supplied to it. Real materialization loads an adapter that reads request.arguments and changes the resulting TaskSpec; request-payload copy and rejection tests remain.
- Removed the hardcoded total of 58 schema columns from the registry-completeness test. The table registry identities and executed live schema validation tests remain; adding a legitimate column should not require changing a meaningless count oracle.

#### SQL removals and retained backend proof

Removed 13 SQL substring tests only after matching them to real MonitorStore results below. These are shared SQL builder functions with no backend-specific choice; SQLite store execution exercises their query bodies. PostgreSQL-only catalog builders were NOT removed: their source-shape test remains because local SQLite tests cannot prove those distinct queries. Also retained the unsafe-identifier and positional-placeholder contract tests: identifiers and placeholder grammar are machine-consumed interfaces.

| Removed test_monitor_sql suffix | Retained/strengthened test_monitor_store suffix |
| --- | --- |
| builds_raw_deleted_reconciliation_query | reconciles_all_unreferenced_families_in_its_context (full-context query; added after coverage identified that earlier mapped tests only executed the targeted-TID query) |
| builds_affected_tid_reconciliation_query | deletes_messages_and_reconciles_only_affected_tids |
| deletes_task_messages_physically | deletes_task_messages_and_reconciles_parent (actual child row counts) |
| builds_deferred_write_outbox_queries | deferred_writes_are_bounded_outbox_rows (original body preserved, retry counted, pending/flushed rows; now out-of-order insertion, tie-break, limit, multi-ID selective flush) |
| selects_missing_task_message_ids | lists_missing_task_message_ids (known excluded, missing returned in order) |
| retirable_family_query_is_conservative | retirement_honors_retention_window; retirement_requires_reserved_cleanup_when_probe_needed |
| retirable_family_query_omits_null_cutoff_probe | retires_completed_collation_families (default no-cutoff path) |
| reserved_cleanup_query_selects_unchecked_families | lists_reserved_cleanup_pending_tasks |
| orphan_recovery_query_excludes_checked_families | lists_raw_deleted_task_log_recovery_tids (raw deletion, checked exclusion, later evidence resets) |
| orphan_recovery_query_summary_gate | lists_raw_deleted_task_log_recovery_tids, now checks require_summary=True excludes before emission and includes after |
| manager_task_spawned_retention_query_keeps_newest_refs | lists_manager_task_spawned_retention_refs; event_trim_deletes_child_refs_without_closing_manager |
| summary_ready_queries_parameterize_cutoffs | summary_ready_respects_terminal_retention; summary_ready_uses_family_high_water; summary_ready_suspects_only_known_interval; summary_ready_classifies_stale_open_without_interval |
| terminal_control_cleanup_query_requires_summary | terminal_control_cleanup_ready_requires_summary_and_age |

Counterargument retained: semantic SQL shape can be a useful compiler contract, especially for PostgreSQL catalog interpretation. Hence those unique-path checks remain. A future PostgreSQL catalog execution test would be stronger, but local execution does not supply a PostgreSQL backend. Do not claim the three skipped real-PostgreSQL core cases passed.

#### Validation

- Focused mypy: Success, no issues in 62 files. Ruff check and format passed.
- Initial core run: 1693 passed, 3 skipped, 1 failed. Failure was introduced while typing an interleaving queue proxy: omitted before_timestamp. Fixed to forward both scan bounds and included the race test in follow-up validation.
- SQL/store focused final: 127 passed (after all 13 SQL removals and new behavioral assertions).
- Affected behavior run: 315 passed in 88.39s, including corrected heartbeat race and strengthened lifecycle/client/queue tests; log `/tmp/weft-core-affected.log`.
- Final parameterization module after removing constructor tautology: 7 passed.
- Mutation proof: old copied lifecycle tests passed with production decide broken; new lifecycle tests reject the identical mutant.
- Root owns final whole-suite run and coverage comparison. This scoped report does not claim unchanged line coverage without that comparison.

#### Per-module review ledger

All listed functions received oracle review. 'Retain' means no proved defect warranting change was found; it is not a claim of exhaustive bug freedom. Table counts include external concurrent edits where noted; root's pre-existing/current task-state work is not claimed as this agent's implementation.

| Module (tests/core/) | Snapshot functions | Current functions | Disposition |
| --- | ---: | ---: | --- |
| monitor/policies/test_dead_task.py | 6 | 6 | Retain; fixtures/callback types corrected where needed |
| monitor/policies/test_dead_task_properties.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| monitor/policies/test_runtime_control.py | 13 | 13 | Retain; fixtures/callback types corrected where needed |
| monitor/policies/test_runtime_control_properties.py | 5 | 5 | Make expected queue selection independent |
| monitor/test_lifetime_report.py | 6 | 6 | Retain; fixtures/callback types corrected where needed |
| monitor/test_progress.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_agent_resolution.py | 5 | 5 | Retain; fixtures/callback types corrected where needed |
| test_agent_runtime.py | 11 | 11 | Retain; fixtures/callback types corrected where needed |
| test_agent_tools.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_agent_validation.py | 18 | 18 | Retain; fixtures/callback types corrected where needed |
| test_builtin_agent_images.py | 6 | 6 | Retain; fixtures/callback types corrected where needed |
| test_builtin_agent_probe.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_builtin_dockerized_agent.py | 14 | 14 | Retain; fixtures/callback types corrected where needed |
| test_builtin_platform_support.py | 3 | 3 | Retain; fixtures/callback types corrected where needed |
| test_client.py | 47 | 46 | Improve dump/load/tidy; remove local omissions tautology |
| test_container_detection.py | 7 | 7 | Retain; fixtures/callback types corrected where needed |
| test_control_messages.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_control_probe.py | 16 | 16 | Retain; fixtures/callback types corrected where needed |
| test_debugger.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_environment_profiles.py | 8 | 8 | Improve callable oracle |
| test_exceptions.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_heartbeat_helpers.py | 11 | 11 | Retain; fixtures/callback types corrected where needed |
| test_llm_backend.py | 12 | 12 | Retain; fixtures/callback types corrected where needed |
| test_manager.py | 227 | 228 | Retain lifecycle/admission/leadership oracles; remove 3 private-name and permissive cleanup assertions |
| test_manager_services.py | 13 | 13 | Retain; fixtures/callback types corrected where needed |
| test_monitor_collation.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_monitor_external_log.py | 19 | 19 | Retain; fixtures/callback types corrected where needed |
| test_monitor_sql.py | 16 | 3 | Remove 13 redundant spelling checks; retain 3 distinct contracts |
| test_monitor_store.py | 79 | 79 | Improve summary gate and deferred ordering; remove column count |
| test_ops_shared.py | 12 | 11 | Remove private signature test and local fake-context assertions |
| test_pipelines.py | 14 | 14 | Retain; fixtures/callback types corrected where needed |
| test_process_title.py | 10 | 10 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_backend.py | 12 | 12 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_container_runtime.py | 12 | 12 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_execution.py | 1 | 1 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_session_backend.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_settings.py | 7 | 7 | Retain; fixtures/callback types corrected where needed |
| test_provider_cli_windows_shims.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_pruning_apply.py | 9 | 9 | Retain; fixtures/callback types corrected where needed |
| test_queue_wait.py | 5 | 5 | Improve single-wake oracle |
| test_runner_diagnostics.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_runner_plugins.py | 6 | 5 | Remove unsupported stdlib-shape rejection test |
| test_runner_validation.py | 2 | 2 | Retain; fixtures/callback types corrected where needed |
| test_serve_log.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_service_convergence.py | 22 | 22 | Retain; fixtures/callback types corrected where needed |
| test_spawn_requests.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_spec_parameterization.py | 8 | 7 | Remove dataclass storage tautology; retain adapter behavior |
| test_spec_run_input.py | 8 | 8 | Retain; fixtures/callback types corrected where needed |
| test_spec_store.py | 3 | 3 | Retain; fixtures/callback types corrected where needed |
| test_state_machines.py | 19 | 19 | Improve production lifecycle linkage; retain generic machine contracts |
| test_subprocess_runner.py | 16 | 15 | Remove private signature absence; retain execution/fault contracts |
| test_targets.py | 9 | 9 | Retain; fixtures/callback types corrected where needed |
| test_task_evidence_properties.py | 6 | 6 | Retain; fixtures/callback types corrected where needed |
| test_task_log_scanner.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |
| test_task_monitoring.py | 27 | 26 | Remove private source-path scan; retain runtime contracts |
| test_task_state.py | 4 | 5 | External concurrent edits; retained state-snapshot contracts |
| test_terminal_handoff.py | 14 | 14 | Retain; fixtures/callback types corrected where needed |
| test_terminal_handoff_transport.py | 5 | 5 | Retain; fixtures/callback types corrected where needed |
| test_tool_profiles.py | 4 | 4 | Retain; fixtures/callback types corrected where needed |

Total functions: snapshot 823, current 806. Removed 19 test functions in this slice; other count changes may be concurrent root work.


#### Coverage-driven correction

The full coverage comparison exposed three missed branches; this corrects the initial semantic mapping rather than treating line coverage as redundant. Added actual all-context orphan reconciliation with two orphan parents, one child-retained parent, an already-marked parent and a different-context parent; repeated reconciliation preserves the first deletion timestamp. This executes SQL builder lines 1029-1031, which targeted-TID store tests did not cover. Runtime execution test now runs both current and supported no-bundle_root adapter signatures via a documented narrow cast, preserving runtime.py line271. Context now separately constructs a context from a plain config mapping, proving normalization to ResolvedConfig, isolation from invalid ambient values, immutable snapshot ownership, and real queue round-trip; this restores context.py line138.

Validation: affected runtime/store/context modules171 passed. Four isolated coverage probes passed and `/tmp/weft-core-restored-coverage.json` confirms all five specifically lost lines executed (SQL1029-1031, runtime271, context138). Mypy65 files/Ruff pass. Root owns focused PostgreSQL replay of the new shared store/context tests. No production changes.


### Task and system audit

### Task test audit

Owned scope: tests/tasks/** (28 Python modules). Initial source snapshot: /tmp/weft-tasks-before/. Every test was reviewed for its assertions, setup, oracle, and behavioral purpose, with deeper production/spec inspection for questionable cases. This is not a claim of mutation testing every test. Concurrent external changes to liveness, endpoints, and monitor tests were preserved.

#### Review inventory

| Module | Current test functions reviewed |
|---|---:|
| test_agent_execution.py | 27 |
| test_command_runner_parity.py | 17 |
| test_consumer_terminal_events.py | 2 |
| test_control_channel.py | 13 |
| test_heartbeat.py | 14 |
| test_liveness_monitor.py | 33 |
| test_multiqueue_watcher.py | 60 |
| test_pipeline_reserved_disposition.py | 1 |
| test_pipeline_runtime.py | 37 |
| test_reserved_disposition.py | 3 |
| test_runner.py | 113 |
| test_runtime_identity_custody.py | 2 |
| test_runtime_identity_signals.py | 3 |
| test_service_task.py | 13 |
| test_signal_deferral.py | 6 |
| test_task_endpoints.py | 17 |
| test_task_execution.py | 119 |
| test_task_interactive.py | 9 |
| test_task_monitor.py | 137 |
| test_task_observability.py | 31 |
| test_task_observer_behavior.py | 10 |
| test_task_stop_sqlite_only.py | 1 |
| test_tasks_simple.py | 7 |
| test_terminal_event_retry.py | 4 |

#### Removed tests and replacement evidence

- `test_task_inherits_from_multiqueue_watcher`: only asserted inheritance. Constructor queue wiring, execution, control and retry behavior remain covered.
- `test_task_has_basic_attributes`: duplicate attribute smoke test. Identity assertion moved to constructor test; required/custom queues already independently checked.
- `test_stop_kill_overrides_declare_control_policy`: checked override presence/nonempty policy metadata, not STOP/KILL semantics. Existing control-channel, pipeline and manager behavioral tests remain. Metadata is not used to execute stop/kill.
- `test_resource_session_and_host_runner_constructors_drop_inert_context`: froze constructor parameter absence through inspect.signature. Real queue-free worker construction and runner/session execution cover the meaningful boundary.

#### Improved tests

- `test_multi_queue_watcher_uses_base_retry_loop` replaced by `test_multi_queue_watcher_recovers_from_transient_drain_failure`: inject a first drain failure, then verify actual queued payload is delivered once and inbox emptied through retry. No class dictionary assertion.
- `test_wait_for_activity_falls_back_when_helper_returns_none`: elapsed >= 0 was a tautology. Now captures Event.wait and checks the requested timeout. An in-memory no-op mutation passes the old assertion and fails the new test. An in-memory retry-bypass mutation also fails the new retry test.
- `test_runner_outcome_rejects_duplicate_worker_pid_identity`: replaces dataclass field inspection with actual rejected legacy constructor input. Retained because [CC-3.2] explicitly forbids duplicate completed-outcome worker PID authority.
- Cleanup function/command output tests now actually invoke cleanup before asserting exact retained output; renamed to say output is preserved. Previously names claimed cleanup behavior but assertions ran before cleanup.
- Control-response broker-failure test now asserts a write was actually attempted and its payload is PONG; previously a no-op implementation could pass.
- Docker network-disabled test checks parsed route headers and absence of routes, rather than exact kernel tab formatting.
- Same-instance monitor deferred retry test removes tautological id(task) equality; actual pending/output progression remains.
- Stop-event constructor tests assert stop sets the supplied event and should_stop, not just object identity.
- Interactive streaming test now unconditionally requires final stderr and completed terminal envelopes. Previously missing all control messages skipped those assertions.
- Interactive startup/completion waits now drive to live-session / terminal-and-closed-session evidence, replacing fixed sleeps. The explicit late-input turn remains bounded because no producer remains.

#### Typing and diagnostic fixes

Complete function/fixture annotations use actual broker targets, queues, fixtures, protocols, and narrow casts for test facades. Invalid-input tests retain intentional invalid values with targeted ignore only where needed. Existing dynamic JSON/runner payloads remain dynamic; no blanket ignores were added. Fixed bytes-vs-text Docker setup diagnostics and the failure-only monitor retirement diagnostic's nonexistent MonitorStore.has_task_messages call by using its actual session/table API. These fixes make failure reporting reliable.

#### Retained contract-level structural tests

Final reactor template prohibitions protect [CC-2.2.1]/[IMPL.10] ownership. Snapshot field isolation protects mutable state ownership; it is not simply a count/shape snapshot. Host runner selection and adapter precedence hardcode independently expected precedence. Resource, monitor lifecycle, queue disposition, observer sampling, task identity, pipeline and terminal event tests assert program outcomes or explicit contract boundaries.

#### Remaining cautions

Some entry-launcher delegation tests use counters in a fake run_until_stopped; these establish delegation only, not real wait behavior. Real wait/retry behavior is independently tested in multiqueue coverage. Do not interpret those tests as integration coverage.
Some interactive memory-limit testing retains a bounded polling loop; it triggers a real resource threshold and validates terminal state/event classification, not elapsed timing. Backend integration tests remain conditional on available environments.

#### Verification

- Focused strict mypy: success, all 28 source files, /tmp/weft-tasks-mypy-final.txt.
- Ruff tasks: passed.
- Entire task slice after typing: 1110 passed, 1 skipped (/tmp/weft-tasks-pytest.txt). Later audit edits validated by 14 focused tests (/tmp/weft-tasks-final-focused.txt), plus all 17 interactive parameterized cases (/tmp/weft-tasks-interactive-final.txt), all passed.
- Mutation probes: in-memory no-op activity wait and bypassed retry both fail improved tests; production files were not mutated.
- No commits. Root owns final aggregate suite and review.

#### Additional assigned system review

All bodies reviewed: test_release_script.py: 34 test functions, test_pytest_pg_script.py: 23 test functions.

- Release dry-run logging test now fails if subprocess execution occurs; the old no-op subprocess stub could conceal accidental execution.
- Release precheck tests assert independent required suite paths, tools, extras, configuration, and all-test markers. Removed irrelevant command/extras ordering and the live-provider last-position pin. Optional extension cases now check literal target paths rather than equality with constants produced by the same module.
- Workflow gate test now parses YAML jobs and checks actual publish dependency edges, selected GitHub verification step and expected SHA environment binding. Removed arbitrary 3000-second value pin. JavaScript query fields remain scoped static checks; they do not prove JavaScript run-selection semantics. Exercising JavaScript would require a separate runtime-backed workflow test and was not represented as achieved here.
- Caller permissions test now checks publish-release job effective permissions and actual reusable workflow target; a read permission in an unrelated job or comment no longer suffices.
- PG OSError-containment tests now record the attempted terminate/kill operation, preventing a no-op from passing without exercising the exception boundary.
- Windows process isolation assertion now requires the CREATE_NEW_PROCESS_GROUP bit, not merely presence of creationflags. Windows branch remains platform conditional.
- Retained version, retag, publication, environment, process escalation, readiness and cleanup tests: these are functional responsibilities of the helper scripts. No product specification defines all helper operational policy values; this review preserves documented script behavior rather than inventing a product contract.
- Validation: 74 parameterized cases passed (/tmp/weft-tasks-system-final.txt); strict mypy and Ruff pass on both modules. Tasks plus both modules strict mypy pass (30 files). Supported SimpleBroker architecture scan passes after switching monitor spy self type to public BrokerConnection.

#### Independent adapter change review

Compared against /tmp/weft-adapters-before/tests, including AST-normalized semantic deltas, removed assertions, dynamic annotations and casts. Boundary close, queue delivery/move, stream sentinel, full chunk sequence, checkpoint ordering and transition-target changes strengthen independent evidence. Internal alias/signature deletions match stated non-normative scope. No blanket Any or ignore expansion observed; remaining dynamic values are JSON/broker/mocking boundaries, with narrow facade casts.

One finding sent to root and adapter owner: test_client_serve_preserves_context_config removed its public wrapper `None` return assertion during typing. Recommended `invoke: Callable[[], object] = client.managers.serve; assert invoke() is None` to preserve runtime oracle without mypy func-returns-value. This is distinct from a lower-level command's return test. Adapter edits are concurrent; root must verify the final resolution and rerun aggregate checks.

PG interrupt tests also use a module-local POSIX signal facade instead of mutating global os.name, avoiding platform/pathlib corruption during failures on Windows.

Final PG-only rerun passed after facade isolation (/tmp/weft-tasks-pg-final.txt). In-memory no-op interrupt mutation was rejected by the new attempted-terminate oracle. All 30 assigned files pass Ruff format check and git diff --check.


### Commands and CLI audit

### Adapter test typing and correctness audit

Scope: tests/commands and tests/cli. No production edits or commits. Pre-edit snapshot: /tmp/weft-adapters-before/tests. Existing/concurrent owner changes retained. Counts are test definitions, not parametrized executions.

#### Commands: per-module review

| Module | Current tests | Review |
|---|---:|---|
| tests/commands/test_boundary.py | 6 | Exception translation, public error provenance, started-stream cleanup and idempotence; improved close oracle. |
| tests/commands/test_diagnostics.py | 3 | Rendered diagnostic fields and controlled extensible payload handling; retain. |
| tests/commands/test_dump_load.py | 43 | Actual dump/load queue contents, remapping, malformed input, selection and failure behavior; retain. |
| tests/commands/test_dump_load_sqlite_only.py | 2 | SQLite-specific restoration/drop behavior using real storage; retain. |
| tests/commands/test_interactive_client.py | 8 | Terminal client input/output, resize, session closure and exit behavior; retain. |
| tests/commands/test_interactive_exit_terminal.py | 3 | Terminal events correlated to exit codes, including malformed evidence; retain. |
| tests/commands/test_manager_commands.py | 55 | Real manager discovery/lifecycle and bounded mocked failure/escalation paths; removed internal alias ban. |
| tests/commands/test_observation_connections.py | 4 | Queue closure and lazy subscription cleanup are resource lifecycle obligations; retain. |
| tests/commands/test_queue.py | 32 | Real queue reads, writes, move selection, endpoints, and closed subscriptions; strengthened delivery/source removal. |
| tests/commands/test_realtime_events.py | 13 | Parsing/filtering/merging typed events with concrete timestamps and cursor boundaries; retain. |
| tests/commands/test_result.py | 59 | Queue result delivery, claimed-result provenance, malformed frames, status and terminal ordering; retain. |
| tests/commands/test_retention_prune.py | 18 | Real retention eligibility/age boundaries and protected queue families; retain. |
| tests/commands/test_run.py | 81 | Task submission, run result/exit, frame decoding and lifecycle cases; strengthened all 600 output chunks. |
| tests/commands/test_run_public.py | 7 | Public run signature is normative [PY-2], plus real submission/output result semantics; retain. |
| tests/commands/test_runtime_prune.py | 20 | Actual stale runtime pruning and protected live handles; removed internal import alias ban. |
| tests/commands/test_serve.py | 13 | Serve validation, leases, cleanup, and capability forwarding; retain. |
| tests/commands/test_specs.py | 19 | Stored-spec identity, creation/list/show/delete and input validation via real queues; retain. |
| tests/commands/test_status.py | 53 | Snapshot evidence precedence, liveness, retained tasks and observer results; retain. |
| tests/commands/test_submission.py | 22 | Submission preparation/validation, environment/options and manager startup semantics; removed two internal inert-signature pins. |
| tests/commands/test_system_public_contract.py | 7 | Structured public receipts and context identity; strengthened exact tidy target. |
| tests/commands/test_task_commands.py | 66 | Status/event delivery and STOP/KILL evidence/escalation; improved nonempty stream and expected transition targets, removed internal inert-signature pin. |
| tests/commands/test_task_evidence.py | 18 | Independent literal payloads exercise terminal/local evidence precedence and nonconsumption; retain. |
| tests/commands/test_task_monitor.py | 16 | Summary/event classification, cursor/checkpoint and replay semantics; improved sink failure and checkpoint ordering. |
| tests/commands/test_task_snapshot_reducer.py | 18 | Concrete lifecycle precedence, probe plans, result evidence and no shared top-level mutation; removed reexport identity pin. Keep pure-dependency guard as architecture check, not proof of absence of I/O. |
| tests/commands/test_tid_mapping_contracts.py | 12 | Canonical IDs vs selectors at state/control boundaries. Concurrent outside additions preserved; retain. |

#### CLI typing inventory

All CLI test definitions/helpers were read while completing concrete annotations and assertion narrowing. Parent commissioned a separate full CLI correctness audit; integrate that report for independent module dispositions.

| Module | Tests |
|---|---:|
| tests/cli/test_cli.py | 12 |
| tests/cli/test_cli_init.py | 15 |
| tests/cli/test_cli_init_sqlite_only.py | 2 |
| tests/cli/test_cli_list_task.py | 3 |
| tests/cli/test_cli_long_session.py | 1 |
| tests/cli/test_cli_manager.py | 11 |
| tests/cli/test_cli_pipeline.py | 7 |
| tests/cli/test_cli_queue.py | 29 |
| tests/cli/test_cli_result.py | 11 |
| tests/cli/test_cli_result_all.py | 6 |
| tests/cli/test_cli_run.py | 61 |
| tests/cli/test_cli_run_installed_entrypoint.py | 2 |
| tests/cli/test_cli_serve.py | 8 |
| tests/cli/test_cli_spec.py | 8 |
| tests/cli/test_cli_status_rendering.py | 4 |
| tests/cli/test_cli_system.py | 23 |
| tests/cli/test_cli_tidy.py | 1 |
| tests/cli/test_cli_validate.py | 23 |
| tests/cli/test_commands.py | 6 |
| tests/cli/test_env_file_bootstrap.py | 12 |
| tests/cli/test_manager_proctitle.py | 1 |
| tests/cli/test_result_claimed_json.py | 1 |
| tests/cli/test_retired_command_rendering.py | 3 |
| tests/cli/test_status.py | 11 |

#### Removed process-only tests

- `test_manager_commands::test_manager_runtime_exposes_only_canonical_lifecycle_names`: bans internal underscore lifecycle aliases without a normative public contract. Actual start/stop/serve/discovery and public facade tests retained.
- `test_runtime_prune::test_prune_command_does_not_reexport_core_config_types`: bans incidental module imports instead of checking pruning. Real pruning eligibility, protection and receipts retained.
- `test_submission::test_manager_startup_interfaces_drop_inert_verbose_parameter` and `test_prepare_taskspec_drops_inert_context_parameter`: inspect internal parameter spellings rather than behavior. Preparation/validation and startup tests retained. Public `cmd_run` exact signature retained because Python surface spec [PY-2] makes it normative.
- `test_task_commands::test_task_snapshot_interfaces_drop_inert_process_parameter`: internal helper signature pin; concrete task snapshot/probe tests retained.
- `test_task_snapshot_reducer::test_system_reexports_snapshot_types`: asserts internal alias object identities, including private helper aliases. Pure reducer and actual system collector tests retained.

#### Improved oracles

- Boundary stream close now starts a closable source, verifies close occurs exactly once, and checks exhausted iteration. Original unstarted-generator test passed with `_TranslatedIterator.close` replaced by a no-op; improved test fails that mutation.
- Broadcast verifies exactly one destination and the payload actually received. Original receipt-only test passed a fake broadcast that returned count=1 without writes; improved test fails. Endpoint writes also verify actual inbox payload.
- Watched task status now contains a real sentinel typed event. Original empty stream test passed replacing `cmd_task_status` with always-empty output; improved test fails.
- Queue move verifies source remainder as well as destination order/count. This distinguishes move from copy.
- Interactive output beyond fixed window checks the complete ordered 600-chunk sequence instead of just endpoints/count.
- System tidy asserts the exact context display target, replacing a truthiness check.
- Control convergence table now checks expected target state independently as well as action. Coverage bookkeeping alone can accept incorrect state transitions.
- Checkpoint test now verifies sink runs before checkpoint publication and failed sink leaves no checkpoint. One-record scan gives an exact seeded cursor oracle; existing replay tests remain.
- Removed an unrelated fake context dictionary identity assertion from queue watch cleanup test. Actual generator closure assertion retained.

#### Verification

- Strict mypy: `mypy tests/cli tests/commands --config-file /tmp/weft-mypy-tests.toml`: clean, 51 source files.
- Ruff owned directories clean.
- Mutation evidence: `/tmp/adapters-weak-oracle-mutations-before.txt` (old passes), `/tmp/adapters-weak-oracle-mutations-after.txt` (improved fails). Close mutation also exercised directly.
- Owned-directory suite completed: 4 skipped, 4 failures, all four fixed (class-level cli_exit_code lookup; TaskSpec mock metadata). No remaining runtime regression identified. Log: `/tmp/adapters-pytest.txt`.
- Final focused verification reran all four failures plus changed correctness tests: 99 passed, exit 0. Log: `/tmp/adapters-final-targeted.txt`. Checkpoint success/failure and state transition target checks also passed independently.
- Final Ruff check and format check pass; strict owned-directory mypy still passes (51 sources).

#### Limits and retained architectural checks

Tests using mocks are not automatically tautological: control sequencing, observer cleanup, retry boundaries, command forwarding, and terminal evidence precedence have distinct input and expected output/effect oracles. Existing pure reducer AST dependency guard is useful as a narrow architectural constraint but cannot prove absence of all I/O (aliases/dynamic imports can bypass it); behavioral reducer tests are retained. Shallow-copy reducer test matches actual top-level ownership contract; no unjustified deep-copy requirement added.

#### Integrated independent CLI findings

Applied the parent's independent Claude CLI audit (all 24 CLI modules read end-to-end): `test_system_builtins_ignores_local_project_shadow` now actually calls `system builtins --json` after creating the shadow and checks shipped source/function target plus absence of local-only name. Previously the test stopped after setup and had no oracle for its named behavior. Trimmed redundant JSON parsing/builtin assertions from metadata test. Relaxed exact Rich-centered whitespace in validation short-circuit test to ordered semantic fragments, retaining absence of both preflight messages. Typed missing-plugin thrower with object variadics and Never; removed unjustified no-untyped-def suppression. Exact four changed CLI tests pass (`/tmp/adapters-cli-final2.txt`), final mypy/Ruff/format checks pass. Test-helper retry/cleanup retained on parent review because it protects TS-0 harness correctness; Removed `TestCLIConstants.test_constants_override` on parent review: monkeypatching module globals and mocking echo only pins implementation; real --version tests retain observable output coverage. Entire `test_cli.py` passes (12 tests; `/tmp/adapters-version-final.txt`).

Post-review correction: restored the public client serve `None` result assertion using `Callable[[], object]`, avoiding mypy's no-value-return restriction without losing the runtime oracle. All 19 serve cases passed; focused mypy/Ruff passed. Independent core review is in `/tmp/weft-review-core-adapters.md`; authorized lifecycle expected-action enhancement passed 26 cases.


### Independent root review and context/TaskSpec audit

### Independent root-slice review and context/TaskSpec value audit

Reviewed current plan and root-owned test changes against `/tmp/weft-typing-baseline`, excluding core/tasks/cli/commands. No delegation. Edits made by this reviewer are confined to `tests/context` and `tests/taskspec`; second-slice snapshot is `/tmp/core-second-before`.

#### Verdict

PASS after one reported diagnostic defect was fixed by root. In `tests/architecture/test_import_boundaries.py`, the renamed `getattr_module` variable was not initially used in the final error f-string, leaving a stale previous-loop `module`. Current source now reports `getattr_module`, confirmed by rereading lines 557-563. This affected scanner diagnostics, not rejection itself.

No remaining correctness weakening found in reviewed root deltas. Public/resource constants and shutdown-budget relations remain where they encode behavior; removed numeric timing literals, prose text, empty-allowlist prohibitions and workflow step-name ordering only constrained incidental process/spelling. Resource-limit tests continue to require forbidden outcomes, reason subject/values, real aggregated metrics and recursive traversal. Lifecycle pair matrix now uses a literal independent expected graph. Harness changes replace obsolete unused mock seams with reachable current state entries and positive-before-negative evidence. Timeout diagnostics preserve the original error and now cover bytes. Shared fixture protocols match actual ownership/call signatures. Partial doubles use narrow casts; dynamic import/JSON boundaries are distinguished from fixtures with known concrete types. Package markers make mypy module identities explicit.

Gate/config review: pyproject retains strict definition/body settings and removes the tests exclusion; no directory suppressions were added. Local mypy wrapper, CI invocation, release invocation and docs now include tests. Spec [TS-3] text matches the previously independently reviewed exact proposal. New mypy policy test actually executes typed acceptance and three invalid cases, plus wrapper argument/failure propagation. The PyYAML stub dependency provides types to existing workflow tests rather than adding runtime behavior. Production/source differences already present in the baseline are not attributed to this test audit.

#### Context and TaskSpec exhaustive body review

Read every test body in both directories and the fixture module, plus matching context/spec/model/transport behavior for disputed checks. Kept acceptance/rejection pairs, queue round trips, configuration precedence, filesystem permission behavior, immutable data and transport provenance. The public get_context absence assertion remains as a public-surface check; it is distinct from a private helper-name ban. Exact shared boundary errors remain where the test explicitly compares callers' public error contract.

Implemented improvements:

1. Metric history property previously accepted a complete `update_metrics` no-op because all current values could remain None; current <= peak also accepted arbitrarily inflated peaks. It now compares each metric to the latest supplied non-None sample and each peak to the exact maximum observed, including preservation across None updates. This follows `02-TaskSpec.md` current/peak field contract.
2. Status timestamp property previously accepted a no-op `mark_started`: its count increased but the task remained created, bypassing timestamp checks. It now requires each successful operation's requested status and preservation of the whole state after a rejected operation; timestamp checks remain.
3. Mutation probes confirmed the old metric property passes a no-op update_metrics and the old status property passes a no-op mark_started; the strengthened versions reject those identical mutants.
4. Removed the Pydantic model_post_init positional-only signature inspection. Positional-only parameter spelling does not prove model validation/freezing and is not a product contract; actual construction/validation/frozen-assignment tests remain.
5. Removed the parameterized absence check for private `_mutations_allowed`. Replaced nested `_allow_mutation` absence checks with actual rejected assignments to every constructed nested section, adding parameterization and its argument alongside spec/limits/runner/agent/tools/templates/run-input/stdin/arguments. A harmless internal name should not fail a test, and an actual thaw should.
6. Runtime test now proves exact 3.0-second live and 2.5-second terminal elapsed time with a fixed clock; previously any nonnegative number passed. Log projection test now verifies actual tid/name/status/runtime/metadata values, rather than only key existence.
7. Renamed the valid provider_cli template acceptance test so it does not claim to exercise absent-provider rejection. Existing provider runtime validation covers rejection; no acceptance coverage removed.
8. Removed a duplicate transport encoded-bundle-root assertion, retained all round-trip/privacy/malformed-boundary checks and documented narrow invalid-path escapes.
9. Replaced the remaining context fake's no-untyped-def ignore with complete object/None annotations.

Validation: final `pytest tests/context tests/taskspec -n 2 --no-cov`: **125 passed in 2.06s**. Focused mypy: **8 files, no issues**. Ruff check and format passed. All changes uncommitted, no pending tools/processes for this slice.

#### Exhaustive module inventory

| Module | Before functions | After functions | Disposition |
| --- | ---: | ---: | --- |
| tests/context/__init__.py | 0 | 0 | Helper/package review; no collected tests |
| tests/context/test_context.py | 31 | 31 | Retain all behavioral tests |
| tests/context/test_context_sqlite_only.py | 1 | 1 | Retain all behavioral tests |
| tests/taskspec/__init__.py | 0 | 0 | Helper/package review; no collected tests |
| tests/taskspec/fixtures.py | 0 | 0 | Helper/package review; no collected tests |
| tests/taskspec/test_taskspec.py | 61 | 59 | Remove 2 private-shape tests; strengthen nested freeze/runtime/log values |
| tests/taskspec/test_taskspec_properties.py | 6 | 6 | Strengthen metric/status properties; retain all 6 properties/cases |
| tests/taskspec/test_transport.py | 5 | 5 | Retain all 5 tests; remove duplicate assertion |

#### Root delta inventory

Reviewed the complete diff for these Python paths (including package markers); full body re-audit outside context/taskspec is not claimed.

- tests/architecture/__init__.py
- tests/architecture/test_import_boundaries.py
- tests/conftest.py
- tests/context/__init__.py
- tests/context/test_context.py
- tests/fixtures/llm_test_models.py
- tests/fixtures/provider_cli_fixture.py
- tests/fixtures/runtime_profiles_fixture.py
- tests/helpers/__init__.py
- tests/helpers/hypothesis_strategies.py
- tests/helpers/multiqueue_sigint_probe.py
- tests/helpers/typing.py
- tests/helpers/weft_harness.py
- tests/liveness/__init__.py
- tests/liveness/test_registry.py
- tests/long_session_surface_benchmark.py
- tests/multiqueue_polling_benchmark.py
- tests/specs/__init__.py
- tests/specs/manager_architecture/__init__.py
- tests/specs/manager_architecture/test_agent_spawn.py
- tests/specs/manager_architecture/test_manager_state_events.py
- tests/specs/manager_architecture/test_spawn_retry.py
- tests/specs/manager_architecture/test_tid_correlation.py
- tests/specs/message_flow/__init__.py
- tests/specs/message_flow/test_agent_spawning_transition.py
- tests/specs/message_flow/test_spawning_transition.py
- tests/specs/quick_reference/__init__.py
- tests/specs/resource_management/__init__.py
- tests/specs/resource_management/test_monitor_compat.py
- tests/specs/resource_management/test_resource_limit_killed.py
- tests/specs/resource_management/test_resource_metrics.py
- tests/specs/taskspec/__init__.py
- tests/specs/taskspec/test_process_target.py
- tests/specs/taskspec/test_state_transitions.py
- tests/specs/test_command_queue_seam.py
- tests/specs/test_mypy_policy.py
- tests/specs/test_ruff_policy.py
- tests/specs/test_spec_hygiene.py
- tests/specs/test_test_audit_policy.py
- tests/system/test_constants.py
- tests/system/test_constants_properties.py
- tests/system/test_helpers.py
- tests/system/test_pytest_pg_script.py
- tests/system/test_reactor_driver.py
- tests/system/test_release_script.py
- tests/system/test_release_workflow.py
- tests/system/test_runtime_fixtures.py
- tests/system/test_short_tid.py
- tests/system/test_test_backend.py
- tests/taskspec/__init__.py
- tests/taskspec/test_taskspec.py
- tests/taskspec/test_taskspec_properties.py
- tests/test_harness_registration.py

Also reviewed pyproject.toml, bin/mypy-check, bin/release.py, .github/workflows/test.yml and mypy/spec documentation deltas.


Coverage follow-up: the prior freezing change to manually constructed context fixtures eliminated the constructor normalization branch. New `test_manual_context_normalizes_plain_broker_config_without_ambient_reads` proves that accepted runtime path explicitly with a documented narrow cast, immutable ResolvedConfig snapshot, invalid ambient setting and real queue I/O. Focused coverage confirms context.py138 executes. See `/tmp/weft-audit-core.md` coverage correction for full validation.


### Independent CLI audit

I've now read all 24 `tests/cli/test_*.py` files in full, plus the governing specs (CLI-1.x, CLI-4/5/6, TS-0/TS-3) and production contracts as needed. Here is my per-test value review.

#### Summary verdict

The `tests/cli/` suite is, on the whole, high-value: the large majority are real subprocess/real-broker integration tests asserting operator-observable contracts (exit codes 0/1/2/3/124, JSON key sets, [SB-0.2] string vs numeric timestamp projection, error prose without tracebacks, resource/data lifecycle, cleanup). Oracles are mostly independent (constructed snapshots, direct queue reads, separate expected transcripts). I found **one genuine oracle gap**, a small number of **low-value process-only / test-infrastructure self-tests**, incidental coupling to a **flag scheduled for deletion**, and minor **brittleness/redundancy**. No broad deletions are warranted.

---

#### Findings (ranked)

##### 1. CONFIRMED oracle gap — `test_system_builtins_ignores_local_project_shadow` (tests/cli/test_cli_system.py:126)
The test name and CLI-6 contract ("`system builtins` reports the shipped inventory … independent of local stored-spec shadows") promise that a local `tasks/probe-agents.json` shadow does **not** change `weft system builtins` output. But the body only creates the shadow spec and asserts `rc == 0`/`err == ""` on the *create* — it never runs `system builtins` afterward and never asserts the builtin inventory is unaffected. The test passes even if `system builtins` began honoring shadows (the exact regression it names).
- **False-pass:** production starts resolving shadows in `system builtins`; test stays green.
- **No equivalent coverage:** `test_spec_list_and_show_prefer_local_shadow_over_builtin` (test_cli_spec.py:367) proves the *opposite* behavior (list/show *prefer* the shadow). The builtins-ignore-shadow path is otherwise unprotected.
- **Fix (strengthen, do not delete):** after creating the shadow, run `system builtins --json` and assert `probe-agents` still reports `source == "builtin"` and `function_target == "weft.builtins.agent_probe:probe_agents_task"`, and that `local-probe-agents` does not appear.

##### 2. Low-value test of test-scaffolding — `test_wait_for_outbox_retries_transient_backend_failure` (tests/cli/test_cli_result.py:80)
This exercises the module-local polling helper `_wait_for_outbox` with a hand-rolled `FakeQueue`, asserting it retries once on `OperationalError` and closes each queue. It protects no production or tooling contract — only the retry/close hygiene of a setup helper used by other tests in the file. Oracle is fully synthetic.
- **Suggestion:** remove. If connection-hygiene coverage is desired it belongs to real broker/harness tests, not a mock of a test helper. No product coverage is lost.

##### 3. Low-value white-box test — `TestCLIConstants.test_constants_override` (tests/cli/test_cli.py:140)
Mocks `typer.echo` and `typer.Exit` and calls `version_callback(True)` directly to assert it interpolates the two module globals. This couples to the internal implementation of `version_callback` and asserts nothing beyond "it reads `PROG_NAME` and `__version__`."
- **Equivalent coverage:** `test_version_flag` (test_cli.py:35) and the `python -m weft --version` subprocess test already prove the real observable `--version` output.
- **Suggestion:** remove; observable behavior is covered by the real invocation tests.

##### 4. Incidental coupling to a flag slated for deletion — `WEFT_MANAGER_REUSE_ENABLED`
Set in test_cli_run.py:2957, :2996, :3039 (three parallel/reuse tests) and referenced in the failure-dump payload at test_cli_run.py:341; also set in test_cli_run_installed_entrypoint.py:127. Per the project decision to delete this test-only, inert-since-RunSession flag, these assignments are dead coupling. The tests themselves (single-manager convergence, reuse-adoption) exercise real, valuable behavior and should be **kept** — but when the flag is removed from production, drop the `env[...] = "1"` assignments and the `env.get("WEFT_MANAGER_REUSE_ENABLED")` diagnostic line so the tests don't imply a live knob. Flagging so the root fix stays consistent across the CLI slice (this is not the duplicate-constant/glyph work the native reviewer owns).

##### 5. Test-of-harness ownership overlap — `test_installed_console_init_failure_does_not_materialize_harness_context` (tests/cli/test_cli_run_installed_entrypoint.py:222)
Re-invokes the acceptance test with a monkeypatched `subprocess.run` to verify `WeftTestHarness` cleanup on a pre-enter init failure (won't materialize a broker context, won't touch an inherited default-DB root). This protects a genuine destructive-safety property, so **keep it**, but note its true owner is the harness (plan slice 1 / `tests/test_harness_registration.py`), not the CLI contract. Worth a one-line comment pointing at the harness invariant it guards so it isn't mistaken for a CLI behavior test.

##### 6. Minor presentation brittleness — `test_validate_taskspec_adapter_failure_short_circuits_preflight` (tests/cli/test_cli_validate.py:721)
The valuable oracle here (valid ✓, run-input ✗, and preflight lines *absent* — proving short-circuit) is sound. But it asserts via `out.startswith(...)` including the exact Rich-centered header `"               Validation Errors               \n"`. That centered whitespace is a presentation detail (sensitive to Rich version/width even with `COLUMNS=100`), not an enumerated contract.
- **Suggestion:** assert the ordered fragments (`"✓ TaskSpec is valid"`, `"✗ Run-input validation failed"`, `"run_input"`, `"No module named 'helper_module'"`) and the two absent preflight lines, without pinning the centered spacing.

##### 7. Minor redundancy / dead code (low priority)
- **test_commands.py `TestValidateTaskspecCommand`** overlaps with **test_cli_validate.py** on valid-success, schema-invalid, missing-file(exit 2), and summary. Keep both — test_commands.py uniquely covers malformed-JSON (`rc==2`, `_json` key) and multi-error enumeration; test_cli_validate.py uniquely covers the layered load-runner/preflight matrix. Consolidation optional, not required.
- **test_cli_system.py `test_system_builtins_json_reports_builtin_metadata`** (test_cli_system.py:85) computes `payload = json.loads(out)` and re-looks-up `builtin` twice (lines 94–102 then 104–114). Harmless duplicated block; trim for clarity.

---

#### Notably strong tests (keep as-is — flagging so they aren't "simplified" away)
- `test_status_json_projects_only_owned_broker_identity_fields` (test_cli_status_rendering.py:45): exact per-source [SB-0.2] string-vs-numeric matrix from CLI-1.2.1; independent oracle. High value.
- Terminal-handoff category matrix, cancelled/memory/timeout exit-code cases, and `_assert_no_terminal_handoff_details` (test_cli_run.py:717 ff.): directly enforce the bounded public error categories and `{tid,status,error}` schema in CLI-1.1.1.
- `test_result_claimed_outbox_json_keeps_reconciliation_without_waiting` (test_result_claimed_json.py:21): monkeypatches `_await_single_result` to *raise if called* — an excellent negative oracle proving the "return promptly, don't wait" contract.
- Queue argv-vs-stdin context tests (test_cli_queue.py:27, :72, :104) and the typed INVALID_MESSAGE_ID JSON selector (test_cli_queue.py:554, :576): enforce CLI-4's exact "no context to decode argv / read-only stdin context / typed-result JSON selection" contracts.
- SIGINT/interrupt stream-close tests (test_cli_queue.py:121, :152; test_cli_system.py:726): real resource-lifecycle proofs.
- Env-file bootstrap suite (test_env_file_bootstrap.py): before-import ordering, precedence, exit-2 on missing/malformed, secret non-echo, no-recursion — full CLI-5 coverage. `test_started_task_tid_requires_child_work_start` (test_cli_run.py:617) legitimately protects the cancellation-test oracle's producer-closure boundary (TS-0) — keep.

---

#### Per-module reviewed test-definition counts (all reviewed; none skipped)

| Module | Test defs reviewed |
|---|---|
| test_cli.py | 13 |
| test_commands.py | 6 |
| test_cli_run.py | ~61 |
| test_cli_result.py | 12 |
| test_cli_result_all.py | 6 |
| test_result_claimed_json.py | 1 |
| test_cli_queue.py | ~30 |
| test_cli_spec.py | 8 |
| test_cli_validate.py | ~27 (incl. parametrize families) |
| test_cli_status_rendering.py | 4 |
| test_status.py | 9 |
| test_retired_command_rendering.py | 3 |
| test_cli_manager.py | 12 |
| test_cli_init.py | 13 |
| test_cli_init_sqlite_only.py | 2 |
| test_cli_system.py | ~24 |
| test_cli_tidy.py | 1 |
| test_cli_run_installed_entrypoint.py | 2 |
| test_cli_list_task.py | 3 |
| test_cli_serve.py | 8 |
| test_env_file_bootstrap.py | 12 |
| test_manager_proctitle.py | 1 |
| test_cli_long_session.py | 1 |
| test_cli_pipeline.py | 7 |

**24 files total** (the glob returned 24, not 25). Every file was read end-to-end and every test definition considered; the three files whose bodies exceeded a single read page (test_cli_run.py) were paged through completely. **No module was left partially reviewed.** Counts marked `~` are exact function-def counts where parametrize expands into multiple cases; I counted each `def test_*` once.

Typing note (secondary to my correctness remit): the CLI tests are already largely well-annotated; the main mypy-relevant escapes I saw are the deliberate `# type: ignore[no-untyped-def]` on the invalid-input helper `_raise_missing_plugin` (test_cli_init.py:368) and `Any`-typed subprocess/JSON payload boundaries (e.g. test_cli_serve.py record dicts), both of which are the genuinely-dynamic broker-JSON boundary the plan explicitly permits — no blanket suppressions or untyped defs to flag.

No external artifacts were written and nothing was modified (read-only review). The root can apply findings 1–3 directly; 4–7 are hygiene items to fold in with the related production/native-reviewer work.

### Independent core review

### Independent core diff review

Reviewed `/tmp/weft-audit-core.md`, SQL removals against MonitorStore behavioral replacements, lifecycle production linkage, removed-test rationales, and assertion/runtime-semantic changes relative to `/tmp/weft-core-before`. The wider typing diff was screened with AST assertion comparison and targeted runtime-double reads. This is an independent diff review, not a second claim to fully audit every unchanged core test.

#### Findings

1. PostgreSQL compatibility evidence is incomplete until the parent-owned PG execution: removing `test_monitor_sql_retirable_family_query_omits_null_cutoff_probe` removes its explicit rejection of `? IS NULL`. SQLite executes such a predicate but does not establish PostgreSQL parameter-type inference compatibility. Shared query construction alone is insufficient to justify this particular deletion. Recommendation: retain a narrow no-untyped-NULL-placeholder guard OR verify retained no-cutoff retirement through PostgreSQL. Parent will run the retained MonitorStore/SQL suite on PG. Other SQL removals map to actual executed data effects, with distinct PG catalog SQL shape checks retained.

2. Lifecycle table checked expected target/transition ID and aggregate action coverage, but not which action belongs to each row. Swapping actions could pass while every action remains covered. Fixed with parent authorization: each literal row now contains its expected action and asserts `decision.action == expected_action`. The production lifecycle replacement itself is a clear improvement over the copied test-local machine. Validation: 26 lifecycle cases pass; focused mypy/Ruff pass.

No further unjustified test deletion or weakened correctness oracle found in reviewed core diffs. Internal alias/signature bans, dataclass storage assertion, local omissions dictionary, and obsolete entry-point return shape deletions match their stated rationales. Deferred-write ordering/limit/selective flush and summary-gated recovery now use actual store results. Queue wake test correctly closes the producer before asserting no second wake. Corrected queue proxy forwards both scan bounds. Nominal casts preserve runtime doubles; callback recorder replacements preserve effects and returned values.

#### Adapter follow-up

Restored `assert invoke() is None` for client serve via `Callable[[], object]`; preserves public wrapper void result independently of the runtime helper's tuple. All 19 serve cases passed, and mypy/Ruff passed. No test processes remain active from this review.


### Independent architecture review

### Final architecture audit review

Verdict: PASS for the reviewed final changes. Initial review was read-only. Root then authorized the final test-only import-spelling fix in `test_liveness_boundaries.py`; no production edits. Reviewed current `tests/architecture/test_import_boundaries.py` and `test_liveness_boundaries.py` against the initial snapshot and the prior findings.

#### Findings resolved

Exactly ten historical tests removed:

- `test_transitional_core_ops_package_is_deleted`
- `test_transitional_core_types_module_is_deleted`
- `test_managed_callable_module_is_deleted`
- `test_manager_lifecycle_mirror_module_is_deleted`
- `test_dead_command_handlers_module_is_deleted`
- `test_run_support_mirror_module_is_deleted`
- `test_retained_runner_facades_keep_identity`
- `test_agent_backend_package_exports_registration_only`
- `test_pruning_package_initializer_is_a_marker`
- `test_old_liveness_owners_are_removed_without_shims`

These asserted historical file names, private aliases, or private package inventories. The retained guards establish the actual contracts: one-way runtime imports, public inventories, initializer behavior, lazy command loading, backend registration behavior, and sole task-state deletion ownership. `14-Python_API_Surfaces.md` [PY-1] explicitly declares core names private; [PY-4] specifically requires CLI/core markers and the import graph. `01-Core_Components.md` [CC-3] maps RunnerOutcome ownership to `core/runners/outcome.py`, without requiring four permanent internal aliases. `07-System_Invariants.md` [LIVENESS.R3] requires a sole component deleter, not the permanent absence of two historical filenames.

Public inventories, no-compatibility guards and CLI/core marker tests remain. This appropriately preserves the current explicit policy rather than deleting every source-based architecture test indiscriminately. The no-compatibility checks are narrower historical probes of the Overview's 'Single current contract' policy, not tautologies.

The CLI error-map test now requires exactly one call to the expected patched export, its unique error diagnostic, and the expected exit. `_calls` and `_export` are bound in defaults, avoiding loop closure leakage. This is required by [PY-2]'s one corresponding facade call and typed-error/CLI-exit contracts. Counterexample checked earlier: the baseline usage case passed with `definitely-not-a-cli-command`, which only caused a parser exit 2; the current assertion rejects that same invocation before accepting the exit result.

The liveness import parser now resolves relative imports from the flat `weft.liveness` package, including `from .. import core`, and records imported names. Three new parameterized probes cover relative context imports, relative core imports and direct SimpleBroker imports. The policy's queue_window exception now excludes forbidden other core modules without requiring an unnecessary core edge to exist. Removing that existence requirement does not weaken dependency exclusion. Sole-exact-delete ownership scanning is retained.

#### Verification

- Scoped mypy: success, no issues in 3 source files.
- Scoped Ruff check: all passed.
- Scoped Ruff format check: 3 files already formatted.
- Final focused liveness architecture tests after the authorized spelling fix: 8 passed in 1.42s. Root owns the full-suite and PostgreSQL runs.

#### Narrow residual notes

The policy import spelling limitation is resolved. `ImportFrom` now records only the resolved imported module/name, so both `from weft.core import queue_window` and `from weft.core.queue_window import QueueWindowRow` normalize under the same allowed prefix. A three-case parameterized test runs the actual architecture guard against temporary evidence modules: both allowed spellings pass and a forbidden sibling `task_state` fails. It does not duplicate the guard predicate as its oracle. Mypy/Ruff were rerun and remain clean.

The stale Overview implementation note remains flagged only: `00-Overview_and_Architecture.md` lines 171-172 calls root/core 'lazy compatibility exports', while lines 195-198 and `14-Python_API_Surfaces.md` [PY-4] establish metadata-only root/no old inventories and CLI/core markers. This review did not modify that note. Use the explicit current normative contract when assessing the retained guards.


## Final Integration Verification

Implementation is verified and uncommitted for user review. Plan metadata remains draft until that review/commit; this does not indicate pending implementation.

- All 229 Python files under tests are included in the strict mypy gate. Final inventory: 189 test modules, 2934 test definitions.
- 47 test definitions removed, excluding renamed/strengthened replacements. Detailed mappings above. No test file was deleted.
- Final isolated SQLite full run (`pytest -n 4 --cov=weft --cov-branch`): **4769 passed, 14 skipped**, 435.06s. The skips require PostgreSQL. Log: `/tmp/weft-pytest-final-isolated.txt`.
- Slow suite: **1 passed, 11 skipped**, 276.71s. The 288-task long-session integration passed; skipped cases require explicit live-provider opt-in. Log: `/tmp/weft-slow-final.txt`.
- PostgreSQL store/SQL/harness run: **132 passed, 1 skipped** (SQLite-only NOCASE catalog case), 9.50s. Includes no-cutoff retirement, resolving the independent SQL compatibility concern. Log: `/tmp/weft-postgres-audit.txt`.
- PostgreSQL added full-reconciliation and manual-context normalization cases: **2 passed**. Log: `/tmp/weft-postgres-added-paths.txt`.
- Mypy: **418 source files, no issues**. Ruff check, formatter (443 files), and suppression-registry check pass. No blanket test typing exemptions.
- Independent reviews: native cross-slice reviews plus read-only Claude CLI review; fixes and rejected recommendations recorded above.
- First combined full run had 4766 passes, 14 skips and four timeouts while jobs overlapped. All four passed an isolated rerun (7.97s), followed by the clean non-overlapping full run above. Timeout assertions were not weakened.

### Coverage and attribution

Across **143 byte-identical production files** compared with the baseline, covered lines increased from **19,894 to 19,907** (+13), and covered branches from **5,572 to 5,579** (+7), with identical denominators (22,795 statements; 7,710 branches). Overall measured line coverage increased from 87.09% to 87.21%, and branch coverage from 73.38% to 73.56%. The overall figures include concurrent changes to 12 production files and are not attributed solely to this audit.

Coverage comparison caught three actual lost paths, which were restored with direct behavioral tests: global reconciliation (not just targeted-TID reconciliation), runtime execute signatures without bundle_root, and plain-mapping broker config normalization. Isolated coverage probes and final full coverage confirm them. Remaining individual hit differences are in startup/ownership and worker-result interleavings; their tests were retained. Aggregate coverage is supporting evidence, not a substitute for the independent oracle review and mutation probes. Full comparison: `/tmp/weft-coverage-delta-final.txt`; source reports: `/tmp/weft-coverage-baseline.json` and `/tmp/weft-coverage-final.json`.

Existing and concurrent edits were preserved. No commit, push, or runtime production change was made for test typing. Required gate wiring changed only to include tests (and the previously omitted bin scope in release prechecks). The approved PyYAML stubs are development-only.
