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

Weft's Python lint gate uses the stable default rule set of the Ruff version
locked in `uv.lock`, extended with the repository's reviewed `E`, `W`, `F`,
`I`, `B`, `C901`, `C4`, and `UP` rule families. The configuration extends
Ruff's defaults rather than replacing them. The gate covers every tracked
first-party `.py`/`.pyi` file and Python-shebang repository tool. Ruff owns
Python file discovery; configuration includes tracked extensionless Python
tools explicitly and must not parse Bash tools as Python.

Owner: `pyproject.toml` owns rule selection and discovery; the root CI lint
job enforces it. Boundary: `weft/`, `tests/`, `integrations/`, `extensions/`,
and Python tools under `bin/`. Verification:
`tests/specs/test_ruff_policy.py` invokes the real repo-managed Ruff binary,
compares effective discovery and the complete enabled-rule set with reviewed
inventories, proves every behavior-affecting policy setting fires, and proves
that a stable-default rule outside the retained families and a retained-family
rule both fire. Required action: a Ruff version or rule-selection change
intentionally reviews and updates the enabled-rule inventory before changing
the lock or configuration.

Requirements:

- configured rule families extend Ruff's stable defaults; preview rules
  remain opt-in and are not part of the default gate;
- intentionally broad exception, retry, plugin, process, and best-effort
  cleanup boundaries retain runtime behavior through explicit structure where
  practical; a suppression is the reviewed last resort, not the default
  alternative to a behavior-changing rewrite;
- changing a public exception type, signature, resource lifetime, subprocess
  policy, or output shape to satisfy lint requires direct compatibility proof
  from owning tests or downstream type checks;
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
`bin/ruff_suppression_index.py` parses the standalone [TS-3.1 operational
registry](../ruff-suppression-registry.md), invokes normal and raw Ruff,
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

This section governs approved local exceptions to [TS-3]. A plan may propose or
review a candidate, but it must not become the lasting source of truth for an
adopted exception. The approved exception records, global raw-diagnostic
inventory, and generated symbol index live in the standalone
[Ruff Suppression Registry](../ruff-suppression-registry.md). That operational
ledger is not normative and is not required reading.

_Implementation mapping_: approved local source directives across `weft/`,
`tests/`, `bin/`, `integrations/`, and `extensions/` point to the standalone
registry; `bin/ruff_suppression_index.py` reconciles those directives with the
human rows, raw Ruff findings, and generated symbol index; and
`tests/specs/test_ruff_policy.py` plus
`tests/specs/test_ruff_suppression_index.py` prove the live policy and
check/write failure boundaries.

Owner: the standalone registry owns each stable suppression group,
human-reviewed rationale, and approved cardinality. The local directive owns
rule codes and the stable group pointer. The generated index owns only derived
paths, qualified symbols, actual directive counts, and raw-diagnostic counts.
Boundary: only the rules, cardinality, invariant, and locations covered by the
approved group. Verification: the named real proof, `ruff check .`, and
`./.venv/bin/python bin/ruff_suppression_index.py --check`. Required action:
obtain explicit review before adding, regrouping, growing, or shrinking a
suppression; update the human row, cardinality, and source pointer together;
then regenerate only the delimited derived index with
`./.venv/bin/python bin/ruff_suppression_index.py --write`.

The approved local form is
`# noqa: <codes> approved [TS-3.1] [RUFF-SUP-NNN] exception`. The stable group
points to the single durable full reason; source comments do not duplicate it.
Group IDs are unique and match `RUFF-SUP-[0-9]{3}`. Every group has at least
one live source directive. Human rows contain `Group`, `Rules`, `Approved
cardinality`, `Protected invariant`, `Real proof`, `Rejected alternatives`,
and `Approval`.

The standalone registry also owns one lexically sorted
``Global raw-`noqa` inventory:`` line containing backticked `CODE=count`
entries for every diagnostic exposed by `--ignore-noqa`. The backticks around
`noqa` are part of the canonical parser grammar. This aggregate is a tripwire,
not a second identity registry.

The generated index is enclosed by unique begin/end markers. It renders one
deduplicated `path::qualified_symbol` site per group, sorted by group ID and
site. A symbol is the outermost enclosing function, qualified by class names,
or `<module>`; decorator lines belong to their function. Physical line remains
the internal identity for matching Ruff diagnostics, duplicate detection, and
error messages. Content outside the generated markers is human-owned and must
remain byte-for-byte unchanged during regeneration.

The repository tool must refuse to write if normal Ruff is unclean, a source
or registry marker is malformed, a group is unknown or empty, a rule or
cardinality differs, a raw diagnostic does not match its directive, the global
inventory differs, any raw `C901` diagnostic lacks a tagged approved directive
at the same `noqa_row`, or discovered Python source is unreadable or
syntactically invalid. Policy mismatches exit 1. Anticipated invocation,
decoding, Ruff, and atomic-replacement failures exit 2 with a one-line
diagnostic and no traceback. Both classes leave the standalone registry
byte-for-byte unchanged. Unexpected programming defects retain a traceback as
bug evidence.

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

- [`docs/plans/2026-08-07-ruff-suppression-registry-extraction-plan.md`](../plans/2026-08-07-ruff-suppression-registry-extraction-plan.md)
- [`docs/plans/2026-08-05-ruff-stable-default-lint-expansion-plan.md`](../plans/2026-08-05-ruff-stable-default-lint-expansion-plan.md)
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
