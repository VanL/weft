# Test Audit Repairs

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-2]; existing owner contracts listed below
Superseded by: none

Class: 3. Multiple test surfaces change; no product behavior, test policy,
public API, dependency, or runtime lifecycle changes. Hardening: N/A. Tests
adopt existing isolation and cleanup paths rather than introducing a lifecycle.

## Goal

Remove three verified redundant or stub-only tests and repair the audited
tests' assertions and isolation without new production seams or frameworks.

## Source Documents and Spec Baseline

Baseline: `132626153d9232e8469da72562f1ea795f9a95cf`. The existing admission
work modifies Manager Architecture and System Invariants; those sections are
outside this change. Governing existing sections:

- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-2]: real owner
  boundaries, harness isolation, reactor driver and producer closure.
- `docs/specifications/07-System_Invariants.md` [STATE.1], [IMPL.10]: lifecycle
  transitions and reactor ownership. No normative edits planned.
- `docs/specifications/03-Manager_Architecture.md` [MA-2]: durable spawn TID
  correlation. Admission-controller sections are unrelated.
- `docs/specifications/10-CLI_Interface.md` [CLI-6]: backend-native tidy.
- `docs/specifications/13-Agent_Runtime.md` [AR-5]: no implicit startup probes
  and compatibility of consumed SDK calls.
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-8.1], [DJ-8.4]:
  request client routing.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], [SB-0.4a]:
  canonical broker configuration and semantic Monitor schema validation.
- `docs/agent-context/runbooks/testing-patterns.md` and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` govern
  verification and independent review. The invoked test-audit skill governs
  retention and sensitivity evidence.

This is maintenance of existing proofs, not implementation or revision of a
spec-owned product boundary. Product mappings and normative specs remain
unchanged; this plan records test ownership and validation.

## Outcomes and Invariants

- [x] Delete the three named tests only after confirming remaining proof.
- [x] Replace Django source-string assertions with facade execution.
- [x] Retarget Monitor catalog-query checks while preserving unique checks.
- [x] Prove tidy invokes maintenance at the command owner; keep CLI evidence.
- [x] Observe forbidden provider launches, actual SDK call compatibility,
  canonical removed config keys, and reachable network denial.
- [x] Isolate four client tests, explicitly clean up audited task instances,
  and scope fake clocks to their owning modules.
- [x] Review the two reactor architecture guards; retain if unique proof is
  unresolved rather than deleting coverage speculatively.
- [x] Run focused, full, backend, type, lint, and independent-review gates.

Preserve product code, public APIs, queue names, TID correlation, TaskSpec
immutability, allowed transitions, resource semantics and all unrelated dirty
files. No new dependencies, production hooks, configuration or CI changes.
No commits or pushes. Record review state in the handoff.

## Slices and Files

1. **Independent plan review and removal/isolation batch (parent).** Read the
   complete candidates and owners. Edit `tests/commands/test_manager_commands.py`,
   `tests/specs/manager_architecture/test_spawn_retry.py`,
   `tests/specs/taskspec/test_state_transitions.py`, `tests/core/test_client.py`,
   `tests/tasks/test_task_observer_behavior.py`,
   `tests/tasks/test_consumer_terminal_events.py`,
   `tests/system/test_reactor_driver.py`, `tests/system/test_constants.py`.
   Keep the real stop-manager PID test, real persisted spawn correlation, and
   exhaustive lifecycle pair matrix. Existing audit controls caught each
   removed regression. Reuse `WeftTestHarness`, `broker_env`, task cleanup and
   module-local fake clocks; do not add injection hooks.
2. **Adapter assertions.** Edit the Django test, tidy CLI/command tests, and
   `tests/core/test_agent_validation.py`. Keep actual facade/validator/command
   owners, replace only dependencies. Name each facade's expected operation.
   Probe controls must fail on bypassed request selection, removed vacuum,
   and a hidden provider launch, respectively.
3. **Schema and extension assertions.** Edit `tests/core/test_monitor_sql.py`,
   `tests/core/test_monitor_store.py` only if unique schema proof needs a home,
   `extensions/weft_microsandbox/tests/test_runtime_adapter.py`, and the real
   microsandbox network test. Read actual SDK call sites and installed SDK.
   Bind consumed calls; prove optional signature growth accepted and required
   growth rejected. Network test requires an allowed-network guest control
   against the same endpoint before denied execution. If runtime unavailable,
   report skipped live proof. Review `tests/specs/test_reactor_architecture.py`
   against runtime tests without speculative deletion.
4. **Integrated verification and independent coverage review.** Wait for all
   edits before starting tests. Format changed Python files only; run focused
   tests at default xdist, then full suite, Django/extensions, PostgreSQL schema
   cases, mypy and lint. Reviewer compares removed assertions to keepers and
   checks that replacements can fail. Fix accepted findings and rerun affected
   gates. Update this plan and its index with evidence.

## Execution and Verification

Independent edit lanes own disjoint file sets; no edits while any test run is
in flight in this checkout. Delegates report edits ready for central testing
and do not launch tests independently. Sensitivity controls use isolated
in-memory monkeypatches or temporary subprocess hooks, never persisted owner
changes. Reuse recorded controls only where the keeper remains unchanged.

All commands source `.envrc` and use the repo virtualenv:

- Focused pytest of the files above plus `test_tid_correlation.py`, the real
  PID stop keeper, and plan metadata at default parallelism, including slow.
- `./.venv/bin/python -m pytest -m ''` for the full canonical suite.
- Django and microsandbox suites using their repo-supported runner.
- `./.venv/bin/python bin/pytest-pg --all tests/core/test_monitor_store.py`
  for actual PostgreSQL schema behavior.
- `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django
  extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`.
- `./.venv/bin/ruff format <changed Python files>`;
  `./.venv/bin/ruff check .`; `git diff --check`.

Testing-only work uses sensitivity controls as the sanctioned failing-first
proof: broken-owner runs fail the improved tests for their intended reason,
then restored owners pass. Isolation fixes reuse audited concrete side effects
and verify local roots and task cleanup. No product fix is presumed.

## Reviews

Self-review: draft checked for scope creep and missing keeper proof. Clarified
that unresolved architecture guards stay and all edit lanes finish before
testing. Independent same-family review required; available collaboration
tools expose no different-family reviewer.

Independent plan review: PASS. Accepted guardrails: remove the empty spawn
module with its scaffolding; preserve unique PostgreSQL catalog semantics;
drive SDK compatibility checks from actual adapter calls; restore fake clocks
before task cleanup; use the same guest command/endpoint for network control
and denial; retain unresolved architecture guards. The parent applies these
constraints and a different delegate reviews the completed schema slice.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Evidence

Current-state verification:

| Check | Command / boundary | Observed result |
| --- | --- | --- |
| Full canonical suite | `. ./.envrc`; `./.venv/bin/python -m pytest -m '' --tb=short --timeout=900 --timeout-method=thread` | 5,266 passed, 44 skipped, exit 0 |
| Focused repair owners and keepers | pytest of the slice files, PID-exit keeper, lifecycle matrix, spawn correlation and plan metadata at default xdist | 484 passed, 5 skipped before the generated-column review refinement; refined SQL owner subsequently passed |
| Django and all extensions | pytest of `integrations/weft_django/tests`, Docker, macOS sandbox and microsandbox extension tests, `-m ''` | 375 passed, 5 skipped |
| PostgreSQL | `./.venv/bin/python bin/pytest-pg --all` with Monitor SQL/store, agent validation, client, system public contract, observer and consumer-terminal files | 291 passed, 2 SQLite-specific skips |
| Discovery and documentation neighbors | pytest Ruff policy, suppression-index and plan-metadata files | 86 passed |
| Static gates | full prescribed mypy command; `ruff check .`; format checks of all 15 changed Python files; `python bin/ruff_suppression_index.py --check`; `git diff --check` | all exit 0; mypy checked 439 source files |
| Client isolation | exact nine changed parameterized client bodies in an empty ambient working directory, using isolated sibling roots | all passed; ambient directory stayed empty |
| Task resource ownership | exact twelve observer/consumer case bodies, observing original cleanup | all passed; lifecycles CLOSED and owned queue caches empty; real clock restored before cleanup |

Native microsandbox preflight reported "Microsandbox runtime is not installed".
Its four opt-in live cases were skipped; no live network-enforcement claim is
made. Eleven live-provider cases were also opt-in skips in the full suite.
PostgreSQL-specific skips in the SQLite run are distinguished from the real
PostgreSQL proof above.

### Sensitivity Controls

Controls were disposable in-memory substitutions in separate pytest processes;
no production source or configuration changed. Restored owners passed the
canonical, extension and PostgreSQL reruns above.

| Control | Intended observation |
| --- | --- |
| Remove only real tidy owner's vacuum invocation | compaction assertion failed |
| Launch fixture provider `--version` and ignore its nonzero result | all five no-launch cases failed on recorded process creation |
| Bypass selected Django request client for a fresh client | all sixteen facade/alias cases failed on missing selected-client calls |
| Stop filtering the removed WEFT vacuum override | canonical `VACUUM_LOCK_TIMEOUT` absence assertion failed |
| SDK create adds optional keyword-only parameter | both network cases still passed |
| SDK create adds required parameter or makes name keyword-only | both cases failed for missing required argument / positional incompatibility |
| PostgreSQL validity/readiness projections become TRUE | each false-flag case failed at its semantic assertion |
| PostgreSQL index query includes payload columns or loses opclass/collation | catalog result comparison failed for each mutation |
| PostgreSQL aliases renamed consistently | five cases passed, one SQLite case skipped |
| SQLite table_xinfo replaced by table_info | generated-column presence assertion failed; restored owner passed |
| Real Ruff invocation excludes existing client source | discovery guard failed naming `tests/core/test_client.py`; restored invocation passed |

Deletion keepers were unchanged: prior audit controls caught removed PID-exit
confirmation, ordinary timestamp insertion, and each of eight deliberately
allowed forbidden transitions. Independent review rechecked input and owner
path equivalence before accepting deletion. No product/support seam was added;
the empty spawn-retry module's fixture/proxy scaffolding was removed with it.


Independent work review accepted XINFO-1: ordinary SQLite columns did not
distinguish table_xinfo from table_info. A virtual generated column now asserts its presence and returned
hidden/generated flag. The focused control and restored run verified the fix. Round-two independent review: PASS. No other
coverage or implementation findings. The two reactor architecture guards remain:
shared PING ownership has explicit [CC-2.4] support; retired-cap equivalence
across every scanned runtime path remains unresolved.


## Verification Discovery and Disposition

The first integrated full run returned 5,265 passed, 44 skipped, and one
failure: Ruff's discovery guard included an unstaged deleted Git path. The
file itself was already absent, so Ruff correctly omitted it. Repair the
local `_tracked_python_files` oracle to exclude non-files. This preserves the
existing [TS-3] requirement for every tracked source file and changes no Ruff
configuration or process policy. The real failing full run is the pre-change
proof; rerun the guard and full suite after correction. Independent review
checks that an omitted existing source still fails the guard.

Update only the current `RUFF-SUP-017` proof pointer in
`docs/ruff-suppression-registry.md` from the removed stop stub to the retained
real PID-exit keeper. Approval and suppression limits stay intact. Historical
plan references remain historical; no retrospective rewriting.


Scoped final review: PASS for the working-tree discovery filter and current
suppression-proof reference. An omitted existing Python file still produced
an intended failure under the real Ruff exclusion control. All accepted
findings are resolved; product code, normative specs, configuration,
dependencies and CI are unchanged. This record remains a draft until landing;
the conversation handoff states the review/commit state separately.
