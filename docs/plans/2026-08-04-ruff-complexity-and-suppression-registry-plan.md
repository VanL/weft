# Ruff Complexity And Suppression Registry Plan

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1], [TS-3], [TS-3.1]
Superseded by: none

Class: 5+P — the implementation will add normative repository verification
policy to the testing spec and materially change how future Python complexity
findings and Ruff suppressions are implemented, reviewed, and verified.

Plan type: implementation with spec revision.

Hardening: required. The change crosses the Ruff configuration, repository
Python-file discovery, CI, a new repository-tool CLI, source directives, tests,
and the normative testing spec. It must not change Weft runtime behavior or use
score-driven refactors to disturb the durable execution spine.

## 1. Goal

Enable Ruff `C901` across every tracked first-party Python source at McCabe
complexity 10, while treating the score as an audit signal rather than a design
verdict. Every retained finding will use a narrow local directive tied to a
human-reviewed suppression group. A repository-owned checker, adapted from
SimpleBroker, will reconcile normal Ruff, raw `--ignore-noqa` diagnostics,
source directives, approved cardinalities, and a generated symbol-keyed index.

The migration must make unexplained complexity impossible to add without
making cohesive Weft state owners worse merely to satisfy a number.

## 2. Requested Outcomes

- [x] Add `C901` to the normal Ruff gate with
  `lint.mccabe.max-complexity = 10`.
- [x] Change the CI lint invocation to repository discovery (`ruff check .`)
  without accidentally extending formatter ownership.
- [x] Make Ruff discover every tracked `.py`/`.pyi` file and every tracked
  Python-shebang tool under `bin/`, while excluding Bash tools.
- [x] Inventory and disposition every baseline C901 finding as simplify at a
  real ownership seam, retain as a cohesive owner, or move into a separately
  planned risky refactor.
- [x] Give every retained C901 finding one narrow local directive and one
  stable `RUFF-SUP-NNN` group whose durable row names the protected invariant,
  real proof, rejected alternatives, cardinality, and approval.
- [x] Adapt SimpleBroker's suppression-index tool rather than designing a
  second parser, grammar, or write path.
- [x] Key the generated audit index on
  `repository/path.py::Class.qualified_symbol`; retain physical lines only for
  raw diagnostic reconciliation and error reports.
- [x] Preserve a separate exact global inventory of every raw diagnostic
  exposed by enabled-rule `noqa` directives, including existing ungrouped
  `E402` and `F401` directives.
- [x] Make normal Ruff, the suppression-index check, formatter checks, and
  mypy part of the CI lint job in dependency order; keep the focused policy
  tests in CI's existing pytest matrix so they are not run twice.
- [x] Add no per-file ignore, blanket file directive, global C901 ignore,
  raised threshold, or baseline allowlist.
- [x] Change no public API, CLI behavior, TaskSpec shape, queue contract,
  state transition, persistence format, or runtime result.

## 3. Source Documents And Precedent

Weft owners:

- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1] owns the
  repository's current verification model and test-policy gates.
- `docs/specifications/07-System_Invariants.md`, especially the state,
  execution, observability, and implementation invariants, defines runtime
  boundaries that complexity cleanup must not disturb.
- `docs/agent-context/engineering-principles.md` principles 9 and 11 require
  boundary-first plans and reject file-size-driven decomposition.
- `docs/agent-context/runbooks/writing-plans.md` governs this plan, spec
  promotion, traceability, and the deviation log.
- `docs/agent-context/runbooks/hardening-plans.md` governs invariants,
  anti-mocking rules, rollback, and stop gates.
- `docs/agent-context/runbooks/testing-patterns.md` governs the real proof
  boundary and failing-test-first rule.
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md` governs the
  new repository tool's hostile-input and honest-exit floors.

SimpleBroker precedent, read at sibling commit
`6d6398f` during plan authoring:

- `../simplebroker/docs/plans/2026-07-29-complexity-and-state-machine-hardening-plan.md`
  explains the complexity-10 activation, exact audit, and retain-versus-split
  decision model. Its state-machine expansion is not part of this Weft plan.
- `../simplebroker/docs/plans/2026-07-30-ruff-suppression-index-generator-plan.md`
  explains stable group IDs, generated-index ownership, and the later move
  from line keys to qualified symbols.
- `../simplebroker/bin/ruff_suppression_index.py` is the implementation source
  to copy and adapt.
- `../simplebroker/tests/test_ruff_policy.py` and
  `../simplebroker/tests/test_ruff_suppression_index.py` are the firing-test
  sources to copy and adapt.
- `../simplebroker/docs/specs/01-development-documentation-operating-model.md`
  [DOM-10.1], [DOM-10.1.1] is rationale and wording precedent only. Weft's
  normative owner remains its own testing spec.

The SimpleBroker plan is historical precedent, not a Weft contract. Adapt its
end state, including the symbol-keyed revision, rather than reproducing its
intermediate line-keyed implementation.

## 4. Baseline Evidence

Spec and repository baseline:

- `3ada6ccb2c3c419119c44ed4426818aa43fc4abf` —
  `docs/specifications/08-Testing_Strategy.md`, `pyproject.toml`, CI, source,
  and tests at plan authoring time.
- The worktree was clean when the audit commands below ran.
- Ruff was `0.16.1` from `./.venv/bin/ruff`.

Current configuration and CI:

- `pyproject.toml` selects `E`, `W`, `F`, `I`, `B`, `C4`, and `UP`; it does
  not select `C901` and has no McCabe threshold.
- CI lints six explicit directory roots and does not lint `bin/`.
- `ruff check .` is clean under the current enabled rules.
- Ruff's default discovery sees `bin/launch_manager.py` and `bin/release.py`
  but not the six extensionless Python-shebang tools.

Raw audit at the baseline:

```text
current enabled-rule raw-noqa inventory: E402=22, F401=5
C901 under default Ruff discovery: 149 findings
C901 in extensionless Python tools omitted by default discovery: 3 findings
complete initial C901 inventory after discovery is corrected: 152 findings
```

The complete 152-finding distribution is:

| Surface | Findings |
|---|---:|
| `weft/` | 105 |
| `tests/` | 29 |
| `extensions/` | 12 |
| `integrations/` | 1 |
| `bin/` (`.py` plus Python-shebang tools) | 5 |

| Score band | Findings |
|---|---:|
| 11–12 | 61 |
| 13–15 | 39 |
| 16–20 | 31 |
| 21–30 | 13 |
| 31+ | 8 |

Reproduce before any migration edit:

```bash
./.venv/bin/ruff check --select C901 --output-format json .
./.venv/bin/ruff check --select C901 --output-format json \
  bin/check-doc-paths bin/check-dom15-fixtures bin/coalesce-check \
  bin/pytest-live-providers bin/pytest-pg bin/pytest-worker-count
./.venv/bin/ruff check --ignore-noqa --output-format json .
```

All three audit invocations are expected to exit 1 when findings are present.
Their JSON is evidence; it is not a success gate.

## 5. Proposed Spec Delta

Promotion strategy: **B — atomic**. Promote [TS-3] and [TS-3.1], the reviewed
human registry rows, Ruff configuration, local source pointers, repository
tool, policy tests, and reciprocal implementation mapping in one activation
slice. A partial landing would either claim a gate that does not exist or make
normal Ruff fail on every approved baseline finding.

Before the activation slice, Task 1 must append the exact disposition ledger
and proposed human registry rows to this plan. Those rows receive a scoped
independent review and explicit owner approval. They are not inferred or
generated during spec promotion. The policy text below is the exact proposed
normative delta; the reviewed rows are exact migration data added by the
required pre-activation plan revision.

Insert the following sections in
`docs/specifications/08-Testing_Strategy.md` after [TS-0] and before [TS-1]:

### Repository Static-Analysis Gate [TS-3]

> Weft's Python lint gate uses the rule families selected in `pyproject.toml`
> across every tracked first-party `.py`/`.pyi` file and Python-shebang
> repository tool. Ruff owns Python file discovery; configuration must include
> tracked extensionless Python tools explicitly and must not parse Bash tools
> as Python.
>
> Owner: `pyproject.toml` owns rule selection and discovery; the root CI lint
> job enforces it. Boundary: `weft/`, `tests/`, `integrations/`, `extensions/`,
> and Python tools under `bin/`. Verification:
> `tests/specs/test_ruff_policy.py` invokes the real repo-managed Ruff binary,
> compares effective discovery and rule selection with reviewed inventories,
> and proves each behavior-affecting policy setting fires. Required action: a
> Ruff version or rule-selection change must intentionally review and update
> the enabled-rule inventory before changing the lock or configuration.
>
> Requirements:
>
> - the root lint job uses repository discovery (`ruff check .`)
> - preview rules remain opt-in
> - global ignores remain limited to documented repository-wide conflicts;
>   per-file ignores are empty; other suppressions are local and narrow
> - formatter paths remain explicit; widening lint discovery must not
>   silently widen formatter ownership
> - the policy gate and source changes land atomically when activating a rule
>   with existing findings
>
> _Implementation mapping_: `pyproject.toml` owns Ruff rule selection, the
> McCabe threshold, ignores, and extensionless-Python discovery;
> `.github/workflows/test.yml` owns the ordered normal-Ruff,
> suppression-index, formatter, and mypy CI steps;
> `bin/ruff_suppression_index.py` parses [TS-3.1], invokes normal and raw Ruff,
> enforces C901 registration completeness and cardinality, and checks or
> atomically rewrites only the generated index;
> `tests/specs/test_ruff_policy.py` proves effective configuration, discovery,
> mutation behavior, and CI wiring; and
> `tests/specs/test_ruff_suppression_index.py` proves parser, reconciliation,
> symbol attribution, byte preservation, honest exits, and failure safety.
> The repository-tool module docstring cites [TS-3] and [TS-3.1] as its
> reciprocal implementation reference. The two policy-test module docstrings
> cite the section they prove.
>
> Ruff `C901` is enabled repository-wide with
> `lint.mccabe.max-complexity = 10`. The score is a visibility signal, not a
> design verdict. Each finding must either be simplified at a real ownership
> seam or carry a narrow local `C901` suppression registered in [TS-3.1]. The
> registry must explain the protected coupling, debugging locality, or
> semantic risk; name real behavioral proof; record rejected decompositions
> and approval; and assign a stable suppression-group ID. A cohesive parser,
> state owner, lifecycle frame, reducer, checklist, test protocol, or
> concurrency proof must not be fragmented merely to lower its score.
>
> The policy gate runs normal Ruff and a raw audit with `--ignore-noqa`.
> Source directives, human-owned [TS-3.1] groups, the generated symbol index,
> and raw findings at tagged locations using Ruff's `noqa_row` must reconcile
> exactly, including each group's approved directive and raw-diagnostic
> cardinalities. In addition, every raw `C901` diagnostic must resolve to a
> tagged, approved [TS-3.1] directive at its `noqa_row`; the global aggregate
> is not sufficient proof of registration. A new unsuppressed finding, an
> untagged or unregistered `C901` suppression, an unregistered tagged
> directive, an unknown or empty group, a cardinality change, a stale
> directive, a stale generated index, or a mismatched raw finding fails
> verification.
>
> A separate global raw-diagnostic inventory covers every enabled-rule local
> `noqa`, including reasoned suppressions outside [TS-3.1]. It is an exact
> aggregate by rule code. Aggregate changes fail verification; a same-code
> remove/add swap remains a source-review concern rather than receiving false
> identity semantics. Per-file ignores, global C901 ignores, threshold raises,
> blanket file directives, and baseline allowlists are prohibited.

### Approved Ruff Suppression Registry [TS-3.1]

> This section owns approved local exceptions to [TS-3]. A plan may propose or
> review a candidate, but it must not become the lasting source of truth for
> an adopted exception.
>
> Owner: this section owns each stable suppression group, human-reviewed
> rationale, and approved cardinality. The local directive owns rule codes and
> the stable group pointer. The generated index owns only derived paths,
> qualified symbols, actual directive counts, and raw-diagnostic counts.
> Boundary: only the rules, cardinality, invariant, and locations covered by
> the approved group. Verification: the named real proof, `ruff check .`, and
> `./.venv/bin/python bin/ruff_suppression_index.py --check`. Required action:
> obtain explicit review before adding, regrouping, growing, or shrinking a
> suppression; update the human row, cardinality, and source pointer together;
> then regenerate only the delimited derived index with
> `./.venv/bin/python bin/ruff_suppression_index.py --write`.
>
> The approved local form is
> `# noqa: <codes> approved [TS-3.1] [RUFF-SUP-NNN] exception`. The stable group
> points to the single durable full reason; source comments do not duplicate
> it. Group IDs are unique and match `RUFF-SUP-[0-9]{3}`. Every group has at
> least one live source directive. Human rows contain `Group`, `Rules`,
> `Approved cardinality`, `Protected invariant`, `Real proof`, `Rejected
> alternatives`, and `Approval`.
>
> The section also owns one lexically sorted
> ``Global raw-`noqa` inventory:`` line containing backticked `CODE=count`
> entries for every diagnostic exposed by `--ignore-noqa`. The backticks
> around `noqa` are part of the canonical parser grammar. This aggregate is a
> tripwire, not a second identity registry.
>
> The generated index is enclosed by unique begin/end markers. It renders one
> deduplicated `path::qualified_symbol` site per group, sorted by group ID and
> site. A symbol is the outermost enclosing function, qualified by class names,
> or `<module>`; decorator lines belong to their function. Physical line
> remains the internal identity for matching Ruff diagnostics, duplicate
> detection, and error messages. Content outside the generated markers is
> human-owned and must remain byte-for-byte unchanged during regeneration.
>
> The repository tool must refuse to write if normal Ruff is unclean, a source
> or spec marker is malformed, a group is unknown or empty, a rule or
> cardinality differs, a raw diagnostic does not match its directive, the
> global inventory differs, any raw `C901` diagnostic lacks a tagged approved
> directive at the same `noqa_row`, or discovered Python source is unreadable
> or syntactically invalid. Policy mismatches exit 1. Anticipated invocation,
> decoding, Ruff, and atomic-replacement failures exit 2 with a one-line
> diagnostic and no traceback. Both classes leave the spec byte-for-byte
> unchanged. Unexpected programming defects retain a traceback as bug
> evidence.

The activation slice inserts the reviewed human table, the exact global
inventory, and the generated index immediately after this prose. It also adds
this plan to the spec's `## Related Plans` section and records the promotion
baseline identifier below.

## 5A. Task 1 Exact C901 Disposition Ledger

Exact Task 1 baseline at `3ada6ccb2c3c419119c44ed4426818aa43fc4abf`:
**152 findings; S1 = 5, S2 = 42, S3 = 105**. The independently audited
`weft/core` slice reconciles exactly **79 findings; S1 = 1, S2 = 35, S3 = 43**;
the application/command slice reconciles **26 findings; S1 = 4, S2 = 5,
S3 = 17**; and tests/integrations/extensions/tools reconcile **47 findings;
S1 = 0, S2 = 2, S3 = 45**. The 123 proposed groups reconcile to 152
directives and 152 raw C901 diagnostics. Task 2's clean outside review passed,
and the owner approved all 123 groups on 2026-08-05.

The ledger was mechanically compared with live Ruff JSON plus AST
outermost-symbol attribution: 152 actual identities, 152 planned identities,
no missing row, no extra row, and no duplicate identity. All 123 proposed
human rows use the copied parser's canonical grammar and match their ledger
directive/raw cardinalities exactly.
For nested functions, the qualified symbol follows [TS-3.1]'s planned
outermost-function attribution; rows 59 and 60 are distinct Ruff diagnostics
owned by the same symbol.

### Core ledger (79)

| # | Path::qualified symbol | Score | Disposition | Stable group ID / theme | Governing contract and closest real proof |
|---:|---|---:|---|---|---|
| 1 | `weft/core/agents/provider_cli/container_runtime.py::resolve_provider_container_runtime` | 12 | S3 | `RUFF-SUP-001` provider container runtime resolution | `docs/specifications/13-Agent_Runtime.md` [AR-0.1], [AR-7]; `tests/core/test_provider_cli_container_runtime.py` |
| 2 | `weft/core/agents/provider_cli/registry.py::CodexProvider.validate_options` | 15 | S3 | `RUFF-SUP-002` Codex provider option policy | `docs/specifications/13-Agent_Runtime.md` [AR-5]; `tests/core/test_provider_cli_settings.py` |
| 3 | `weft/core/agents/provider_cli/registry.py::CodexProvider.resolve_options` | 11 | S3 | `RUFF-SUP-002` Codex provider option policy | `docs/specifications/13-Agent_Runtime.md` [AR-5]; `tests/core/test_provider_cli_settings.py` |
| 4 | `weft/core/agents/provider_cli/registry.py::CodexProvider.build_invocation` | 11 | S3 | `RUFF-SUP-003` Codex invocation assembly | `docs/specifications/13-Agent_Runtime.md` [AR-5]; `tests/core/test_provider_cli_execution.py` |
| 5 | `weft/core/agents/provider_cli/registry.py::CodexProvider.build_session_invocation` | 16 | S3 | `RUFF-SUP-003` Codex invocation assembly | `docs/specifications/13-Agent_Runtime.md` [AR-6]; `tests/core/test_provider_cli_session_backend.py` |
| 6 | `weft/core/endpoints.py::list_resolved_endpoints` | 13 | S2 | `RUFF-SUP-004` endpoint live-owner resolution | `docs/specifications/01-Core_Components.md` [CC-2.4.1]; `tests/tasks/test_task_endpoints.py` |
| 7 | `weft/core/manager.py::Manager._register_manager` | 17 | S2 | `RUFF-SUP-005` manager registry convergence | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 8 | `weft/core/manager.py::Manager._prune_expired_manager_registry_entries` | 13 | S3 | `RUFF-SUP-005` manager registry convergence | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 9 | `weft/core/manager.py::Manager._self_registry_status_timestamp` | 11 | S3 | `RUFF-SUP-005` manager registry convergence | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 10 | `weft/core/manager.py::Manager._prune_older_self_registry_entries` | 13 | S3 | `RUFF-SUP-005` manager registry convergence | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 11 | `weft/core/manager.py::Manager._unregister_manager` | 14 | S2 | `RUFF-SUP-005` manager registry convergence | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 12 | `weft/core/manager.py::Manager._manager_record_liveness` | 11 | S3 | `RUFF-SUP-006` manager liveness proof | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 13 | `weft/core/manager.py::Manager._active_dispatch_manager_records` | 15 | S2 | `RUFF-SUP-007` manager leadership resolution | `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-3]; `tests/core/test_manager.py` |
| 14 | `weft/core/manager.py::Manager._maybe_yield_leadership` | 14 | S2 | `RUFF-SUP-007` manager leadership resolution | `docs/specifications/03-Manager_Architecture.md` [MA-1.4]; `tests/core/test_manager.py` |
| 15 | `weft/core/manager.py::Manager._child_has_exited` | 11 | S3 | `RUFF-SUP-008` manager child-exit proof | `docs/specifications/07-System_Invariants.md` [IMPL.10]; `tests/core/test_manager.py` |
| 16 | `weft/core/manager.py::Manager._child_terminal_proof_visible` | 11 | S3 | `RUFF-SUP-008` manager child-exit proof | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/core/test_manager.py` |
| 17 | `weft/core/manager.py::Manager._cleanup_children` | 15 | S2 | `RUFF-SUP-009` manager child cleanup | `docs/specifications/03-Manager_Architecture.md` [MA-1]; `tests/core/test_manager.py` |
| 18 | `weft/core/manager.py::Manager._terminate_children` | 17 | S2 | `RUFF-SUP-009` manager child cleanup | `docs/specifications/07-System_Invariants.md` [IMPL.10]; `tests/core/test_manager.py` |
| 19 | `weft/core/manager.py::Manager._cleanup_stale_internal_reserved_queues` | 12 | S2 | `RUFF-SUP-010` manager reservation lifecycle | `docs/specifications/05-Message_Flow_and_State.md` [MF-6]; `tests/core/test_manager.py` |
| 20 | `weft/core/manager.py::Manager._apply_spawn_reserved_policy` | 12 | S3 | `RUFF-SUP-010` manager reservation lifecycle | `docs/specifications/02-TaskSpec.md` [TS-1]; `tests/specs/manager_architecture/test_spawn_retry.py` |
| 21 | `weft/core/manager.py::Manager._handle_work_message` | 11 | S2 | `RUFF-SUP-011` manager spawn dispatch | `docs/specifications/03-Manager_Architecture.md` [MA-1.1], [MA-2]; `tests/specs/manager_architecture/test_spawn_retry.py` |
| 22 | `weft/core/manager.py::Manager._terminate_duplicate_service_candidates` | 12 | S2 | `RUFF-SUP-012` manager service supervision | `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]; `tests/core/test_service_convergence.py` |
| 23 | `weft/core/manager.py::Manager._service_candidate_from_service_owner_record` | 14 | S3 | `RUFF-SUP-012` manager service supervision | `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]; `tests/core/test_service_convergence.py` |
| 24 | `weft/core/manager.py::Manager._reconcile_managed_services` | 18 | S2 | `RUFF-SUP-012` manager service supervision | `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]; `tests/core/test_service_convergence.py` |
| 25 | `weft/core/manager.py::Manager._build_autostart_spawn_payload` | 17 | S3 | `RUFF-SUP-013` manager autostart compilation | `docs/specifications/03-Manager_Architecture.md` [MA-1.6]; `tests/core/test_manager.py` |
| 26 | `weft/core/manager.py::Manager._managed_service_convergence_active_reasons` | 13 | S3 | `RUFF-SUP-012` manager service supervision | `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]; `tests/core/test_service_convergence.py` |
| 27 | `weft/core/manager.py::Manager._process_reactor_turn` | 21 | S2 | `RUFF-SUP-014` manager reactor turn | `docs/specifications/01-Core_Components.md` [CC-2.2.1]; `docs/specifications/05-Message_Flow_and_State.md` [MF-6]; `tests/tasks/test_task_execution.py` |
| 28 | `weft/core/manager_runtime.py::_snapshot_registry` | 13 | S2 | `RUFF-SUP-015` manager runtime registry view | `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-3]; `tests/commands/test_manager_commands.py` |
| 29 | `weft/core/manager_runtime.py::_manager_record_diagnostic` | 13 | S3 | `RUFF-SUP-016` manager diagnostic classification | `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-3]; `tests/commands/test_manager_commands.py` |
| 30 | `weft/core/manager_runtime.py::_mark_manager_stopped` | 15 | S2 | `RUFF-SUP-017` manager runtime stop | `docs/specifications/03-Manager_Architecture.md` [MA-3]; `tests/commands/test_manager_commands.py` |
| 31 | `weft/core/manager_runtime.py::_await_manager_stop_confirmation` | 14 | S2 | `RUFF-SUP-017` manager runtime stop | `docs/specifications/03-Manager_Architecture.md` [MA-3]; `tests/commands/test_manager_commands.py` |
| 32 | `weft/core/manager_runtime.py::_start_manager` | 19 | S2 | `RUFF-SUP-018` manager runtime start | `docs/specifications/03-Manager_Architecture.md` [MA-3]; `tests/commands/test_manager_commands.py`; `tests/commands/test_run.py` |
| 33 | `weft/core/manager_runtime.py::_stop_manager` | 19 | S2 | `RUFF-SUP-017` manager runtime stop | `docs/specifications/03-Manager_Architecture.md` [MA-3]; `tests/commands/test_manager_commands.py` |
| 34 | `weft/core/manager_services.py::reduce_managed_service_state` | 12 | S3 | `RUFF-SUP-019` managed-service reducer | `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]; `tests/core/test_manager_services.py` |
| 35 | `weft/core/monitor/lifetime_report.py::_taskspec_from_collation` | 12 | S3 | `RUFF-SUP-020` monitor lifetime-report projection | `docs/specifications/07-System_Invariants.md` [OBS.13.12], [OBS.17]; `tests/core/monitor/test_lifetime_report.py` |
| 36 | `weft/core/monitor/runtime.py::TaskMonitorRuntimeConfig.from_config` | 28 | S1 | `RUFF-SUP-021` temporary monitor config parsing (Task 5) | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/core/test_task_monitoring.py` |
| 37 | `weft/core/monitor/store.py::MonitorStore.record_task_log_updates` | 11 | S3 | `RUFF-SUP-022` monitor-store ingest transaction | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]; `tests/core/test_monitor_store.py` |
| 38 | `weft/core/monitor/task_log_collation.py::collate_next_task_log_group` | 11 | S3 | `RUFF-SUP-023` monitor task-log collation | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/core/test_monitor_collation.py` |
| 39 | `weft/core/monitor/task_monitor.py::TaskMonitor._worker_local_monitor_clone` | 15 | S3 | `RUFF-SUP-024` monitor worker snapshot | `docs/specifications/07-System_Invariants.md` [IMPL.11]; `tests/tasks/test_task_monitor.py` |
| 40 | `weft/core/monitor/task_monitor.py::TaskMonitor._close_worker_local_resources` | 11 | S3 | `RUFF-SUP-024` monitor worker snapshot | `docs/specifications/07-System_Invariants.md` [IMPL.11]; `tests/tasks/test_task_monitor.py` |
| 41 | `weft/core/monitor/task_monitor.py::TaskMonitor._ingest_retained_task_log_rows` | 20 | S2 | `RUFF-SUP-025` monitor task-log ingest and recovery | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]; `tests/core/test_task_monitor_cleanup.py` |
| 42 | `weft/core/monitor/task_monitor.py::TaskMonitor._recover_pre_checkpoint_task_log_rows` | 16 | S2 | `RUFF-SUP-025` monitor task-log ingest and recovery | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]; `tests/tasks/test_task_monitor.py` |
| 43 | `weft/core/monitor/task_monitor.py::TaskMonitor._emit_monitor_store_summaries` | 15 | S2 | `RUFF-SUP-026` monitor summary disposition | `docs/specifications/07-System_Invariants.md` [OBS.13.7]; `tests/tasks/test_task_monitor.py` |
| 44 | `weft/core/monitor/task_monitor.py::TaskMonitor._run_terminal_control_cleanup_slice` | 19 | S2 | `RUFF-SUP-027` monitor terminal-runtime cleanup | `docs/specifications/07-System_Invariants.md` [OBS.13.7]; `tests/core/monitor/policies/test_runtime_control.py` |
| 45 | `weft/core/monitor/task_monitor.py::TaskMonitor._run_reserved_cleanup_slice` | 20 | S2 | `RUFF-SUP-056` monitor reserved-runtime cleanup | `docs/specifications/07-System_Invariants.md` [OBS.13.5], [OBS.13.7]; `tests/core/monitor/policies/test_runtime_control.py` |
| 46 | `weft/core/monitor/task_monitor.py::TaskMonitor._trim_manager_task_spawned_task_log_rows` | 13 | S2 | `RUFF-SUP-028` monitor task-log retirement | `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]; `tests/tasks/test_task_monitor.py` |
| 47 | `weft/core/monitor/task_monitor.py::TaskMonitor._recover_orphan_task_log_rows` | 14 | S2 | `RUFF-SUP-057` monitor orphan task-log recovery | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]; `tests/tasks/test_task_monitor.py` |
| 48 | `weft/core/monitor/task_monitor.py::TaskMonitor._coalesce_and_delete_dead_task_log_rows_for_tids` | 11 | S2 | `RUFF-SUP-058` monitor dead-TID task-log coalescing | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/core/monitor/policies/test_dead_task.py` |
| 49 | `weft/core/outbox.py::process_outbox_message` | 13 | S3 | `RUFF-SUP-029` outbox envelope decoding | `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-5]; `tests/commands/test_result.py` |
| 50 | `weft/core/pipelines.py::_merge_stage_defaults` | 14 | S3 | `RUFF-SUP-030` pipeline stage-default merge | `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-2.7], [PL-3.2]; `tests/core/test_pipelines.py` |
| 51 | `weft/core/pruning/apply.py::apply_exact_prune_candidates` | 24 | S2 | `RUFF-SUP-031` exact-prune application | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16], [OBS.17]; `tests/core/test_pruning_apply.py` |
| 52 | `weft/core/pruning/retention.py::_task_log_candidates` | 16 | S3 | `RUFF-SUP-032` task-log retention selection | `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.14], [OBS.16]; `tests/commands/test_retention_prune.py` |
| 53 | `weft/core/pruning/runtime.py::_manager_candidates` | 12 | S3 | `RUFF-SUP-033` runtime-registry prune selection | `docs/specifications/07-System_Invariants.md` [OBS.6], [OBS.13]; `tests/commands/test_runtime_prune.py` |
| 54 | `weft/core/pruning/runtime.py::_endpoint_candidates` | 12 | S3 | `RUFF-SUP-059` endpoint-registry prune selection | `docs/specifications/07-System_Invariants.md` [OBS.6], [OBS.13]; `tests/commands/test_runtime_prune.py` |
| 55 | `weft/core/runners/host.py::HostTaskRunner._run_one_shot_terminal_handoff` | 40 | S2 | `RUFF-SUP-034` host terminal handoff | `docs/specifications/07-System_Invariants.md` [EXEC.5]-[EXEC.10]; `tests/tasks/test_runner.py` |
| 56 | `weft/core/runners/host.py::HostTaskRunner.start_session` | 13 | S3 | `RUFF-SUP-035` host session startup | `docs/specifications/06-Resource_Management.md` [RM-5], [RM-5.1]; `tests/tasks/test_runner.py` |
| 57 | `weft/core/runners/host.py::HostTaskRunner.start_agent_session` | 11 | S3 | `RUFF-SUP-035` host session startup | `docs/specifications/13-Agent_Runtime.md` [AR-6]; `tests/tasks/test_runner.py` |
| 58 | `weft/core/runners/subprocess_runner.py::run_monitored_subprocess` | 25 | S2 | `RUFF-SUP-036` subprocess monitor loop | `docs/specifications/06-Resource_Management.md` [RM-5.1], [RM-5.2]; `tests/core/test_subprocess_runner.py` |
| 59 | `weft/core/runners/subprocess_runner.py::_start_stream_reader` | 14 | S3 | `RUFF-SUP-037` subprocess stream decoding (outer diagnostic) | `docs/specifications/06-Resource_Management.md` [RM-5.1]; `tests/core/test_subprocess_runner.py` |
| 60 | `weft/core/runners/subprocess_runner.py::_start_stream_reader` | 13 | S3 | `RUFF-SUP-037` subprocess stream decoding (nested `_reader` diagnostic) | `docs/specifications/06-Resource_Management.md` [RM-5.1]; `tests/core/test_subprocess_runner.py` |
| 61 | `weft/core/state_machines.py::StateMachine._validate` | 19 | S3 | `RUFF-SUP-038` state-machine definition validation | `docs/specifications/07-System_Invariants.md` [STATE.1]-[STATE.6], [MANAGER.15]; `tests/core/test_state_machines.py` |
| 62 | `weft/core/task_evidence.py::known_tid_evidence` | 15 | S3 | `RUFF-SUP-039` known-TID evidence reconciliation | `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-5]; `tests/commands/test_task_evidence.py` |
| 63 | `weft/core/tasks/base.py::BaseTask._validate_reactor_topology` | 12 | S3 | `RUFF-SUP-040` task-reactor topology validation | `docs/specifications/07-System_Invariants.md` [QUEUE.7]; `tests/tasks/test_task_execution.py` |
| 64 | `weft/core/tasks/base.py::BaseTask.run_until_stopped` | 18 | S2 | `RUFF-SUP-041` task-reactor drive loop | `docs/specifications/01-Core_Components.md` [CC-2.2.1], [CC-2.5]; `tests/tasks/test_task_execution.py` |
| 65 | `weft/core/tasks/consumer.py::Consumer._ensure_outcome_ok` | 17 | S3 | `RUFF-SUP-042` consumer outcome finalization | `docs/specifications/06-Resource_Management.md` [RM-1], [RM-2]; `tests/tasks/test_consumer_terminal_events.py` |
| 66 | `weft/core/tasks/heartbeat.py::HeartbeatTask._emit_due_registrations` | 11 | S3 | `RUFF-SUP-043` heartbeat due emission | `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1], [MF-6]; `tests/tasks/test_heartbeat.py` |
| 67 | `weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_finalize_session` | 12 | S2 | `RUFF-SUP-044` interactive session finalization | `docs/specifications/05-Message_Flow_and_State.md` [MF-2]; `tests/tasks/test_task_interactive.py` |
| 68 | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._apply_topology_mutation_on_owner` | 18 | S2 | `RUFF-SUP-045` watcher topology mutation | `docs/specifications/07-System_Invariants.md` [QUEUE.8]; `tests/tasks/test_multiqueue_watcher.py` |
| 69 | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._apply_pending_topology_mutations` | 13 | S2 | `RUFF-SUP-045` watcher topology mutation | `docs/specifications/07-System_Invariants.md` [QUEUE.8]; `tests/tasks/test_multiqueue_watcher.py` |
| 70 | `weft/core/tasks/service.py::ServiceTask._stop_service_worker` | 16 | S2 | `RUFF-SUP-046` service-worker shutdown | `docs/specifications/07-System_Invariants.md` [IMPL.8], [IMPL.9]; `tests/tasks/test_service_task.py` |
| 71 | `weft/core/tasks/sessions.py::AgentSession.wait_ready` | 22 | S3 | `RUFF-SUP-047` readiness-payload classification | `docs/specifications/13-Agent_Runtime.md` [AR-6], [AR-9]; `tests/tasks/test_runner.py` |
| 72 | `weft/core/tasks/sessions.py::AgentSession.execute` | 33 | S2 | `RUFF-SUP-048` agent-session terminal handoff | `docs/specifications/13-Agent_Runtime.md` [AR-6], [AR-9]; `tests/tasks/test_runner.py` |
| 73 | `weft/core/tasks/sessions.py::AgentSession.terminate` | 14 | S2 | `RUFF-SUP-049` agent-session termination | `docs/specifications/06-Resource_Management.md` [RM-5.1], [RM-5.2]; `tests/tasks/test_runner.py` |
| 74 | `weft/core/taskspec/model.py::resolve_taskspec_payload` | 27 | S3 | `RUFF-SUP-050` TaskSpec resolution boundary | `docs/specifications/02-TaskSpec.md` [TS-1]; `tests/taskspec/test_taskspec_properties.py` |
| 75 | `weft/core/taskspec/model.py::AgentSection.validate_runtime_constraints` | 12 | S3 | `RUFF-SUP-051` TaskSpec agent validation | `docs/specifications/13-Agent_Runtime.md` [AR-2.2]; `tests/specs/taskspec/test_agent_taskspec.py` |
| 76 | `weft/core/taskspec/model.py::SpecSection.validate_target` | 13 | S3 | `RUFF-SUP-052` TaskSpec target validation | `docs/specifications/02-TaskSpec.md` [TS-1]; `tests/taskspec/test_taskspec.py` |
| 77 | `weft/core/taskspec/model.py::StateSection.validate_state_consistency` | 16 | S3 | `RUFF-SUP-053` TaskSpec state validation | `docs/specifications/02-TaskSpec.md` [TS-1]; `tests/specs/taskspec/test_state_transitions.py` |
| 78 | `weft/core/taskspec/model.py::TaskSpec._validate_strict_requirements` | 24 | S3 | `RUFF-SUP-054` TaskSpec strict resolution validation | `docs/specifications/02-TaskSpec.md` [TS-1]; `tests/taskspec/test_taskspec.py` |
| 79 | `weft/core/taskspec/run_input.py::parse_declared_option_args` | 18 | S3 | `RUFF-SUP-055` TaskSpec declared-option parser | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `tests/core/test_spec_run_input.py` |

### Application and command ledger (26)

| # | Symbol | Score | Disposition | Proposed group | Governing proof |
|---:|---|---:|---|---|---|
| 1 | `weft/_constants.py::_normalize_weft_override_value` | 69 | S1 | `RUFF-SUP-101` temporary explicit override normalization (Task 5) | `docs/specifications/04-SimpleBroker_Integration.md` configuration compilation contract; `tests/system/test_constants.py` |
| 2 | `weft/cli/app.py::task_status` | 12 | S1 | `RUFF-SUP-102` temporary task-status process augmentation and rendering (Task 5) | `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]; `tests/cli/test_status.py`; `tests/commands/test_status.py` |
| 3 | `weft/cli/validate_taskspec.py::_display_taskspec_summary` | 11 | S1 | `RUFF-SUP-103` temporary TaskSpec validation summary rendering (Task 5) | `docs/specifications/10-CLI_Interface.md` [CLI-1.4.1]; `tests/cli/test_cli_validate.py`; `tests/cli/test_commands.py` |
| 4 | `weft/commands/_load_support.py::ImportReport.format_preview` | 12 | S3 | `RUFF-SUP-104` dump-import preview completeness and truncation | `docs/specifications/10-CLI_Interface.md` [CLI-6]; `tests/commands/test_dump_load.py` |
| 5 | `weft/commands/_load_support.py::_parse_import_file` | 20 | S3 | `RUFF-SUP-105` dump header and record parsing | `docs/specifications/10-CLI_Interface.md` [CLI-6]; `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2], [SB-0.4]; `tests/commands/test_dump_load.py` |
| 6 | `weft/commands/_result_wait.py::await_one_shot_result` | 34 | S2 | `RUFF-SUP-106` one-shot result evidence and grace state | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `docs/specifications/07-System_Invariants.md` [OBS.3], [OBS.14], [IMPL.1]; `tests/commands/test_result.py` |
| 7 | `weft/commands/events.py::iter_task_realtime_events` | 35 | S2 | `RUFF-SUP-107` non-consuming realtime event stream | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/commands/test_result.py`; `tests/core/test_ops_shared.py` |
| 8 | `weft/commands/result.py::_await_result_materialization` | 15 | S3 | `RUFF-SUP-108` result-surface materialization race | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/commands/test_result.py`; `tests/core/test_ops_shared.py` |
| 9 | `weft/commands/result.py::_await_single_result` | 42 | S2 | `RUFF-SUP-109` persistent result batch-boundary state | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `docs/specifications/07-System_Invariants.md` [OBS.14]; `tests/commands/test_result.py` |
| 10 | `weft/commands/result.py::cmd_result` | 21 | S1 | `RUFF-SUP-110` temporary result-command validation and rendering (Task 5) | `docs/specifications/10-CLI_Interface.md` [CLI-1.2]; `tests/commands/test_result.py`; `tests/architecture/test_import_boundaries.py` |
| 11 | `weft/commands/run.py::_run_interactive_session` | 55 | S2 | `RUFF-SUP-111` interactive queue-session lifecycle | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/commands/test_run.py`; `tests/commands/test_interactive_client.py` |
| 12 | `weft/commands/run.py::render_run_execution_result` | 13 | S3 | `RUFF-SUP-112` run-result rendering and exit mapping | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `tests/commands/test_run.py` |
| 13 | `weft/commands/run.py::_execute_inline` | 14 | S3 | `RUFF-SUP-113` inline construction, submission, and wait orchestration | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-5]; `tests/commands/test_run.py`; `tests/cli/test_cli_run.py` |
| 14 | `weft/commands/run.py::execute_run` | 16 | S3 | `RUFF-SUP-114` mutually exclusive run-mode dispatch | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `tests/commands/test_run.py`; `tests/cli/test_cli_run.py` |
| 15 | `weft/commands/submission.py::apply_submit_overrides` | 18 | S3 | `RUFF-SUP-115` public TaskSpec submit overrides | `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-6]; `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1], [PL-4.1]; `tests/core/test_client.py`; `tests/commands/test_submission.py` |
| 16 | `weft/commands/submission.py::ensure_manager_after_submission` | 14 | S3 | `RUFF-SUP-116` queue-first submission reconciliation | `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-6]; `tests/commands/test_run.py`; `tests/core/test_ops_shared.py` |
| 17 | `weft/commands/system.py::_stale_liveness_reason` | 11 | S3 | `RUFF-SUP-117` task and service stale-liveness classification | `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]; `docs/specifications/07-System_Invariants.md` observability invariants; `tests/commands/test_task_snapshot_reducer.py`; `tests/commands/test_status.py` |
| 18 | `weft/commands/system.py::_collect_internal_service_snapshots` | 12 | S3 | `RUFF-SUP-118` internal-service evidence aggregation | `docs/specifications/03-Manager_Architecture.md` [MA-1.6a]; `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]; `tests/commands/test_status.py` |
| 19 | `weft/commands/system.py::_watch_task_events` | 12 | S3 | `RUFF-SUP-119` status-watch cursor, filtering, and rendering | `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/commands/test_status.py` |
| 20 | `weft/commands/task_monitor.py::run_task_monitor` | 11 | S3 | `RUFF-SUP-120` task-monitor scan, sink, and checkpoint pass | `docs/specifications/10-CLI_Interface.md` [CLI-6]; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `docs/specifications/07-System_Invariants.md` [OBS.13]; `tests/commands/test_task_monitor.py` |
| 21 | `weft/commands/tasks.py::_await_control_surface` | 23 | S2 | `RUFF-SUP-121` dynamic control-surface terminal wait | `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/commands/test_task_commands.py` |
| 22 | `weft/helpers/__init__.py::read_limited_stdin` | 12 | S3 | `RUFF-SUP-122` bounded binary and text stdin decoding | `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]; `tests/cli/test_cli_run.py` |
| 23 | `weft/helpers/__init__.py::iter_queue_entries` | 11 | S3 | `RUFF-SUP-123` broker generator compatibility, bounds, and closure | `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2]; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]; `tests/system/test_helpers.py`; `tests/core/test_task_log_scanner.py` |
| 24 | `weft/helpers/__init__.py::terminate_process_tree` | 13 | S3 | `RUFF-SUP-124` descendant-first process-tree termination | `docs/specifications/06-Resource_Management.md` process-tree escalation contract; `tests/helpers/weft_harness.py`; `tests/commands/test_task_commands.py`; `tests/tasks/test_runner.py` |
| 25 | `weft/helpers/__init__.py::write_file_atomically` | 13 | S3 | `RUFF-SUP-125` atomic write retry, cleanup, and fallback | `tests/system/test_helpers.py` |
| 26 | `weft/helpers/__init__.py::_format_duration` | 11 | S3 | `RUFF-SUP-126` relative-duration thresholds and unit selection | `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]; `tests/commands/test_status.py`; `tests/cli/test_status.py` |

### Tests, integrations, extensions, and tools ledger (47)

| # | Path::qualified symbol | Score | Disposition | Stable group ID / theme | Governing proof, protected invariant, and rejected split |
|---:|---|---:|---|---|---|
| 1 | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_uses_cached_image_tag_returned_by_ensure_agent_image` | 12 | S3 | `RUFF-SUP-201` Docker agent runner protocol tests | `extensions/weft_docker/weft_docker/agent_runner.py`; `docs/specifications/13-Agent_Runtime.md` [AR-7]. The prepared image tag and complete runtime setup reach container creation. Keep the stateful fake Docker protocol local to the test. |
| 2 | `extensions/weft_docker/tests/test_agent_runner.py::test_agent_runner_reports_cancel_requested_as_cancelled` | 12 | S3 | `RUFF-SUP-201` Docker agent runner protocol tests | Same proof family. Cancellation kills the live container and reports `cancelled`, not generic failure. Do not extract the temporal fake-container sequence. |
| 3 | `extensions/weft_docker/weft_docker/agent_runner.py::DockerProviderCLIRunner.run_with_hooks` | 24 | S2 | `RUFF-SUP-202` Docker provider lifecycle frame | `extensions/weft_docker/tests/test_agent_runner.py`; `extensions/weft_docker/tests/test_container_runtime_resolution.py`; [AR-7], [AR-9]. Preparation, handle publication, streaming, cancellation, outcome mapping, and cleanup remain ordered. Any decomposition needs a separate hardened plan; reject score-driven state passing. |
| 4 | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_work_item_mounts` | 11 | S3 | `RUFF-SUP-203` Docker work-item mount validation | `extensions/weft_docker/tests/test_agent_runner.py`; `extensions/weft_docker/tests/test_docker_plugin.py`; `docs/specifications/02-TaskSpec.md` [TS-1.3]. Preserve exact keys, types, defaults, and indexed errors. Per-field helpers would separate errors from mount context. |
| 5 | `extensions/weft_docker/weft_docker/agent_runner.py::_resolve_work_item_mounts` | 12 | S3 | `RUFF-SUP-203` Docker work-item mount validation | Same proof family; [AR-7]. Required/optional references, absolute paths, filesystem kinds, and output normalization share one mount context. Reject staged helpers that duplicate it. |
| 6 | `extensions/weft_docker/weft_docker/plugin.py::DockerCommandRunner._build_docker_command` | 11 | S3 | `RUFF-SUP-204` Docker argv assembly | `tests/tasks/test_command_runner_parity.py`; `extensions/weft_docker/tests/test_docker_plugin.py`; `docs/specifications/06-Resource_Management.md`. Limits, network, mounts, cwd, image, and inner argv form one shell-free ordered command. Do not hide precedence behind append helpers. |
| 7 | `extensions/weft_docker/weft_docker/plugin.py::DockerRunnerPlugin.validate_taskspec` | 15 | S3 | `RUFF-SUP-205` Docker TaskSpec boundary validation | `extensions/weft_docker/tests/test_docker_plugin.py`; `extensions/weft_docker/tests/test_container_profiles.py`; [TS-1.3]. Command and one-shot agent lanes reject unsupported combinations in exact order. Distributed validators would obscure precedence. |
| 8 | `extensions/weft_docker/weft_docker/plugin.py::DockerRunnerPlugin._validate_agent_taskspec` | 14 | S3 | `RUFF-SUP-205` Docker TaskSpec boundary validation | `extensions/weft_docker/tests/test_docker_plugin.py`; `extensions/weft_docker/tests/test_agent_runner.py`; [AR-7]. The narrow supported provider lane, descriptor, recipe, mount, and preflight checks stay together. Reject option-family fragmentation. |
| 9 | `extensions/weft_docker/weft_docker/plugin.py::_describe_runtime` | 11 | S3 | `RUFF-SUP-206` Docker runtime observation and lookup | `extensions/weft_docker/tests/test_docker_plugin.py`; runner-handle contract in `docs/specifications/01-Core_Components.md`. One external state payload reduces to one stable runtime description. Field-level extraction would hide the complete mapping. |
| 10 | `extensions/weft_docker/weft_docker/plugin.py::_lookup_container` | 12 | S3 | `RUFF-SUP-206` Docker runtime observation and lookup | Same proof family. Preserve fallback order from runtime name to recorded ID to filtered listing and defensive API behavior. Independent lookup helpers would obscure precedence. |
| 11 | `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py::MacOSSandboxRunnerPlugin.validate_taskspec` | 11 | S3 | `RUFF-SUP-207` macOS sandbox validation | `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py`; [TS-1.3]. One boundary checklist owns command-only shape, profile, environment, platform, executable, and path errors. Reject extraction without reuse. |
| 12 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::parse_options` | 14 | S3 | `RUFF-SUP-208` Microsandbox option normalization | `extensions/weft_microsandbox/tests/test_options.py`; `extensions/weft_microsandbox/tests/test_plugin_validation.py`; [TS-1.3]. All runner options and cross-field constraints become one canonical value. Splitting weakens cross-field validation and error order. |
| 13 | `integrations/weft_django/weft_django/client.py::_apply_taskspec_payload_overrides` | 21 | S3 | `RUFF-SUP-209` Django public override mapping | `integrations/weft_django/tests/test_weft_django.py`; `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-6], [DJ-8], [DJ-16], [DJ-17]. Preserve copy isolation, section mapping, and decorated-task restrictions. Per-section helpers would hide cross-section precedence. |
| 14 | `tests/architecture/test_import_boundaries.py::_parse_import_edges` | 13 | S3 | `RUFF-SUP-210` architecture import and graph enforcement | Same module; `AGENTS.md` sections 4.1-4.2. Relative imports, function scope, and `TYPE_CHECKING` scope share nested visitor state. External helpers reduce AST-state locality. |
| 15 | `tests/architecture/test_import_boundaries.py::_strongly_connected_components` | 12 | S3 | `RUFF-SUP-227` import-cycle graph algorithm | Same module's cycle assertions. Tarjan index, stack, and low-link state remain local and deterministic. Reject extraction of algorithm state. |
| 16 | `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries` | 17 | S3 | `RUFF-SUP-228` internal layer-boundary test | Same test; `AGENTS.md` sections 4.1-4.2. One edge stream proves core/commands/CLI/client plus Rich/Typer boundaries. Splitting risks partial enforcement and repeated traversal. |
| 17 | `tests/architecture/test_import_boundaries.py::_simplebroker_surface_violations` | 20 | S3 | `RUFF-SUP-229` SimpleBroker public-surface audit | Same module; `docs/specifications/04-SimpleBroker_Integration.md`. Static and dynamic reaches share one alias table across AST passes. Separate scanners would interpret aliases inconsistently. |
| 18 | `tests/cli/test_cli_run.py::_run_parallel_manager_reuse_cycle` | 16 | S3 | `RUFF-SUP-211` parallel manager-reuse acceptance | Callers in the same module; `docs/specifications/03-Manager_Architecture.md`; `docs/specifications/08-Testing_Strategy.md` [TS-1]. Concurrent submissions, terminal waits, convergence, and diagnostic snapshots form one temporal scenario. Reject step fragmentation. |
| 19 | `tests/cli/test_manager_proctitle.py::test_manager_proctitle_updates_to_running` | 11 | S3 | `RUFF-SUP-212` manager process-title acceptance | Same test; `docs/specifications/07-System_Invariants.md` [OBS.4], [OBS.8]. Real launch, polling, assertion, and cleanup prove the live title contract. Keep the protocol together. |
| 20 | `tests/conftest.py::run_cli` | 12 | S3 | `RUFF-SUP-213` canonical CLI test harness | CLI suite; [TS-0]. Backend-aware interpreter, environment, root preparation, timeout diagnostics, and resource registration define one subprocess boundary. Splitting makes execution policy harder to audit. |
| 21 | `tests/conftest.py::_register_from_json` | 12 | S3 | `RUFF-SUP-230` CLI JSON resource registration | `tests/test_harness_registration.py`; `run_cli`; [TS-0]. Recursive task/manager TID and PID registration preserves role context. Separate dict/list walkers add indirection without an owner. |
| 22 | `tests/core/test_manager.py::test_manager_terminate_children_kills_sigterm_trapping_descendant_tree` | 11 | S3 | `RUFF-SUP-214` manager lifecycle race tests | Same test; `weft/core/manager.py`; `docs/specifications/03-Manager_Architecture.md`. A manager kills a descendant tree that traps `SIGTERM`, with bounded cleanup. Keep the OS-level scenario local. |
| 23 | `tests/core/test_manager.py::test_manager_active_heartbeat_race_preserves_superseded_record` | 11 | S3 | `RUFF-SUP-231` manager heartbeat/supersession race | Same test; manager registry contract. The fake queue encodes the decisive heartbeat/supersession interleaving. Extraction would hide the race schedule. |
| 24 | `tests/fixtures/mcp_stdio_fixture.py::main` | 18 | S3 | `RUFF-SUP-215` MCP and provider CLI fixtures | `tests/tasks/test_agent_execution.py`; `tests/fixtures/runtime_profiles_fixture.py`; [AR-7]. Stateful MCP framing, tool calls, errors, and shutdown form one wire-protocol emulator. Reject handler fragmentation. |
| 25 | `tests/fixtures/provider_cli_fixture.py::_run_claude` | 12 | S3 | `RUFF-SUP-232` provider CLI fixtures | Provider backend/session/validation tests; [AR-7]. One provider's supported argv/input/output grammar stays visible. Reject cross-provider abstraction. |
| 26 | `tests/fixtures/provider_cli_fixture.py::_run_codex` | 23 | S3 | `RUFF-SUP-232` provider CLI fixtures | Same proof family. Config overrides, structured input, tools, failures, and framing share parsed argv state. Option-handler extraction reduces comprehensibility. |
| 27 | `tests/fixtures/provider_cli_fixture.py::_run_gemini_or_qwen` | 20 | S3 | `RUFF-SUP-232` provider CLI fixtures | Same proof family. Preserve the deliberate common grammar and explicit provider differences. Further abstraction would duplicate state or invent a fixture framework. |
| 28 | `tests/fixtures/provider_cli_fixture.py::_run_opencode` | 16 | S3 | `RUFF-SUP-232` provider CLI fixtures | Same proof family. Provider-local argv, input, tools, failure, and output semantics remain directly comparable with the adapter. |
| 29 | `tests/helpers/test_backend.py::cli_context_root` | 15 | S3 | `RUFF-SUP-216` test CLI context resolution | CLI init/run/manager tests; `prepare_cli_root`; [TS-0]. Explicit context, spec context, init target, and cwd fallback have exact precedence. Command-specific scanners would obscure it. |
| 30 | `tests/helpers/weft_harness.py::WeftTestHarness.wait_for_completion` | 11 | S3 | `RUFF-SUP-217` harness wait and cleanup protocols | `tests/test_harness_registration.py`; [TS-0]. Timestamp reordering, terminal-event recognition, diagnostics, and closure share cursor state. Keep the polling frame cohesive. |
| 31 | `tests/helpers/weft_harness.py::WeftTestHarness._stop_active_managers` | 12 | S3 | `RUFF-SUP-233` harness manager/task shutdown | Same proof family. Graceful-to-force manager/task shutdown and PID settlement share tracking state and order. Reject extraction that makes escalation implicit. |
| 32 | `tests/helpers/weft_harness.py::WeftTestHarness._cleanup_preserving_database` | 17 | S3 | `RUFF-SUP-234` preserve-database cleanup | Same proof family, including Windows regressions. Linked deadlines prove quiescence and DB releasability without deletion. Splitting risks deadline resets or changed escalation. |
| 33 | `tests/helpers/weft_harness.py::WeftTestHarness._database_files_releasable` | 11 | S3 | `RUFF-SUP-235` database release probe | Same proof family. Database/WAL/SHM rename probes must restore every file or surface failure. Mutation and rollback remain one frame. |
| 34 | `tests/long_session_surface_benchmark.py::CliSurface.run_task` | 12 | S3 | `RUFF-SUP-218` long-session benchmark protocol | Same benchmark; [TS-0] noncanonical benchmark statement. API and CLI surfaces receive equivalent run arguments. Per-option helpers add noise without reuse. |
| 35 | `tests/long_session_surface_benchmark.py::_run_long_session` | 31 | S3 | `RUFF-SUP-218` long-session benchmark protocol | Same module. Persistent work, aliases, submissions, observations, and cleanup remain one timed sequence. Phase extraction would hide measurement boundaries. |
| 36 | `tests/tasks/process_target.py::run_task` | 22 | S3 | `RUFF-SUP-219` synthetic resource-load target | `tests/tasks/test_task_execution.py`; `tests/tasks/test_runner.py`; `docs/specifications/06-Resource_Management.md`. Memory, CPU, sockets, files, output, and duration intentionally compose in one deployable function target. |
| 37 | `tests/tasks/test_multiqueue_watcher.py::test_owner_fatal_exit_signals_every_queued_mutator` | 11 | S3 | `RUFF-SUP-220` watcher fatal-owner regression | Same test; `weft/core/tasks/multiqueue_watcher.py`. Events, waiter, queued threads, and joins encode one deterministic deadlock-release schedule. Helper extraction would obscure causality. |
| 38 | `tests/tasks/test_runner.py::test_agent_session_close_releases_multiprocessing_handles` | 11 | S3 | `RUFF-SUP-221` AgentSession lifecycle regressions | Same test; `weft/core/tasks/runner.py`; [AR-6]. Closing releases process and queue handles without a late stop request. Local fakes preserve call visibility. |
| 39 | `tests/tasks/test_runner.py::test_agent_session_does_not_poll_limits_after_producer_exit` | 14 | S3 | `RUFF-SUP-236` AgentSession producer-exit ordering | Same test; terminal-handoff tests; [AR-6]. Dead-producer/channel evidence wins before a stale limit. The fakes encode one same-turn observation order. |
| 40 | `tests/tasks/test_runner.py::test_session_stop_effect_cannot_reset_absolute_drain_deadline` | 11 | S3 | `RUFF-SUP-237` AgentSession absolute drain deadline | Same test; terminal-handoff reducer coverage; [AR-6]. Persistent cleanup cannot extend the accepted absolute drain deadline. Keep fake clock and receiver local. |
| 41 | `tests/tasks/test_task_monitor.py::test_task_monitor_worker_local_snapshot_owns_mutable_runtime_resources` | 11 | S3 | `RUFF-SUP-222` TaskMonitor ownership and retirement regressions | Same test; `docs/specifications/07-System_Invariants.md` [IMPL.11]. Workers receive isolated snapshots/facades and cannot mutate reactor state or leak resources. Exhaustive identity checks stay together. |
| 42 | `tests/tasks/test_task_monitor.py::test_retirement_backlog_identifies_binding_stage` | 18 | S3 | `RUFF-SUP-238` TaskMonitor retirement backlog diagnostic | Same test; `docs/specifications/05-Message_Flow_and_State.md` [MF-5]. Production-shaped terminal backlog retires or names the binding predicate with samples. Workload and diagnostics must remain local. |
| 43 | `bin/check-dom15-fixtures::check` | 11 | S3 | `RUFF-SUP-223` DOM-15 fixture audit | `docs/agent-context/decision-hierarchy.md` [DOM-15]; plan-metadata tests. Every class and `+P` has a valid fixture and cumulative wording. One finite governance checklist needs no dispatch layer. |
| 44 | `bin/coalesce-check::main` | 14 | S3 | `RUFF-SUP-224` coalescing evidence audit | `docs/coalescing.md`; real Git probes. SHAs and retrieval cues resolve or name valid foreign evidence with honest diagnostics. Parsing, reachability, attribution, and reporting share state. |
| 45 | `bin/pytest-pg::main` | 11 | S3 | `RUFF-SUP-225` Postgres test-runner lifecycle | `tests/system/test_pytest_pg_script.py`; [TS-0]. Mode validation, dependencies, container setup, pytest, cleanup, and honest exits form one operator flow. Further splitting scatters it. |
| 46 | `bin/release.py::plan_tag_action` | 11 | S3 | `RUFF-SUP-226` release tag and orchestration policy | `tests/system/test_release_script.py`; release workflow guards. Version, local/remote tag state, HEAD, and retag permission resolve to one safe decision table. Branch extraction hides precedence. |
| 47 | `bin/release.py::main` | 18 | S2 | `RUFF-SUP-226` release tag and orchestration policy | Same proof family; `.github/workflows/release*.yml`. Dirty-tree refusal, release planning, prechecks, commit/tag/publish order, and dry-run behavior stay fail-safe. Plausible seams require a separate hardened plan; reject activation-time changes. |

This slice reconciles exactly **47 findings; S1 = 0, S2 = 2, S3 = 45**.
The six extensionless Python-shebang tools were audited explicitly:
`bin/check-dom15-fixtures`, `bin/coalesce-check`, and `bin/pytest-pg` own the
three findings above; `bin/check-doc-paths`, `bin/pytest-live-providers`, and
`bin/pytest-worker-count` have none. `bin/mypy-check` and `bin/uv` are Bash and
remain excluded from Python discovery. Groups `RUFF-SUP-201` through
`RUFF-SUP-238` approve 47 directives with cardinalities
`2,1,2,1,2,2,1,1,1,1,1,1,1,1,1,1,1,2,1,1,1,1,1,1,1,2,1,1,1,1,1,4,1,1,1,1,1,1`
respectively.

### Proposed human registry rows: core

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|---|---|---|---|---|---|---|
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

### Proposed human registry rows: application and commands

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|---|---|---|---|---|---|---|
| `RUFF-SUP-101` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: explicit in-process overrides preserve the environment parsers, accepted input types, removed-setting errors, defaults, and loader-key parity. | `tests/system/test_constants.py::test_env_loader_and_explicit_override_normalizer_keys_stay_in_parity`; `tests/system/test_constants.py::TestLoadConfig::test_manager_serve_log_overrides_normalize`; `tests/system/test_constants.py::TestLoadConfig::test_removed_task_monitor_env_rejects` | Generic smart coercion or moving configuration ownership out of `_constants.py`; rejected in favor of a same-module declarative per-key seam subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-102` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: task status preserves snapshot authority while `--process` adds only scoped and live managed PIDs and JSON/plain output retains exact exits and fields. | `tests/cli/test_status.py::test_task_status_process_json_reports_dead_pid_stale_liveness`; `tests/cli/test_status.py::test_task_status_not_found` | Move status reconstruction into the CLI adapter or duplicate runner-liveness policy; rejected in favor of adjacent process-augmentation and rendering helpers subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
| `RUFF-SUP-103` | `C901` | `1` directives; raw: `C901=1` | Temporary through Task 5: the validation summary reflects validated task type, runner, command/function target, agent runtime/model, and adapter refs without mutating the payload. | `tests/cli/test_cli_validate.py::test_validate_taskspec_agent_summary`; `tests/cli/test_commands.py::TestValidateTaskspecCommand::test_validate_with_summary` | Generic table-rendering framework or recomputed validation semantics; rejected in favor of an adjacent target-type row builder subject to the per-refactor locality review. | Task 2 clean outside review PASS; owner approved 2026-08-05. |
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

### Proposed human registry rows: tests, integrations, extensions, and tools

| Group | Rules | Approved cardinality | Protected invariant | Real proof | Rejected alternatives | Approval |
|---|---|---|---|---|---|---|
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

## 6. Context And Key Files

### Files to create

- `bin/ruff_suppression_index.py` — copied from the current SimpleBroker tool,
  then adapted for [TS-3.1], Weft paths, and Weft commands.
- `tests/specs/test_ruff_policy.py` — firing tests for configuration,
  discovery, complexity 10/11, effective rule inventory, CI ordering, and live
  registry reconciliation.
- `tests/specs/test_ruff_suppression_index.py` — copied/adapted behavioral and
  hostile-input tests for check/write semantics.
- `tests/fixtures/ruff-enabled-rules.txt` — reviewed effective-rule inventory.

### Files to modify

- `pyproject.toml`
- `.github/workflows/test.yml`
- `docs/specifications/08-Testing_Strategy.md`
- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`
- `docs/plans/README.md`
- every retained C901 source location found through Ruff discovery
- focused behavior tests named by each approved suppression group
- `docs/agent-context/context.index.yaml` only if a new always-read guidance
  document is introduced (the preferred implementation introduces none)

### Read before editing

- all files in §3 under SimpleBroker precedent
- the entire target function and closest behavioral test for every C901
  disposition; do not classify from score, function name, or line count alone
- `docs/specifications/07-System_Invariants.md` before changing any runtime
  function
- the active plan/spec governing a recently changed owner, especially terminal
  handoff, task snapshots, manager lifecycle, monitoring, runners, and sessions
- CI lint/format/type-check steps and the repository-managed toolchain commands

### Reuse and adaptation boundary

Copy SimpleBroker's parser, reconciliation model, dataclasses, AST symbol
resolution, Markdown fence handling, newline preservation, atomic replacement,
exit-code split, and test fixtures. Rename only the normative section pointer,
default spec path, commands, and repository-specific expectations. Extend the
copied reconciliation with a Weft-required `C901` completeness check: project
raw `C901` diagnostics by `(path, noqa_row, code)` and require each one to be
owned by exactly one parsed tagged directive at that row. Run this check even
when the global raw aggregate matches, so an updated aggregate cannot conceal
an unregistered complexity suppression.

Do not:

- invent a generic Markdown generator framework
- add a dependency
- teach the generator to approve a suppression or write human-owned cells
- key the generated index on line numbers
- make the generator auto-run in write mode during pytest or CI
- use a baseline JSON allowlist as the durable registry

### Comprehension gates

Before editing, the implementer must be able to answer:

1. Why does normal Ruff need to pass while raw `--ignore-noqa` intentionally
   exits 1 and still participates in a passing policy check?
2. Which registry fields are human-owned, which are generated, and why may the
   tool never change approved cardinality?
3. Why does `path::qualified_symbol` detect suppression migration better than
   `path:line` or `(path, rule, count)`?
4. Which Weft functions own live task, manager, monitor, runner, or session
   state, and which existing real tests prove their failure and cleanup order?

## 7. Invariants And Constraints

### Runtime invariants

- The plan changes verification policy, not product behavior.
- TID format and immutability remain unchanged.
- Task state transitions remain forward-only.
- Reserved queue policy remains unchanged.
- Resolved TaskSpec `spec` and `io` sections remain immutable.
- Spawn-based process behavior and broker-connection ownership remain intact.
- Runtime-only `weft.state.*` queues remain runtime-only.
- The canonical
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine
  remains the only execution path.

### Complexity-policy invariants

- Complexity 10 passes and 11 fails under the committed normal configuration.
- Every retained finding stays visible to raw Ruff.
- Every raw `C901` diagnostic is owned by exactly one tagged, approved
  directive at the same Ruff `noqa_row`; untagged `C901` is always a policy
  failure, even if the global aggregate was updated.
- One directive belongs to one stable group; grouped sites must share one
  invariant, proof family, rejected decomposition, and approval.
- Registry cardinality is an approval boundary, not generated bookkeeping.
- A source move within the same qualified symbol does not churn the index; a
  suppression moving between symbols produces a visible index diff.
- Existing global ignores `E501` and `B008` are not expanded in this work.
- Existing enabled-rule raw suppressions begin at `E402=22`, `F401=5`; any
  migration change is reviewed explicitly rather than silently normalized.
- Ruff remains the sole Python-discovery owner. The tool must not maintain an
  independent hand-written source tree inventory.

### Refactor constraints

- File size and score alone are never refactor reasons.
- Extract only a real owner-local phase, pure decision seam, repeated
  operation, or independently testable state transition.
- Do not create pass-through helpers, generic state-machine engines, generic
  command frameworks, or new modules solely to lower C901.
- A runtime owner whose decomposition would pass live mutable state or cleanup
  precedence across helpers remains cohesive and is registered.
- Recently established reducers, lifecycle frames, and canonical evidence
  owners are presumed intentional until their governing plan, spec, and tests
  prove a separable seam.
- Any candidate refactor that changes runtime behavior, a public annotation,
  diagnostic, timing guarantee, cleanup order, or persistence effect moves to
  a separate class-appropriate plan. The C901 finding remains registered in
  this migration.

### Review and landing constraints

- No source suppression lands before its human registry row is reviewed.
- No registry row lands without a real proof path and rejected alternatives.
- Normal Ruff configuration, all initial local C901 directives, approved rows,
  generated index, tool, and policy tests form one atomic activation slice.
- Use explicit file-list staging if this plan is later committed; never absorb
  unrelated worktree changes.
- No implementation completion claim is valid until the plan metadata gate,
  traceability gate, full Ruff policy, normal Ruff, formatter, mypy, and full
  pytest suite have been rerun from current state.

## 8. Anti-Mocking And Proof Rules

The generator itself is pure repository tooling and should use isolated real
filesystem fixtures plus the real locked Ruff binary. Monkeypatch only atomic
replacement failure or another OS failure that cannot be induced portably.

For complexity dispositions:

- pure parsers and validators may use focused unit tests
- queue semantics use real SimpleBroker queues
- manager/task lifecycle uses `WeftTestHarness` or the existing real lifecycle
  suite
- subprocess, session, signal, and process-tree behavior uses real spawned
  processes and bounded waits
- monitor/store cleanup uses the real store/queue boundary already owned by
  its closest tests
- mocks must not replace the live owner whose cohesion is being used to
  justify a suppression

A unit mock can supplement proof but cannot be the sole `Real proof` entry for
a runtime/concurrency group.

## 9. Error Priorities

- An unclean normal Ruff run, unapproved suppression growth, an untagged raw
  `C901` diagnostic, registry/source mismatch, raw diagnostic mismatch,
  malformed human row, stale generated index, or partial write is a policy
  failure and blocks activation or CI.
- Unreadable or invalid Python source makes a complete repository index
  unverifiable; the tool exits 2 and writes nothing. Unlike a batch product
  scanner, partial suppression indexes are forbidden because they would be
  presented as complete policy evidence.
- Expected policy and tool failures emit one-line diagnostics without
  tracebacks. Unexpected programming errors retain tracebacks.
- A generator failure must never modify source files or human-owned spec text.
- A refactor regression is resolved by reverting that refactor while retaining
  the gate and its registered exception; passing Ruff does not outrank runtime
  correctness.

## 10. Rollout, Rollback, And One-Way Doors

There is no runtime rollout, data migration, persisted-format change, or
product one-way door.

Implementation order matters:

1. capture and review the exact inventory and human rationales
2. land the red generator/policy fixtures
3. atomically promote [TS-3]/[TS-3.1] with the tool, configuration, source
   directives, registry, index, and policy tests
4. perform only approved owner-coherent cleanup under the active gate
5. reconcile the final registry and run repository-wide verification

Rollback the atomic activation slice as a unit if the checker or CI discovery
is unsound. After activation is stable, each cleanup refactor is independently
revertible: restore its registered C901 directive and approved cardinality in
the same revert. Do not roll back by raising the threshold or weakening the
registry.

The only process one-way door is accepting human registry reasons as durable
policy. Mitigation: stable IDs, explicit approval, exact cardinality, source
review, and a final independent reconciliation review.

## 11. Dependency-Ordered Tasks

### Task 1 — Capture the exact baseline and disposition ledger

- Outcome: this plan contains one exact row for every baseline C901 finding,
  with path, qualified symbol, score, category, proposed group, governing
  contract/test, and planned disposition.
- Files to touch:
  - this plan only
- Actions:
  1. re-run the three baseline commands in §4
  2. add the six extensionless Python tools to the audit input
  3. record every finding exactly once
  4. classify it as:
     - `S1`: simplify now at a proven owner-local seam
     - `S2`: retain now; separately plan a risky or behavior-changing seam
     - `S3`: retain as cohesive and register
  5. draft stable suppression groups; group sites only when invariant, proof,
     rejected alternative, and approval are genuinely shared
  6. append the exact proposed [TS-3.1] human rows to §5
- Required proof:
  - every finding count and unique `noqa_row` location reconciles
  - no finding is classified from score alone
- Stop if:
  - the inventory differs from 152 without an explained repository change
  - a candidate requires public/runtime behavior change
  - a group reason cannot name a protected invariant and real proof
- Done when:
  - the ledger count equals the raw finding count
  - every planned retained directive belongs to exactly one draft group
  - the plan revision has a baseline identifier in the Revision Log

### Task 2 — Independently review the exact policy and registry migration

- Outcome: a reviewer and the owner approve the policy delta, every group
  rationale, cardinality, proof, and disposition before source suppressions
  exist.
- Read first:
  - this plan, including the Task 1 ledger and exact proposed rows
  - [TS-0], [TS-1], proposed [TS-3], [TS-3.1]
  - the named functions and proof tests
  - SimpleBroker precedent in §3
- Reviewer stance:
  - challenge score-driven fragmentation, vague shared groups, mock-only
    proofs, stale counts, hidden runtime changes, and performative ceremony
- Required action:
  - disposition every finding; revise accepted issues; record declined issues
    with evidence; obtain explicit owner approval of the final human rows
- Done when:
  - verdict is PASS on implementability and non-degradation
  - the reviewed plan baseline is recorded

### Task 3 — Add red policy and generator behavior tests

- Outcome: tests prove the missing gate and tool behavior before production
  implementation.
- Files to create:
  - `tests/specs/test_ruff_policy.py`
  - `tests/specs/test_ruff_suppression_index.py`
  - `tests/fixtures/ruff-enabled-rules.txt`
- Copy/adapt from SimpleBroker rather than retyping the parser contract.
- Red proofs:
  - complexity 10 passes while 11 fails (fails before C901 configuration)
  - an incomplete extensionless Python discovery fixture fails
  - stale generated index fails check without writing
  - write repairs only the generated block and becomes idempotent
  - unknown group, group growth, mismatched raw diagnostic, malformed marker,
    grammar mimicry, invalid source, CRLF, unsafe Markdown path, replacement
    failure, and repository paths with spaces exercise the documented exits
  - an untagged `# noqa: C901` still fails after its author also updates the
    global raw-noqa aggregate, proving that aggregate reconciliation cannot
    bypass registration
  - both `--check` and `--write` reject that bypass and leave the spec
    byte-for-byte unchanged
- Keep fixture repositories minimal and invoke the real Ruff binary.
- Done when:
  - the intended missing-policy tests fail for the expected reason
  - ported behavior tests pass only where they exercise already-implemented
    isolated helpers; no test is weakened to make the pre-implementation run
    green

### Task 4 — Atomic spec, tool, configuration, source, and CI activation

- Outcome: normal repository Ruff is clean with C901 active at 10, and every
  approved finding remains visible and exactly registered.
- Files to create/modify: all files in §6 plus the approved source locations.
- Actions:
  1. copy and adapt `bin/ruff_suppression_index.py`
  2. set `C901` in `tool.ruff.lint.select` and add
     `[tool.ruff.lint.mccabe] max-complexity = 10`
  3. add exact `extend-include` entries for the six tracked extensionless
     Python tools; do not use `bin/*`, because `bin/mypy-check` and `bin/uv`
     are Bash
  4. add approved local C901 pointers to all retained findings
  5. promote [TS-3], [TS-3.1], human rows, global raw inventory, markers, and
     generated symbol index
  6. record the promotion baseline identifier
  7. change CI lint to `ruff check .`; run the suppression check immediately
     after normal Ruff; retain explicit formatter roots; include the new tool
     in mypy
  8. update the effective-rule fixture and discovery expectation from real
     `ruff --show-settings` / `--show-files` output
  9. insert the exact [TS-3] implementation mapping above and add reciprocal
     [TS-3]/[TS-3.1] citations to the new tool and applicable test module
     docstrings in this same activation slice
- Stop if:
  - normal Ruff needs a per-file/global ignore or threshold raise
  - Ruff discovery attempts to parse a Bash tool
  - the generator would need to infer or alter approval data
  - the atomic slice cannot be kept internally consistent
- Done when:
  - normal Ruff passes
  - raw Ruff reports exactly the reviewed retained findings
  - directives, groups, cardinalities, global inventory, and generated index
    reconcile
  - the raw-to-tagged C901 completeness projection is exact and rejects every
    untagged complexity suppression
  - every targeted policy mutation has a firing test

### Task 5 — Perform only approved owner-coherent simplifications

- Outcome: S1 findings are removed at real seams without changing runtime or
  public behavior; S2/S3 findings remain registered.
- Sequence work by owner so each slice is independently reviewable:
  1. pure configuration, parser, validation, and formatting helpers
  2. repository tooling and test infrastructure
  3. command result/wait/render owners
  4. manager, monitor, runner, session, and extension owners only where Task 1
     found a behavior-preserving same-owner seam with existing real proof
- Before the first function:
  - obtain owner direction to create a checkpoint commit containing the clean,
    atomic Task 4 activation; record that SHA as the first accepted Task 5
    baseline
  - if checkpoint-commit authority is withheld, pause Task 5; do not create a
    branch from an older SHA or attempt to reconstruct an uncommitted accepted
    state in a candidate worktree
- For each function:
  - create a dedicated sibling Git worktree on a uniquely named
    `codex/ruff-rework-rw-nnn` branch from the latest owner-directed accepted
    Task 5 checkpoint SHA; make all candidate source, directive, registry, and
    generated-index edits there, never first in the accepted worktree
  - record the candidate worktree, branch, and baseline SHA in the Rework Queue
    row before editing; creating this isolation branch does not authorize a
    commit, merge, or deletion without the owner's normal direction
  - record before/after score
  - write or strengthen a characterization test first
  - run the closest real behavior suite
  - after the refactor and its focused tests pass, dispatch a new clean-context
    subagent that did not author or advise on the refactor, acting as a Python
    expert, to compare the pre-refactor and post-refactor code
  - give that reviewer only the governing spec/invariant, the relevant tests,
    the exact before/after diff, and enough adjacent code to understand the
    owner; do not give it the implementer's rationale or prime it with the
    lower complexity score
  - require a binary `NET POSITIVE` or `NET NEGATIVE` verdict focused on
    logical locality, comprehensibility, indirection cost, state and failure
    ordering, naming, and whether a reader must now jump across more scopes to
    reconstruct one behavior
  - record the verdict and evidence in §18/§20 before starting the next
    refactor; do not batch several independently reviewable refactors into one
    locality review
  - when the verdict is `NET NEGATIVE` or uncertain, preserve the candidate in
    its dedicated worktree, mark its existing queue row `queued`, and leave the
    accepted worktree byte-for-byte at its prior source, registry, and index
    baseline; the queue entry, not an automatic revert, owns the follow-up
  - only after `NET POSITIVE`, apply the reviewed candidate atomically to the
    accepted worktree, remove the source directive and adjust its human
    cardinality, regenerate rather than hand-edit the derived index, and rerun
    reconciliation plus the focused behavior proof
  - then request owner direction for a checkpoint commit; mark the row
    `awaiting_checkpoint` until that commit exists, record its SHA, and do not
    start the next candidate until it becomes the new accepted baseline
- Stop and leave the finding registered if:
  - helper parameters recreate the entire owner state
  - extraction moves failure/cleanup precedence away from the state owner
  - the refactor wants a new module or abstraction solely for score
  - product behavior, timing, diagnostics, or persistence would change
  - the clean-context Python review returns `NET NEGATIVE`, cannot reach a
    confident `NET POSITIVE` verdict, or finds that lower C901 came at the cost
    of logical locality or comprehensibility; enqueue the candidate for rework
    rather than accepting it or negotiating the review down
- Done when:
  - every S1 row is simplified or explicitly reclassified through a reviewed
    plan revision
  - no S2/S3 owner was fragmented to reduce the registry count
  - every retained refactor has its own recorded clean-context `NET POSITIVE`
    Python-expert review
  - the Task 5 Rework Queue has no `candidate`, `queued`, `reworking`, or
    `awaiting_checkpoint` entry

#### Task 5 Rework Queue

This queue preserves reviewed refactoring work that is not yet good enough to
accept. It is a plan ledger, not a Weft runtime queue. A negative review does
not discard or automatically revert the candidate. The dedicated candidate
worktree preserves its applied diff and review evidence while the accepted
worktree remains on the last accepted source, registry, and generated index.
Do not include queued work in an accepted slice or completion claim. This is a
blocking FIFO for Task 5: rework the head candidate before starting another
complexity refactor, so an unaccepted diff cannot be obscured by later cleanup.

Each entry receives a stable `RW-NNN` ID and records the owning symbol,
candidate worktree/branch and baseline SHA, reviewer evidence, the locality or
comprehensibility problem, the required improvement, attempt count, and status
(`candidate`, `queued`, `reworking`, `awaiting_checkpoint`, or `accepted`).
Rework the candidate in that isolated worktree toward the best combined
outcome, not merely a passing score. Every rework attempt reruns the focused
correctness proof and gets a newly created clean-context Python-expert review.
After a `NET POSITIVE` verdict, atomic apply, and accepted-worktree
verification, set `awaiting_checkpoint`; mark it `accepted` only when the owner
directs the checkpoint commit and its SHA is recorded as the next baseline.
Removing the worktree/branch or abandoning a queued candidate requires
explicit owner direction and a recorded disposition; it is never the automatic
response to a negative review.

| ID | Owning symbol | Candidate worktree/branch | Baseline SHA | Accepted checkpoint SHA | Review evidence | Locality/comprehensibility problem | Required improvement | Attempt | Status |
|---|---|---|---|---|---|---|---|---|---|
| RW-001 | `weft/_constants.py::_normalize_weft_override_value` | `/Users/van/Developer/weft-ruff-rw-001`; `codex/ruff-rework-rw-001` | `a7e7d6ed05b99ff233e56ba24afff1f83ce60158` | pending | Attempts 1 and 2: `NET NEGATIVE`. Attempt 3 fresh clean-context review: `NET POSITIVE`; the unified registry makes each key's category, parser, and removal policy auditable in one scan, while named adjacent helpers preserve the exact coercion and error contract. Every attempt's 560-case sweep matched the baseline. | Resolved in attempt 3: named categories, category-derived validation, and one unified live/removed registry remove the cross-field reconstruction and overlap ambiguity identified by the first two reviews. | Satisfied. Each immutable row names its category and parser or removal reason; import-time validation rejects inconsistent rule shapes, and loader/registry parity has a direct structural test. | 3 | awaiting_checkpoint |

### Task 6 — Final reconciliation, full verification, and closure

- Outcome: one current, exact registry and one reproducible completion record
  remain; no plan-only policy survives.
- Actions:
  1. run normal and raw Ruff from current state
  2. remove obsolete directives and reconcile human cardinalities
  3. regenerate the symbol index
  4. verify the atomically promoted [TS-3] mapping and reciprocal tool/test
     citations still name current file and symbol ownership; correct drift in
     the same closure slice
  5. reconcile spec, plan, tool, CI, source comments, tests, and plan index
  6. run an independent completed-work review over the full diff and exact raw
     inventory
  7. record any durable correction in `docs/lessons.md`
  8. change plan metadata/index status to `completed` only after current gates
     pass and the implementation is committed at the owner's direction
- Done when:
  - every retained raw C901 finding has one approved live directive and group
  - every removed finding has no stale directive or registry cardinality
  - current-state evidence supports every completion claim

## 12. Testing Plan

### Policy configuration and discovery

`tests/specs/test_ruff_policy.py` must prove:

- the exact selected families, global ignores, McCabe threshold, no preview,
  and no per-file ignores
- the effective enabled-rule set matches the committed fixture
- every tracked `.py`/`.pyi` and Python-shebang tool is Ruff-discovered
- Bash tools are not Ruff-discovered as Python
- complexity 10 passes and 11 produces exactly one C901 diagnostic
- normal Ruff is clean
- the live registry reconciles
- CI runs normal Ruff before suppression check, keeps explicit formatter roots,
  and mypy includes the repository tool

### Generator and CLI behavior

Port the SimpleBroker tests for:

- check versus write and idempotence
- byte preservation outside unique generated markers
- stable POSIX repository paths and repository roots containing spaces
- human table grammar, unique groups, exact rule sets, cardinalities, and
  global inventory
- Python-comment token scanning so strings and prose comments are inert
- fenced Markdown mimicry with both backtick and tilde fences
- AST symbol attribution, class qualification, decorator attribution, nested
  function ownership, and `<module>`
- raw diagnostic multiplicity at one directive
- C901 completeness by `noqa_row`, including a mutation that adds an untagged
  `# noqa: C901` and updates the global aggregate but still must fail
- unknown, missing, empty, duplicate, malformed, and stale groups/directives
- unreadable/invalid source, missing spec, malformed Ruff JSON, unexpected Ruff
  exit, unsafe Markdown path, unwritable replacement, and no partial write
- CRLF and non-ASCII preservation

### Complexity disposition proof

Each retained group names the closest real behavioral proof. Each S1 refactor
adds or strengthens a test that would fail if the protected ordering,
diagnostic, state, or cleanup effect changed. Do not add one giant synthetic
complexity test suite; use the existing owner-local tests.

### Failing-test-first record

Task 3 records the red run in this plan's verification table. If a ported test
cannot be red because it covers an unchanged copied helper behavior, record the
SimpleBroker production/test source and run it green after the copy; the
complexity-boundary and discovery tests must still demonstrate the pre-change
failure directly.

## 13. Verification And Gates

Per-task commands use the in-repo environment:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_ruff_policy.py -q
./.venv/bin/python -m pytest tests/specs/test_ruff_suppression_index.py -q
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/ruff check .
./.venv/bin/ruff check --select C901 --ignore-noqa \
  --output-format concise .
```

The raw C901 command intentionally exits 1 while approved findings remain.
The passing comparison is the policy test and suppression-index check.

Final gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
git diff --check
```

`bin/check-doc-paths` is advisory at this baseline and reports the eight known
dangling claims recorded in
`docs/plans/2026-07-28-agent-guidance-propagation-plan.md`. This plan must add
no new dangling claim. Success is either the same reviewed eight-claim set or
zero if that separate debt is resolved before implementation closes; do not
silently waive a changed set.

Backstitch has no Weft-local configuration, so the explicit roots above are
required. They include every C901-bearing core, test, integration, extension,
and tool surface. The authoring baseline is recorded after the initial review
in §18 and includes known unrelated ambiguity and mapping debt. Completion
requires a saved after-report with no error or warning against [TS-3],
[TS-3.1], `bin/ruff_suppression_index.py`, either new policy-test module, or
this plan, plus no new keyed error/warning outside those touched surfaces.
Compare issues by code, path, symbol, and section rather than aggregate counts,
which can change when a mapping resolves existing debt. If the sibling
Backstitch checkout is absent, record that as a traceability-tooling blocker;
run the local metadata/spec-hygiene tests and reciprocal `rg` audit, but do not
claim the Backstitch gate passed.

Run `bin/pytest-pg --all` if any disposition changes shared queue, manager,
watcher, or runtime behavior. Run the closest extension suite for every
extension function changed.

Successful rollout observation is CI lint producing a clean normal Ruff run
followed by a clean suppression-index check. There is no runtime observation
because the intended product behavior is unchanged.

## 14. Independent Review Loop

Plan review is mandatory now and again after Task 1 adds the exact disposition
ledger and registry rows. Prefer a reviewer from a different agent family; if
only same-family review is available, record that limitation.

Review prompt:

> Read this plan, its exact proposed [TS-3]/[TS-3.1] delta, the current Ruff
> configuration, the 152-finding raw audit, and the cited SimpleBroker end
> state. Look for errors, harmful score-driven decomposition, ambiguous
> ownership, weak suppression reasons, mock-only proof, discovery gaps,
> generator behavior that could approve policy, and ceremony that does not
> protect a real risk. Do not implement. Answer PASS or BLOCKED: could a
> zero-context engineer implement this confidently and correctly, and would
> doing so preserve Weft's runtime robustness?

Meaningful-slice review is required after:

1. Task 1 inventory and exact proposed human rows
2. Task 4 atomic activation
3. every individual Task 5 refactor, using a fresh clean-context Python-expert
   subagent and the binary locality/comprehensibility verdict defined below
4. each runtime-owner simplification wave in Task 5
5. final reconciliation

The per-refactor Python review is a separate gate from correctness review and
cannot be replaced by passing tests, a lower C901 score, or the wave-level
review. Its prompt is:

> Act as a Python expert with no prior involvement in this refactor. Compare
> the supplied pre-refactor and post-refactor code using the governing
> invariant, adjacent owner code, and real tests. Ignore the numeric complexity
> improvement when judging design. Decide whether the change is `NET POSITIVE`
> or `NET NEGATIVE`, with primary weight on logical locality and
> comprehensibility. Check whether state, ordering, cleanup, and error behavior
> can still be understood in one place; whether extraction added navigation or
> parameter plumbing; whether names expose the real phases; and whether the
> new seams are independently meaningful. Cite concrete code. Return
> `NET POSITIVE` only when the readability/locality gain exceeds the added
> indirection. Uncertainty is `NET NEGATIVE` for landing purposes.

Use a newly created subagent for each review. It must not inherit an earlier
reviewer's conversation or participate in implementation. Give it the bounded
review packet above, wait for its result, and record the exact verdict and
disposition before proceeding. A `NET NEGATIVE` or uncertain refactor is
preserved as a non-accepted candidate in its dedicated worktree and entered in
the Task 5 Rework Queue; the accepted worktree retains the original live
directive, approved cardinality, and generated index. Each later attempt
receives a different clean-context reviewer. The candidate cannot enter an
accepted slice until one of those reviews returns `NET POSITIVE` and the exact
source/registry/index delta passes atomically in the accepted worktree.

Every finding receives an explicit disposition in the Review Log. Accepted
findings revise the plan or implementation. Declined findings record evidence.

## 15. Stop And Re-Plan Gates

Stop rather than improvise when:

- the complete discovery boundary cannot be stated without parsing non-Python
  tools
- a suppression group cannot name a tested invariant and rejected alternative
- a proposed simplification crosses a public or durable runtime boundary
- a refactor creates a second execution path or state owner
- a real proof would require mocking away the owner under review
- a clean-context Python expert judges a refactor net negative for logical
  locality or comprehensibility, or cannot confidently judge it net positive;
  stop acceptance of that candidate and enqueue it for rework
- the latest accepted Task 5 state is not represented by an owner-directed
  checkpoint commit; pause instead of branching from stale HEAD or inferring
  commit authority
- normal Ruff can pass only through a broad ignore, allowlist, or threshold
  increase
- the generator needs authority over human approval data
- any raw C901 diagnostic cannot be mapped to exactly one tagged, approved
  directive at its Ruff `noqa_row`
- the atomic activation cannot be rolled back as one coherent slice
- the spec, tool, source, and CI inventories cannot be reconciled exactly

## 16. Out Of Scope

- A repository-wide state-machine discovery or transition-table initiative.
  Weft already has targeted state-machine and reducer contracts; expanding
  their policy requires a separate user decision and plan.
- Enabling Ruff preview rules, `select = ["ALL"]`, or unrelated rule families.
- Replacing Weft's current `select` policy with SimpleBroker's broader lint
  expansion strategy.
- Raising or otherwise redesigning project coverage gates.
- Splitting `manager.py`, `task_monitor.py`, or another cohesive file because
  it is large.
- Public API, CLI, TaskSpec, queue, state, persistence, runner, or lifecycle
  changes.
- New dependencies or a generic lint/suppression framework shared across
  repositories.
- Auto-approving, auto-growing, or silently regenerating human policy.
- Publishing a release.

## 17. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## 18. Verification Evidence Record

| Slice | Changed files | Command | Observed result | Review | Residual risk |
|---|---|---|---|---|---|
| Plan authoring | This plan, `docs/plans/README.md`, testing-spec backlink | `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q` | 8 passed | Self-review | Task 1 ledger requires the separate evidence below. |
| Plan authoring | Agent-context fixture claims | `bin/check-dom15-fixtures` | Passed | Self-review | None for the touched fixture contract. |
| Plan authoring | Repository documentation claims | `bin/check-doc-paths` | Reported the same eight pre-existing dangling claims cited in §13 | Self-review | Advisory baseline debt remains; implementation must add no new claim. |
| Plan authoring | Proposed traceability boundary | `../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json` | Expected exit 1; baseline: 355 spec sections, 886 code refs, 728 mappings, 45 errors, 1025 warnings, 610 infos. Report saved outside the repository at `/tmp/weft-ruff-plan-backstitch-before.json`. | Independent review required all C901-bearing roots | Known unrelated traceability debt remains; completion uses the keyed touched-surface gate in §13, not aggregate-count equality. |
| Plan authoring | Plan diff | `git diff --check` | Passed | Self-review | Implementation not started. |
| Task 1 inventory | §5A exact ledger | Repo-managed Ruff JSON for `.` plus the six extensionless Python tools, compared with AST outermost-symbol attribution | 152 actual identities = 152 ledger identities; zero missing, extra, or duplicate rows; after Task 2 round 1: S1=5, S2=42, S3=105 | Three ownership-partition audits plus clean outside review | Owner approved the exact dispositions and groups on 2026-08-05. |
| Task 1 registry proposal | §5A proposed human rows | Ledger/group reconciliation script | After Task 2 round 1: 123 unique groups; directive total=152; raw C901 total=152; every group cardinality exact; all five S1 groups name Task 5 | Outside review required group splits, two S1 reclassifications, and exact firing proofs; owner then approved all rows | Promoted atomically to `docs/specifications/08-Testing_Strategy.md` [TS-3.1]. |
| Task 3 policy tracer | `tests/specs/test_ruff_policy.py` | `./.venv/bin/python -m pytest -n 0 tests/specs/test_ruff_policy.py::test_ruff_complexity_policy_is_configured -q` | Expected RED: C901 was absent from `lint.select` before Task 4 activation | Independent test-slice agent | Closed by the atomic configuration activation. |
| Task 3 registry tracer | `tests/specs/test_ruff_suppression_index.py` | `./.venv/bin/python -m pytest -n 0 tests/specs/test_ruff_suppression_index.py::test_untagged_c901_fails_even_when_global_inventory_matches -q` | Expected RED: 2 parametrized cases failed because the repository tool did not yet exist and exited 2 instead of the required policy exit 1 | Independent registry-tool agent | Closed by the adapted tool and exact raw-C901 ownership projection. |
| Task 4 pre-generation gate | Live [TS-3.1] registry | `./.venv/bin/python bin/ruff_suppression_index.py --check` | Expected exit 1: generated Ruff suppression index was stale; no mutation | Parent integration | Closed by one explicit `--write`, followed by a clean `--check`. |
| Task 4 focused green | Ruff config, registry tool, spec, source pointers, CI, and focused tests | `./.venv/bin/python -m pytest tests/specs/test_ruff_policy.py tests/specs/test_ruff_suppression_index.py -q -n 0` | 40 passed | Parent integration after two independent implementation slices | Broader repository verification and independent Task 4 review remain required. |
| Task 4 traceability | [TS-3], [TS-3.1], reciprocal tool/test/source pointers | `../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json` | Expected repository-debt exit 1; 357 spec sections, 1038 code refs, 741 mappings, 45 errors, 1025 warnings, 610 infos; zero issue keyed to TS-3, TS-3.1, the tool, or the two policy modules | Parent integration | Unrelated baseline traceability debt remains unchanged at 45 errors and 1025 warnings. |
| Task 4 repository verification, first pass | Full default pytest suite | `./.venv/bin/python -m pytest` | 3211 passed, 3 skipped, 1 failed: `test_agent_session_eof_without_result_closes_session` observed the alternate same-turn channel-failure diagnostic; the exact test then passed immediately both with `-n 0` and default xdist | Parent triage | The touched line in `AgentSession` is a comment-only suppression; rerun the full suite before requesting the Task 5 checkpoint. |
| Task 4 repository verification, clean rerun | Full default pytest suite | `./.venv/bin/python -m pytest` | 3212 passed, 3 skipped | Parent integration | None for Task 4; the prior same-turn race did not recur. |
| Task 4 review fix | Normal-Ruff malformed valid JSON | `./.venv/bin/python -m pytest -n 0 tests/specs/test_ruff_suppression_index.py::test_normal_ruff_valid_non_list_json_is_tool_failure_without_writing -q` | Expected RED raised `TypeError` on JSON `null`; GREEN exits 2, emits one line without traceback, and preserves spec bytes under `--write` | Independent review found the gap; parent fixed it TDD-first | Closed; the full focused policy/tool suite passes 41 tests. |
| Task 4 final static gates | Complete lint, format, type, raw-inventory, and registry checks | `./.venv/bin/ruff check .`; explicit-root `ruff format --check`; full configured mypy; raw `--ignore-noqa`; suppression `--check` | Ruff clean; 404 files formatted; mypy clean across 198 source files; raw inventory `C901=152`, `E402=22`, `F401=5`; registry clean | Independent Task 4 re-review PASS | Task 5 requires an owner-directed checkpoint commit before any candidate worktree begins. |
| Task 5 accepted baseline | Atomic Task 4 activation | `git show --stat --oneline a7e7d6ed05b99ff233e56ba24afff1f83ce60158`; `git status --short` | Owner-directed checkpoint `a7e7d6ed05b99ff233e56ba24afff1f83ce60158`; accepted worktree clean before the RW-001 queue entry | Owner authorized the checkpoint commit | RW-001 must branch from this exact SHA. |
| RW-001 attempt 1 | `_normalize_weft_override_value` in its dedicated candidate worktree | Focused constants tests; normal/raw Ruff; suppression reconciliation; 560-case baseline/candidate behavior sweep | Correctness gates passed; score-driven structure was not accepted | Fresh clean-context Python expert: `NET NEGATIVE` | Candidate preserved and queued for attempt 2; accepted source/spec/index remained at checkpoint. |
| RW-001 attempt 2 | `_normalize_weft_override_value` immutable rule-record revision | Focused constants tests; normal/raw Ruff; suppression reconciliation; 560-case baseline/candidate behavior sweep | Correctness gates passed; the record schema still reduced one-key reasoning and permitted live/removed overlap | Second fresh clean-context Python expert: `NET NEGATIVE` | Candidate preserved and reworked as FIFO head for attempt 3. |
| RW-001 attempt 3 | `_normalize_weft_override_value` named-category unified-registry revision, applied atomically to the accepted worktree | `tests/system/test_constants.py` plus the closest monitor configuration proof; normal Ruff; focused mypy; suppression reconciliation; 560-case baseline/candidate behavior sweep; exact accepted/candidate diff comparison | 75 focused tests passed; Ruff and focused mypy passed; suppression registry is current; 560/560 outcomes match; target C901 score reduced from 69 to 1 and the largest new rule method is 8 | Third fresh clean-context Python expert: `NET POSITIVE` | Awaiting owner direction for the checkpoint commit; do not begin RW-002 before that checkpoint. |

## 19. Revision Log

| Date | Reviewed baseline | Revision | Reason | Re-review |
|---|---|---|---|---|
| 2026-08-04 | Initial draft at `3ada6ccb2c3c419119c44ed4426818aa43fc4abf` | Created the Weft-adapted C901 activation, human registry, symbol-index tool, and exact audit plan from SimpleBroker's landed end state. | User requested a local-rules implementation plan for the same capability. | Pending independent plan review. |
| 2026-08-04 | Independent review of the initial draft | Added raw-C901 registration completeness; bypass mutation tests; exact [TS-3] implementation mapping and reciprocal citations; coherent CI ownership; explicit Backstitch roots and comparison gate; canonical inventory grammar. | The copied SimpleBroker reconciler alone could admit an untagged C901, and the first draft left traceability and CI boundaries ambiguous. | PASS for the plan framework; mandatory second review remains after Task 1 adds exact findings and rows. |
| 2026-08-05 | User-directed plan revision | Added a mandatory clean-context Python-expert review after every refactor, with a binary net-positive/net-negative locality verdict, non-batching rule, and evidence record. The initial automatic-revert disposition was superseded by the rework-queue revision below. | A passing test suite and lower C901 score do not prove that a refactor improved logical locality or comprehensibility. | PASS in scoped independent plan review; disposition subsequently revised at user direction. |
| 2026-08-05 | User-directed rework disposition | Replaced automatic revert on a negative/uncertain locality verdict with a durable Task 5 Rework Queue. Every candidate is developed and preserved in a dedicated sibling worktree/branch from an owner-directed checkpoint SHA while the accepted worktree retains its last coherent source/registry/index state; each rework attempt gets fresh proof and a new clean-context review; accepted slices require owner-directed checkpoint commits before further candidates; Task 5 cannot close with pending work. | Preserve useful work and iterate toward the best combined complexity, locality, and comprehensibility outcome instead of discarding a promising candidate after one review. | PASS after scoped review required dedicated worktree isolation and explicit owner-directed checkpoint baselines. |
| 2026-08-05 | Task 1 baseline at `3ada6ccb2c3c419119c44ed4426818aa43fc4abf` | Added the exact 152-finding disposition ledger and initial 107 canonical proposed human rows: S1=7, S2=42, S3=103; directive/raw cardinality=152/152. | Activation requires reviewed per-symbol decisions and exact human-owned approval data rather than an inferred allowlist. | Task 2 round 1 BLOCKED and superseded these initial group/disposition counts. |
| 2026-08-05 | Task 2 outside-review revision | Split every challenged mixed-invariant group, reclassified evidence/temporal owners `_manager_record_diagnostic` and `AgentSession.wait_ready` from S1 to S3, and replaced broad proof paths with exact firing pytest node IDs or exact real tool commands. Revised totals: 123 groups; S1=5, S2=42, S3=105; directive/raw cardinality remains 152/152. | The initial proposal allowed one group approval to cover distinct semantic boundaries and treated two precedence ladders as unproven refactor seams. | Clean Task 2 re-review PASS; owner approved all 123 proposed rows on 2026-08-05. |

## 20. Review Log

| Review | Date | Verdict | Disposition |
|---|---|---|---|
| Independent plan review, round 1 (`gpt-5.6-terra`) | 2026-08-04 | BLOCKED | Accepted F1: require every raw C901 to map to one tagged approved directive and add an aggregate-bypass mutation for both modes. Accepted F2: add exact atomic implementation mapping, reciprocal citations, and Backstitch gate. Accepted F3: keep focused tests in the existing pytest matrix and define lint-job ownership precisely. |
| Independent plan review, follow-up | 2026-08-04 | BLOCKED | Accepted the missing `integrations/` and `extensions/` Backstitch roots. Canonicalized the copied ``Global raw-`noqa` inventory:`` grammar. |
| Independent plan review, final | 2026-08-04 | PASS | Plan framework is implementable and non-degrading. Residual gate: Task 1's exact 152-row ledger, proof references, and human registry rows require another independent review before activation. |
| Scoped review of the per-refactor locality gate | 2026-08-05 | PASS | Confirmed that every individual refactor gets a newly created uninvolved Python-expert subagent and that locality/comprehensibility dominate the binary verdict. Its automatic-revert disposition was superseded by the user-directed rework queue. |
| Scoped rework-queue review, round 1 | 2026-08-05 | BLOCKED | A preserved negative candidate could not coexist coherently with the accepted registry/index in one worktree. Required every candidate to begin and remain in a dedicated sibling worktree until accepted. |
| Scoped rework-queue review, round 2 | 2026-08-05 | BLOCKED | A branch from HEAD could omit accepted but uncommitted Task 4 or prior Task 5 changes. Required an owner-directed checkpoint SHA before Task 5 and after every accepted candidate. |
| Scoped rework-queue review, final | 2026-08-05 | PASS | Confirmed that negative work is preserved without contaminating accepted policy state, every candidate uses an exact committed baseline, withheld commit authority pauses work, and no pending queue state can pass Task 5 completion. |
| Task 2 clean outside ledger review, round 1 | 2026-08-05 | BLOCKED | Accepted F1: split ten mixed-invariant group families into exact semantic owners. Accepted F2: reclassify `_manager_record_diagnostic` and `AgentSession.wait_ready` from unproven S1 seams to S3. Accepted F3: replace broad file/suite proof references in every group with exact firing pytest node IDs or exact real tool commands. Revised proposal mechanically reconciles 152 findings, 123 groups, S1=5/S2=42/S3=105, and 202 unique collectable pytest proof nodes with zero missing references. |
| Task 2 clean outside ledger review, final | 2026-08-05 | PASS | Independently reconfirmed all 152 identities and 123 group cardinalities; all challenged groups now have coherent boundaries; both unsafe S1 candidates are S3; 202 exact proof nodes collect; 22 targeted proofs and both real governance-tool commands pass. Proposal is ready for explicit owner approval. |
| Task 2 owner approval | 2026-08-05 | PASS | Owner explicitly approved the §5A dispositions and all 123 human suppression groups, authorizing Tasks 3 and 4. Approval text was recorded in every proposed row before activation work began. |
| Task 4 independent implementation review | 2026-08-05 | BLOCKED | Found one anticipated-failure contract gap: valid non-list JSON from normal Ruff escaped with a traceback instead of exit 2. All C901 identities, groups, discovery, CI ordering, generated content, byte preservation, tests, and AST semantic equivalence otherwise passed review. |
| Task 4 focused re-review | 2026-08-05 | PASS | Confirmed decoded normal-Ruff shape validation plus a CLI-level exit-2/no-traceback/no-write test. No further findings; 41 focused tests and all static gates pass. |
| RW-001 locality review, attempt 1 | 2026-08-05 | NET NEGATIVE | The `partial` dispatch preserved behavior but moved each override's accepted types, conversion, parser, and error across several scopes. Preserved the candidate worktree and reworked the FIFO head toward immutable, self-describing per-key rules. |
| RW-001 locality review, attempt 2 | 2026-08-05 | NET NEGATIVE | Immutable rows still used cryptic conversion values, duplicated accepted types with error descriptions, and split live/removed precedence across maps. Preserved the candidate and required named categories plus a unified registry for attempt 3. |
| RW-001 locality review, attempt 3 | 2026-08-05 | NET POSITIVE | The unified registry makes each key's category, parser, and removal policy auditable in one scan. Named adjacent helpers preserve native coercion, parser-first behavior, exact type errors, removed-key precedence, and unknown-key passthrough. Import-time validation and direct loader/registry parity prevent structural and key-set drift. Residual risk: they cannot detect a wrong valid parser assigned to a row, but the reviewer found that risk did not outweigh removing the brittle AST inspection and long conditional chain. |

## 21. Fresh-Eyes Checklist

- [x] Every baseline finding appears exactly once in the Task 1 ledger before
  activation.
- [x] Every retained group protects a real invariant and names real proof.
- [x] No proposed split is justified only by score or file size.
- [ ] Every retained Task 5 refactor has an individual clean-context Python
  expert verdict of `NET POSITIVE` for logical locality and comprehensibility.
- [ ] Every negative or uncertain refactor is preserved in the Task 5 Rework
  Queue with concrete improvement criteria, and no
  candidate/queued/reworking/awaiting-checkpoint entry remains at completion.
- [x] The six extensionless Python tools are discovered and the two Bash tools
  are excluded.
- [x] The proposed spec, human rows, source directives, tool, tests, and CI
  activate atomically.
- [x] Human-owned and generated content have an unambiguous edit boundary.
- [x] Every documented error and exit class has a firing test.
- [x] Runtime and public contracts are explicitly unchanged.
- [x] Rollback restores one coherent policy state.
- [x] Independent review and owner approval occur before activation.
- [x] Traceability and current-state verification are explicit completion
  requirements.
