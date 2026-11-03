# Test Suite: Release-Tool Tests and Structural Flakiness (owner decision first)

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1], [TS-3], [TS-4]
Superseded by: none

Class: 3. Non-trivial triggers fire: the work spans several test surfaces
(`tests/cli/`, `tests/tasks/`, `tests/core/`, `tests/system/`) and the shared
harness (`tests/helpers/weft_harness.py`), and a zero-context implementer could
pick the wrong seam for every rewrite. No risky trigger fires on the default
branch: no product code changes, and no queue name, CLI shape, TaskSpec field,
or persisted format changes. Two harness edits remain, and neither changes
cleanup or settlement behavior. Task 8 adds warning logging to the existing
teardown path; it consumes return values that teardown already computes, and it
adds no kill path, raise, or new temp-file or queue-cleanup lifecycle. Task 10
deletes an env key (`WEFT_TEST_MODE`) that no product or test path reads. The
terminal-event selector fix (finding H1) was dropped after cross-family round
1 (F4). It changes which tasks teardown sends STOP and KILL to, so it would
fire the cleanup-lifecycle risky trigger (see Dropped). Task 2 adds a
session-start runner calibration probe to `tests/conftest.py` and a helper
module, `tests/helpers/runner_calibration.py` (Ruling D2 addendum). It sizes
the three timeouts of two tests on Windows only; it changes no cleanup or
settlement behavior and no rule about which tests are valid. Re-check this
class if any other harness edit is added.
No normative spec text changes on the recommended branch.
Declared escalators, one-way and immediate:
- **Class 5 if D1 were ruled reading A (variant A1 or A2): did not fire.**
  The owner ruled D1 on 2026-11-03 as reading B with a per-assertion
  criterion (see Ruling D1). Both release-tool test files stay as the [TS-4]
  `Verification:` owners, so no normative spec or theory text changes and the
  plan stays Class 3.
- **Class 3+P (effective 5)** if any task adds a new standing structural
  gate over the test tree (for example a check that bans a timing pattern).
  Such a gate is valid: under the owner's 2026-11-03 ruling (see Ruling D3),
  a test whose purpose is to mechanically gate an aspect of
  `docs/program-theory.md` is a valid test, not ceremony. It would still
  raise the class to 3+P, because a standing gate changes how all future
  tests are verified, which is a process change. This plan adds none; see
  Out of Scope.
- **Re-classify** if any task needs a production code change. This plan
  removes no production seam (see finding S1).

This is cleanup plan 9 of the 2026-11 series ("test-suite structure"). The
reconciliation audit recorded its row, 4.4-f, as `aligned (unsettled)`. The
owner's rulings of 2026-11-03 settle it: row 4.4-f is `aligned` for D1 (the
release-tool tests) and `drifted` for D2 (platform-scaled load in the two
Manager-reuse stress tests). The D2 entry is this plan's drift row
(4.4-f → plan 9), which satisfies the series start rule. Ruling D3 lets
Tasks 3 to 10 land under that row.

## Owner decision required

The tree cannot settle three questions (D1 to D3) and one sub-question (D1a). Tasks 1 to 10 are conditional on
these rulings. Record each ruling here, with date and owner, before the first
edit. Do not edit `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
to record them: that plan is `completed` and therefore immutable.

### D1. Are the release-tool tests "release script detail" or shipped-tool correctness?

**Subject.** `tests/system/test_release_script.py` (37 `def test_` functions,
46 collected cases) loads `bin/release.py` by path and exercises its
functions. `tests/system/test_release_workflow.py` (1 test) parses
`.github/workflows/release.yml`. Both are `shared` tests in the default fast
suite.

**The conflict.** [ALT-WT-018] rejects "a test that asserts an exact
dependency version, a CI step order, or a release script detail", because
"the test encodes process, not correctness". [WT-4] 4.4 ("Tests prove
correctness, nothing else") repeats it: never "CI script order". [TS-4]
names these tests as the owners: "`tests/system/test_release_script.py`
parses the workflow DAG and exercises release target/status logic;
`tests/system/test_release_workflow.py` checks publication assets." [TS-4]
also gives the release helper a fail-closed contract: it must not tag an
untested SHA, must not silently move a tag, and must publish only the
selected package.

**What the tests actually assert.** I read every test. They fall into three
groups:

| Group | Tests | What a failure means |
|---|---|---|
| (i) Decisions made by the shipped tool (32 functions) | Version handling: `test_validate_version_accepts_explicit_semver`, `test_validate_version_rejects_invalid_values`, `test_read_current_version_rejects_mismatch`, `test_write_version_files_updates_pyproject_and_constants`, `test_write_target_version_updates_extension_pyproject_only`, `test_resolve_target_version_reuses_current_when_unpublished`, `test_resolve_target_version_requires_new_version_after_publication`. Tag safety: `test_plan_tag_action_rejects_existing_tag_on_different_commit`, `test_plan_tag_action_replaces_stale_local_tag`, `test_plan_tag_action_replaces_remote_tag_only_with_retag`, `test_post_ci_revalidation_rejects_publication_or_tag_identity_changes`. CI-run identity: `test_matching_test_runs_accepts_only_exact_main_push_and_selects_latest`, `test_wait_for_test_workflow_fails_immediately_on_completed_failure`. Target selection and dry-run plans: `test_inspect_release_state_uses_target_package_name_and_tag_namespace`, `test_django_release_target_uses_namespaced_tag_and_package_dir`, `test_collect_extension_release_plans_skips_already_published_packages`, the six `test_main_dry_run_*` tests, `test_main_rejects_removed_publish_flag`. Environment and auth: `test_github_repo_slug_from_remote_removes_one_git_suffix`, the two `test_github_api_auth_headers_*` tests, `test_merge_command_env_appends_pytest_addopts`, `test_merge_command_env_drops_non_project_virtualenv`, `test_run_command_dry_run_shows_env_prefix_and_cwd`, `test_docker_extension_tests_are_disabled_on_windows`, `test_build_precheck_commands_include_extension_tests_when_supported`, `test_build_precheck_commands_skip_extension_tests_when_unavailable` | The release helper would publish the wrong version, reuse a published version, move a tag, accept a PR or stale-SHA CI run, publish an extension the operator did not select, or run with a stray virtualenv. PyPI uploads cannot be undone, so each of these is a one-way-door failure. |
| (ii) Behavioral checks mixed with copied-configuration assertions (3 functions) | `test_build_precheck_commands_cover_release_gate_and_quality_gates` checks real gate coverage and targets, but its comment says reordering is harmless and it ends with an exact `release.PRECHECK_ENV_OVERRIDES == {...}` equality and an exact extras-set equality. `test_build_precheck_env_overrides_sets_worker_count` exercises real forwarding (the patched helper returns `"17"` and production must propagate it), but asserts it through an exact equality that repeats the whole base dict. `test_build_postupdate_steps_build_all_publishable_packages` detects an omitted build, but checks `steps[0]` to `steps[5]` by index, including the literal `pytest tests/system/test_constants.py` step, and uses production directory constants as its oracle. | The behavioral parts catch a real gate, forwarding, or build omission. The copied-configuration parts fail when someone adds an unrelated precheck env key, adds an extra, or reorders build steps, with no correctness change. Only those parts take the shape [ALT-WT-018] names, which the test-audit skill also lists as junk: "expected values copied from the helper ... under test" and "copied ... inventories, manifests". These tests are not tautologies as whole tests; Task 1 narrows them. |
| (iii) Workflow structure the release path depends on (3 functions) | `test_release_gate_routes_each_tag_to_exactly_one_package` (each `release-gate*.yml` tag pattern routes to exactly one package); `test_publish_checks_completed_main_push_once_without_polling` (string checks on the `actions/github-script` body in `release.yml`: `workflow_id: "test.yml"`, `head_sha: expectedSha`, `event: "push"`, completed and success checks, no `while (` and no `setTimeout`); `test_github_release_uploads_only_python_distributions` (release assets are exactly `dist/*.tar.gz` and `dist/*.whl`; no `id-token`; no sigstore step) | A tag would publish the wrong package, the gate would poll a running workflow (forbidden by [TS-4]), or the release would ship sidecar files. One assertion in group (i) is ordering: `test_main_dry_run_defaults_to_core_only` asserts push `main`, then wait, then tag. [TS-4] mandates that order, so the order is the contract. |

**Reading A ([ALT-WT-018] refuses them).** "Tests prove correctness" means
correctness of Weft, and the release flow is process around Weft. A regular
change to the release flow ("a regular update should not break things")
should not turn the suite red. Group (iii) asserts workflow strings and step
wiring. Group (ii) asserts a step list and an env dict. Even group (i) tests a
script that ships nowhere: `bin/` is not in the wheel. Consequence: delete
both files (A1), or only groups (ii) and (iii) (A2). Either variant edits the [TS-4] `Verification:` sentence, which is
Class 5. The proof each variant removes differs. A1 leaves the [TS-4]
fail-closed guarantees (never tag an untested SHA, never move a tag, publish
only the selected package) without executable proof. A2 keeps group (i), so
the tag-safety, CI-run identity, and target-selection tests still prove those
guarantees inside the helper. What A2 removes is group (iii): the only
executable guard of the workflow-side [TS-4] clauses (each gate tag routes to
exactly one package, the publish gate looks up one completed Test push without
polling, release assets are Python distributions only). Either variant removes
a capability the release path has today.

**Reading B ([TS-4] wants them).** `bin/release.py` is shipped tooling. The
owner relies on it to make irreversible publications, and [TS-4] specifies
its contract with Owner, Boundary, Verification, and Required action. The
[ALT-WT-018] candidate is a test that uses process as a proxy for some other
correctness ("the test catches a drift early"). Group (i) is not a proxy:
the release helper's behavior is the subject. Group (iii) is the cheapest
independent guard of [TS-4] clauses that cannot run locally (the gate's
JavaScript runs only in Actions). The test-audit retention bar keeps source
inspection when it "is the cheapest independent guard". Group (iii) meets
that part of the bar, but not all of it. The bar also asks that the check
"survives an identifier-only refactor", and some group (iii) string checks do
not: renaming the script's `expectedSha` or `latest` variable breaks
`test_publish_checks_completed_main_push_once_without_polling` with no
behavior change. (At drafting time this plan called that brittleness the
accepted cost of the only executable guard and left narrowing as an untasked
follow-up. Ruling D1 overrides that: the identifier-dependent checks are
narrowed now, in Task 1, except the two polarity substrings on `latest`,
which the owner kept unchanged on 2026-11-03 with their blind spot named in
the Task 1 row.) Group (ii)'s copied-configuration
assertions do fail [ALT-WT-018] and the junk-pattern list. Under reading B,
those assertions are narrowed to the contract they stand in for, and the
behavioral checks around them stay. That is ordinary test-audit hygiene and
needs no spec change.

**Recommendation at drafting time: reading B.** Keep both files as [TS-4]'s verification
owners. Narrow group (ii)'s copied-configuration assertions to the
contract they approximate, keeping their behavioral checks (Task 1). Record row 4.4-f as `aligned`. No spec or theory text
changes. Reason: the tests' subject is a shipped tool whose failures are
irreversible publications, [TS-4] names them, and the copied-configuration
assertions are already refused by the test-audit skill without stretching
[ALT-WT-018] over group (i). The fork stays open for the owner: under reading
A, A2 still removes the only executable guard of the workflow-side clauses.

Ruling D1: reading B, with the owner's criterion applied to every
assertion rather than to the group labels (owner, 2026-11-03). The owner's
words, verbatim:

> "To the extent that the tests enforce the correctness of the release
> process, they are valuable and should be kept. To the extent that they make
> it brittle and unable to change, they should go. Tests that simply restate
> something that is already in a canonical file or would break due to routine
> changes, upgrades, or maintenance are wrong."

What the ruling means for this plan:

1. Both files stay as the [TS-4] `Verification:` owners. No spec or theory
   text changes. Row 4.4-f is `aligned` for D1. The Class 5 escalator did
   not fire.
2. Group (i) stays as it is. Each of its tests fails only when the release
   helper would make a wrong or irreversible publication, which is
   release-process correctness.
3. Group (ii): the copied-configuration assertions go. These are the exact
   equality against the whole `PRECHECK_ENV_OVERRIDES` dictionary, the exact
   extras set, the build steps checked by index (including the literal
   `pytest tests/system/test_constants.py` step), and production directory
   constants used as the oracle. They restate a canonical file
   (`bin/release.py`) and break on routine changes. The behavioral checks
   around them stay: real gate coverage, forwarding of the worker count, and
   detection of an omitted build. Task 1 does this, under this ruling's
   authority.
4. Group (iii): the identifier-dependent string checks go, in Task 1, not as
   a follow-up. Each workflow test keeps the property it enforces (each
   release-gate tag pattern routes to exactly one package; the publish gate
   looks up one completed Test push run for the tagged commit and does not
   poll; release assets are Python distributions only, with no identity
   token and no signing sidecar). Each drops what would break on an
   identifier-only refactor or a routine action upgrade. Where a kept
   property could be checked only by a string that is itself a copy of the
   workflow file, the check is dropped and Task 1 names the guard that is
   lost. The per-test disposition is in Task 1.
5. Acceptance criterion for Task 1 (see Testing Plan): after Task 1, no
   assertion in the six tests Task 1 edits equals a value copied from
   `bin/release.py` or from a workflow file, and none names a
   workflow-local identifier (a job id, a step `env` key, or a variable
   declared in the `actions/github-script` body). The acceptance is Task 1's
   per-assertion disposition table plus its sensitivity controls, verified
   by reading the diff against the table and running the controls. Task 1's
   Verify also carries a one-time AST command, labelled a lint aid and not
   proof: it flags four syntactic patterns and has known false positives
   and false negatives. Neither is a standing policy gate over the test
   tree, so the "Class 3+P" escalator does not fire.

Scope note, an owner question and not tasked. Consequence 2 keeps group
(i) whole, but a per-assertion reading of the criterion would also reach a
few group (i) assertions whose expected values are copies of
`bin/release.py` configuration rather than values derived from the test's
own input. The plan's recommendation to the owner, per location, is the
cross-family reviewer's assessment from round 1 on the D1–D3 changes
(locations in `tests/system/test_release_script.py` at `b7eed9f9`):

| Location | Recommendation |
|---|---|
| `:191` (`"weft_docker/v0.1.0"`) | Keep the tag-construction behavior. The embedded production namespace is replaceable configuration. |
| `:201` to `:203` (`"weft-django"`, `"weft_django/v0.1.0"`, and `release.DJANGO_INTEGRATION_DIR` as the oracle) | Mostly configuration restatement. Keep tag composition, checked with a test-owned target. |
| `:711` and its sibling lines (negative checks on the copied `"weft_docker/v"` namespace and the other namespaces) | Keep "a core-only release publishes no extension". Replace the hard-coded namespace fragments with relations to the non-selected targets. |
| `:872` and `:896` (exact pytest-target sets copied from the precheck command tuples) | Keep supported-suite inclusion (`:872`) and unsupported-suite exclusion (`:896`). Narrow the copied inventories. |
| `:943` (`"--lf -x --maxfail=1"`) | Keep the env-appending behavior, with test-owned overrides instead of the canonical flag string. |
| `:984` to `:985` (`PYTEST_ADDOPTS='-x --maxfail=1'` and `WEFT_EAGER_FAILURE_TRACEBACK=1` in the dry-run output) | Keep the env-prefix rendering behavior, with test-owned values. |

In each location the behavioral claim survives and the configuration
snapshot need not. The owner decides whether Task 1's scope widens to these
locations. Until the owner says so, Task 1 leaves them unchanged, as
consequence 2 directs.

Scope-note closure: closed on the owner's implement instruction (2026-11-03)
with the recommended option: Task 1's scope widens to the six locations in
the table above, and each row's recommendation is applied as written (see
the Execution log, Task 1). Consequence 2 is superseded for those locations
only; the rest of group (i) stays unchanged.

**D1a (only if D1 = B).** Is "a `pytest` step precedes the first `uv build`"
in `bin/release.py::build_postupdate_steps` a contract? Task 1 drops that
ordering assertion unless the owner rules it one.

Ruling D1a: dropped under the D1 criterion (owner, 2026-11-03). The order is
routine-maintenance detail: [TS-4] mandates push, then wait for CI, then tag,
not where the test step sits among the builds. Task 1 asserts no order among
the post-update steps.

### D2. Is platform-scaled load in the Manager-reuse stress tests a 4.4-f drift?

The reconciliation audit cited only `pyproject.toml` for 4.4-f. The tree
holds another candidate. In `tests/cli/test_cli_run.py`,
`test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
and `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
set `constrained_parallelism = os.name == "nt" or sys.platform == "darwin"`.
They then cut the work under it: `iterations = 3 if constrained_parallelism
else 8` (or `else 5`) and `max_workers = 2 if constrained_parallelism else 4`
(lines 3002-3004 and 3043-3045). The Windows reduction came in at
`28b089e2` (2026-04-09, "Harden Windows release tests"; iterations trimmed
at `fa4e1435`, 2026-04-13). `c4915b04` (2026-05-07, "Scale manager reuse
stress on constrained CI") extended both the load cuts and the longer
timeouts to macOS. All of it predates the 2026-10-14 contention rule. Lowering concurrent submitters by platform so the
test passes is the move [ALT-WT-017] and [WT-7] "Contention failures test
possession of the theory" reject, and it is baked into the test itself.

**Recommendation at drafting time: rule it `drifted`.** That gives this
plan its drift row (4.4-f → plan 9) independent of D1. Task 2 removes the
load reduction. The test-audit skill permits only measured,
platform-specific harness budget corrections that preserve the timing
contract, and the tree records no measurement for these branches. The
2026-11-01 Windows record also refuses padding or lowering load on Windows.

**Fresh-eyes finding for the owner (D2), resolved by the ruling.** At
drafting time the tree did not show that the timeout branches were
measured: `c4915b04` added the macOS timeout branch in the same edit as the
macOS load cut, and no measurement is recorded for either the macOS or the
Windows values. Ruling D2 answers it: the macOS timeout branch is removed
(consequence 3 below), and the Windows branch becomes one calibrated time
factor (consequence 4 and the addendum). A third 4.4-f candidate, not classified here
and not tasked: the first-party extension suites run with `-n 0` in CI
(`.github/workflows/test.yml:306`, `:337`, `:364`, `:391`) and in the
release precheck (`bin/release.py` `DJANGO_INTEGRATION_TEST_COMMAND` and the
three `*_EXTENSION_TEST_COMMAND` tuples, lines 188-263; four suite commands
in all). Ruling D2 speaks to the two Manager-reuse stress tests only and
does not reach this candidate; it stays unclassified and untasked.

Ruling D2: `drifted` for the load reductions (owner, 2026-11-03). The
owner's words, verbatim:

> "As for D2, MacOS should not get any breaks. Windows should run at full
> concurrency as well, but we have observed that its process spawning and
> disk accesses are far slower than Mac and Linux. Accordingly we allow for
> a scaled time for Windows to account for a legitimate platform
> difference."

What the ruling means for this plan:

1. Row 4.4-f gets its drift entry: `drifted` for D2, owned by this plan
   (4.4-f → plan 9). Record it in this plan only; the reconciliation plan is
   `completed` and is not edited.
2. Full load on every platform. Task 2 removes every platform-conditional
   cut of iterations and concurrent workers in both tests. Each test runs
   the same iteration count and the same `max_workers` on Linux, macOS, and
   Windows: the values the tests use today on Linux, which are 8 iterations
   and 4 workers in
   `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
   (lines 3003-3004 at `b7eed9f9`) and 5 iterations and 4 workers in
   `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
   (lines 3044-3045).
3. macOS gets no timeout branch. Today the three timeout values in each
   test are chosen by the same `constrained_parallelism` predicate
   (`os.name == "nt" or sys.platform == "darwin"`, lines 3002 and 3043), so
   macOS runs the longer Windows budgets. After Task 2, macOS runs the
   Linux budgets.
4. Windows keeps a scaled time budget, and only time. The Windows value of
   each timeout becomes the Linux value multiplied by one time factor. Per
   the addendum below, the factor is not a fixed constant: it is a runner
   ratio calibrated once per test session (Task 2, Steps 3 to 5). The
   helper's docstring states the owner's reason: process spawning and disk
   access are far slower on Windows than on macOS and Linux; this is a
   legitimate platform difference, not a load reduction. The factor
   applies uniformly to all three timeouts in both tests; there are no
   separate per-site Windows constants. Windows is not padded beyond the
   factor, and its load is not reduced: the 2026-11-01 Windows record
   refuses both, and the ruling permits time only. The factor's reason is
   the platform difference the owner names, not CI stalls, so it is not the
   refused trigger-table proposal. A reference-baseline measurement taken
   during a whole-VM stall episode is not valid evidence (Task 2, Step 4),
   and a session-time stall cannot inflate a budget past the ceiling
   (Task 2, Step 3).
5. Current values the factor replaces (at `b7eed9f9`, identical in both
   tests, lines 3005-3011 and 3046-3052):

   | Timeout | Linux | Windows and macOS today | Ratio |
   | --- | --- | --- | --- |
   | `submit_timeout` | 60.0 s | 120.0 s | 2 |
   | `status_timeout` | 5.0 s | 10.0 s | 2 |
   | `task_completion_timeout` | 10.0 s | 120.0 s (`PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT`, line 47) | 12 |

   Today's Windows values were never measured, and their ratios differ by
   site (2, 2, and 12). None of them is carried forward: after Task 2 each
   Windows budget is the Linux budget times the session's calibrated
   factor, and macOS runs the Linux budgets.
6. Difference register for Task 2 (A = added, N = no difference,
   C = changed, D = diminution):

   | Change | Linux | macOS | Windows | Note |
   | --- | --- | --- | --- | --- |
   | Full iterations and 4 workers on every platform | N | C | C | The tests now exercise the contention [WT-7] and [TS-0] require on every platform. Expect Windows and macOS wall time to rise; on Windows it is bounded by the factor. |
   | macOS timeout branch removed | N | C | N | macOS runs the Linux budgets. |
   | Calibrated Windows time factor (`windows_time_factor`) | N | N | C | A measured budget that follows the runner: the same assertions run with a deadline scaled by the session's probe, floored at 1.0 and capped at the ceiling. macOS and Linux are pinned to 1.0. |
   | Session-start calibration probe (`probe_runner`) | N | N | N | Its own cost: once per session in the xdist controller, a few hundred milliseconds on Linux and macOS (longer on Windows, where each spawn is slower). It checks nothing and changes no assertion. |

   No D row: nothing the tests check is removed on any platform. Rollback:
   the two tests revert with Task 2's commit; no product code changes.

Ruling D2 addendum: calibrated runner ratio (owner, 2026-11-03). After
being shown SimpleBroker's calibration mechanism and the published evidence
that Windows process creation is roughly ten to twenty times slower than
Linux and that hosted Windows runners' disk I/O is several to forty times
slower and highly variable, the owner's words, verbatim:

> "Yes, let's try that for 9."

The earlier ruling stands unchanged: full load on every platform; no macOS
breaks; Windows gets scaled time only, for a legitimate platform
difference. What the addendum changes: the Windows time factor is no
longer one fixed constant set from the two tests' wall times. It is a
calibrated runner ratio in the shape of SimpleBroker's test calibration
(read-only reference: `../simplebroker/tests/performance_calibration.py`
`REFERENCE_BASELINES` :23, `calibrate_machine_performance` :81,
`get_calibration_ratio` :144; `../simplebroker/tests/helper_scripts/timing.py`
`_machine_performance_ratio` :163, `scale_timeout_for_calibration` :220,
`scale_timeout_for_ci` :230). SimpleBroker measures named costs on the
current machine, divides committed reference timings by them, and
`scale_timeout_for_calibration` relaxes a timeout by that ratio on a slower
machine and never tightens it on a faster one (`timeout /
min(ratio, 1.0)`); `scale_timeout_for_ci` is a flat ×2 under `CI`, which
Weft does not adopt. Weft does not import SimpleBroker's test helpers; it
mirrors the shape in its own `tests/helpers/runner_calibration.py`, with
three differences the ruling requires: it measures the two costs the owner
names (process creation and SQLite write cost) rather than SimpleBroker's
three; it takes the larger of the two ratios rather than a harmonic mean,
because either cost can bound these tests; and it applies the factor on
Windows only, capped at a ceiling. Task 2 carries the procedure.

### D3. May the non-drift hygiene tasks ride on this plan?

Tasks 3 to 10 enforce rules the repository already has: testing-patterns
Pattern 11 and Pattern 12, the test-audit skill's negative-assertion and
junk-pattern rules, and the 2026-10-31 lesson that a "never after" claim over
a poll-then-check guard is a race. They are not theory drift. The series rule
admits a cleanup plan on a drift row. If D2 is `drifted`, the plan has one,
and the owner can authorize these tasks to land under it. If D2 is not
`drifted` and D1 is B, the plan has no drift row, and these tasks need
explicit owner authorization or their own plan.

**Recommendation: authorize them here.** Each is small, test-only, and owned
by an existing rule.

Ruling D3: authorized (owner, 2026-11-03). The owner's words, verbatim:

> "Agree with keeping D3. If a test exists to put a mechanical gate on some
> aspect of the theory, that is valid."

What the ruling means for this plan: Tasks 3 to 10 land under this plan, on
the drift row Ruling D2 gives it (4.4-f → plan 9). The second sentence is a
principle: a test whose purpose is to mechanically gate an aspect of
`docs/program-theory.md` is a valid test, not ceremony. It is applied in the
class paragraph's 3+P escalator and in the Out of Scope entry on structural
gates. Neither adds a gate.

## 1. Goal

Apply Ruling D1: keep both release-tool test files as [TS-4]'s
verification owners, and narrow every assertion in them that restates a
canonical file or breaks on routine change. That covers group (ii)'s
copied-configuration assertions and group (iii)'s identifier-dependent
workflow string checks, both in Task 1. Then remove
the structural weaknesses that reading the tree confirms. These are tests
that scale load down by platform, sleep-then-assert-absence windows,
negative windows with no entry witness, one stdlib clock patch in the test
that proves a wrapper forwards pending-worker evidence, private-cursor setup
in three Manager tests, harness teardown that discards stop failures and
survivor lists, one manual
harness lifecycle that bypasses failure diagnostics, an inert harness env
key, and a CLI service test that leaves the production-default
LivenessMonitor off. No product behavior changes.

## 2. Source Documents

- `docs/specifications/08-Testing_Strategy.md` [TS-0] (harness ownership;
  contention stop condition; absence assertions need a producer-closure
  boundary), [TS-1] (`tests/system/` holds "release-script invariants"),
  [TS-3] (mypy and ruff cover `tests/` and `bin/`), [TS-4] (release helper
  contract and its named verification owners).
- `docs/program-theory.md` [WT-4] 4.4 "Tests prove correctness, nothing
  else"; [ALT-WT-017]; [ALT-WT-018]; [WT-7] "Contention failures test
  possession of the theory"; [ALT-WT-008] (Manager reuse flag; removal owned
  elsewhere).
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`: audit row
  4.4-f (`aligned (unsettled)`), "Rows that need the owner" item 5, and the
  series start rule ("Nothing in those plans may start until this plan's
  Slice C audit table names the drift row that justifies it"). Completed;
  context only.
- `docs/plans/2026-10-30-pr48-windows-root-cause-analysis.md`, "Accepted
  hypothesis, action, and reevaluation triggers (2026-11-01)". This is the
  accepted E1 storage-latency hypothesis. Its trigger table refuses any
  proposal to raise a Windows deadline or lower the Windows worker count
  that cites CI stalls, and it refuses relaxing `SYNC_MODE`. Completed. Binding on Task 2.
- `docs/plans/2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md`
  (draft, Class 5). Owns deleting `WEFT_MANAGER_REUSE_ENABLED`, including the
  `env["WEFT_MANAGER_REUSE_ENABLED"] = "1"` lines in the same
  `tests/cli/test_cli_run.py` tests Task 2 touches. Still intended. Not
  executed here.
- `docs/plans/2026-09-14-xdist-title-contention-plan.md` (draft). Records
  the macOS title boundary that [TS-0] now states. Context for dropped
  finding X2.
- Guidance: `skills/test-audit/SKILL.md` (authoring gate, negative
  assertions, junk patterns, retention bar, candidate evidence, sensitivity
  check, validation); `docs/agent-context/runbooks/testing-patterns.md`
  Pattern 1, Pattern 11, Pattern 12; `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`;
  `skills/call-agent/SKILL.md`; `docs/lessons.md` "2026-10-31: Assert on what
  the contract orders, not on what happens to be alive".

## 3. Findings: held and dropped

Each finding was re-derived from the tree at `b7eed9f9`. The commands are in
the Execution log.

### Held

| ID | Category | Finding | Evidence | Task |
|---|---|---|---|---|
| R1 | Release tests | Group (ii) copied-configuration assertions inside behavioral tests (Ruling D1) | `tests/system/test_release_script.py` `test_build_precheck_commands_cover_release_gate_and_quality_gates`, `test_build_precheck_env_overrides_sets_worker_count`, `test_build_postupdate_steps_build_all_publishable_packages`; `bin/release.py` `PRECHECK_ENV_OVERRIDES` (:264), `build_postupdate_steps` (:591) | 1 |
| R2 | Release tests | Group (iii) identifier-dependent workflow checks (Ruling D1): workflow job ids (`publish-release`, `verify-main-test-workflow`, `publish-to-pypi`, `github-release`), a step `env` key (`EXPECTED_SHA`), and variables declared in the `actions/github-script` body (`expectedSha`, `latest`) are matched as strings, and whole `with` blocks, gate file names, and the asset list are compared to literal copies of the workflow files | `tests/system/test_release_script.py` `test_release_gate_routes_each_tag_to_exactly_one_package` (:990), `test_publish_checks_completed_main_push_once_without_polling` (:1042); `tests/system/test_release_workflow.py` `test_github_release_uploads_only_python_distributions` (:13); `.github/workflows/release.yml`, `.github/workflows/release-gate*.yml` | 1 |
| L1 | Load scaling | Platform-conditional `iterations` and `max_workers` (D2) | `tests/cli/test_cli_run.py:3002-3004`, `:3043-3045`; commit `c4915b04` | 2 |
| T1 | Timing | `time.sleep(0.2); assert process.poll() is None`, commented "The duration is the test invariant; do not shorten it." | `tests/cli/test_cli_serve.py:685-689` in `test_serve_forces_no_idle_timeout` | 3 |
| T2 | Timing | `time.sleep(0.05); assert publisher_finished.is_set() is False` | `tests/tasks/test_task_execution.py:4336-4337` in `test_base_task_worker_result_queue_backpressures_when_full` | 3 |
| T3 | Timing | `time.sleep(0.2)` then asserts a removed queue was not handled and the wake count did not move | `tests/tasks/test_multiqueue_watcher.py:2482-2485` in `test_postgres_dynamic_membership_rebinds_native_waiter` (PostgreSQL only; skips on SQLite) | 3 |
| T4 | Timing | "Blocked until released" claims checked with `not X.wait(short)` while the blocked party has no entry witness. `stop_started` is set before `task.stop()` is called, so it does not prove `stop()` reached its wait. | `tests/tasks/test_task_execution.py:332` (`test_base_task_process_entry_publishes_turn_active_with_owner_claim`), `:404` (`test_base_task_run_loop_entry_publishes_active_with_owner_claim`), `:1144` (`test_base_task_stop_waits_for_starting_interlock`); `tests/tasks/test_multiqueue_watcher.py:1506` (`test_background_mutation_committed_before_stop_returns_success`); `tests/core/test_manager.py:6769` (`test_manager_cleanup_waits_for_active_child_launch_worker`) | 4 |
| T5 | Timing | Patches the shared stdlib `time.monotonic` with a two-value iterator. With progress heartbeats on, the worker's heartbeat thread also calls `time.monotonic()` (`tests/conftest.py:615`). The test is not a duplicate. It calls the module's local `drive_task_until` wrapper, which forwards `pending_work=(task._has_pending_worker_results,)`, while `tests/system/test_reactor_driver.py::test_drive_until_applies_one_ready_result_after_the_deadline` calls `drive_until` directly. Only this test fails if the wrapper drops that argument. The keeper shows the right clock seam: it patches the driver module's own `time` binding. | `tests/tasks/test_task_observability.py:151` in `test_drive_task_until_applies_ready_result_after_wall_deadline` | 5 |
| C1 | Implementation coupling | Three Manager tests assign private watcher cursors (`_active_queues`, `_queue_iterator`, `_next_inactive_probe_at`, `_pending_messages_precheck_confirmed`). Any refactor of the drain loop breaks them. One of them has no assertion of its own: the claim lives inside a patched probe that raises `AssertionError`. | `tests/core/test_manager.py` `test_manager_does_not_probe_inactive_public_spawn_queue` (:9057), `test_manager_pending_precheck_activates_public_spawn_queue` (:9105), `test_manager_idle_discovery_skips_reserved_queues` (:9153); owner fields at `weft/core/tasks/multiqueue_watcher.py:224-234` | 6 |
| H2 | Orphaned managers | Harness teardown discards two failure signals. `manager_runtime.stop_manager(...)` returns `tuple[bool, str \| None]` (`weft/core/manager_runtime.py:2378-2387`), and the harness ignores it (`tests/helpers/weft_harness.py:1206`). `_wait_for_registered_pids_to_exit()` returns the live survivors, and the harness ignores that list too (`:779`, `:1217`). Owner PIDs are never force-terminated, by design (`_terminate_registered_pids`, `:1321-1329`; held by `tests/test_harness_registration.py::test_harness_force_termination_targets_managed_pids_only`). So a failed force-stop, for example of a `manager serve` process, whose idle timeout is forced to `0.0` at `weft/core/manager_runtime.py:2313`, leaves a live manager with no report while teardown proceeds. This mechanism is inferred from the code; no orphan was reproduced. | as cited | 8 |
| H3 | Harness duplication | Manual `harness.__enter__()` and `cleanup()` per iteration, plus private `_closed` and `_tempdir` access and a swallowed `PermissionError`. This bypasses `WeftTestHarness.__exit__` (`tests/helpers/weft_harness.py:176-189`), which attaches failure diagnostics through `_attach_failure_diagnostics` (`:191`). | `tests/cli/test_cli_run.py:3014-3035` in `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap` | 9 |
| H4 | Harness env | `WEFT_TEST_MODE=1` is injected (`tests/helpers/weft_harness.py:825`) but read by no product or test path; it is only asserted as an env-restore probe (`tests/test_harness_registration.py:154`, `:1612`, `:1638`). | grep count 0 under `weft/` | 10 |
| H5 | Harness neutralizes default | The harness exports `WEFT_TASK_MONITOR_ENABLED=0` and `WEFT_LIVENESS_MONITOR_ENABLED=0` (`tests/helpers/weft_harness.py:828-829`). The production defaults are `True` (`weft/_constants.py:1074`, `:1077`; read at `weft/core/manager.py:694-699`). Exactly one test under `tests/cli` turns TaskMonitor back on (`tests/cli/test_cli_serve.py:550`). None turns LivenessMonitor on through the CLI. Pattern 11. | as cited | 10 |

### Dropped or not tasked

| ID | Deep-dive claim | Why dropped |
|---|---|---|
| S1 | Test-only production seams | Holds only for `WEFT_MANAGER_REUSE_ENABLED` (`weft/_constants.py:1827`, `:2786`; no reader elsewhere under `weft/`, `integrations/`, `extensions/`; 11 test references). Deleting it is owned by the Class-5 plan `2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md`, because it removes a Quick Reference row and makes the retired key fail fast. Not duplicated here. No other test-awareness token appears under `weft/` (count 0). |
| X1 | Local xdist worker cap | The 2026-09-10 interim cap of 4 (`dc4fb4e1`) was raised to 8 the same day (`8a221e35`) and restored to 12 at `75f5a977`. `pyproject.toml:140` (`--maxprocesses 12`) matches `bin/pytest-worker-count` (`MAX_PYTEST_XDIST_WORKERS = 12`), which CI uses (`.github/workflows/test.yml:63`, `:264`). The reconciliation audit already ruled this `aligned`. |
| X2 | macOS process-title cost | Resolved and owned: [TS-0] states the infrastructure-only boundary (`tests/helpers/xdist_titles.py`, inline harness managers disable their own titles), and `docs/lessons.md` records it. No live structural cause remains in the tree. |
| N1 | Other `wait(...)`-is-false sites | Retained. `tests/core/test_queue_wait.py:531` and `:656`: the wait primitive is the subject, and its timeout return is its contract. `tests/core/test_manager_family_maintenance.py:1205`, `:1238`, `:1430`: non-blocking `wait(0)` state checks at clock-controlled points. `tests/tasks/test_task_execution.py:672`: idle entry is witnessed with `timeout is None` before the window. `tests/core/test_manager.py:4088` and `tests/core/test_manager_burst_bookkeeping.py:567`: adapter re-notification claims. Not classified here; Task 4's rule applies if they are touched. |
| N2 | Copy-pasted `TaskRunner` fakes | Holds (9 `monkeypatch.setattr(consumer_module, "TaskRunner", ...)` sites in `tests/tasks/test_task_execution.py`), but each proves a distinct Consumer sequencing claim. The only remedy is a new shared fake fixture, which this plan's scope forbids. |
| N3 | Harness re-derives production readers | Holds factually (`_latest_tid_state_payloads`, `_list_active_manager_records`, against `weft/core/task_state.py:92` and `weft/core/manager_runtime.py:1080`). Not tasked: `list_manager_records` defaults to `prune_stale=True` (writes), and the harness reads are deliberately best-effort (docstring at `tests/helpers/weft_harness.py:886-892`). Swapping them would change teardown semantics without a demonstrated failure. |
| H1 | Harness terminal-event set diverges from the canonical mapping | Holds: the local 6-name `TERMINAL_TASK_EVENTS` (`tests/helpers/weft_harness.py:72-79`) omits `control_stop` and `control_kill` from the canonical 8-name mapping (`:40-42`; `weft/_constants.py:1702-1711`), so `_live_task_tids_from_mappings` (`:1001`) and `_cleanup_candidate_task_tids` (`:1025`) treat control-cancelled tasks as live. Dropped after cross-family round 1 (F4). The fix is not reporting-only: it changes teardown's STOP and KILL fan-out in `_stop_active_managers` (`:1183`) and lingering-task handling on the preserve-database path (`:1250`). That fires the cleanup-lifecycle risky trigger. A selector test would prove only that suppression changed, not that cleanup still settles; logical termination is not process exit. It needs its own plan that proves cleanup is preserved. |
| N4 | Live orphan managers on the host | The deep dive saw three. `ps` at authoring time showed none. Only the mechanism (H2) is carried. |

## 4. Context and Key Files

Read first, in this order:

1. `skills/test-audit/SKILL.md`, all of it (audit mode).
2. `docs/specifications/08-Testing_Strategy.md` [TS-0] and [TS-4].
3. `docs/agent-context/runbooks/testing-patterns.md` Pattern 1, 11, 12.
4. `tests/helpers/weft_harness.py`: `_patch_environment` (:823), `cleanup`
   (:760), `_stop_active_managers` (:1163), `_terminate_registered_pids`
   (:1321), `_wait_for_registered_pids_to_exit` (:1342).
5. `tests/test_harness_registration.py`: the harness's own suite. Reusable
   test infrastructure may have its own tests when it is the subject.

Current structure:

- `run_cli()` (`tests/conftest.py:412`, env copy at `:423`) and `_popen_cli`
  (`tests/cli/test_cli_serve.py:44`) copy `os.environ`, so every harness
  override reaches CLI child processes.
- `serve_manager_foreground` (`weft/core/manager_runtime.py:2301`) builds
  the Manager spec with `idle_timeout_override=0.0`. Manager init reads
  `metadata["idle_timeout"]` first (`weft/core/manager.py:649-657`), and
  the idle path returns at once when it is `<= 0` (`:8459`).
- `BaseTask._publish_worker_result` (`weft/core/tasks/base.py:796-816`)
  loops on `self._worker_result_queue.put(result, timeout=...)` and catches
  `thread_queue.Full`. That bounded stdlib queue is the dependency a
  backpressure witness may observe.
- `drive_until` (`tests/helpers/reactor_driver.py`) reads `time.monotonic`
  from its own module binding. `tests/system/test_reactor_driver.py`
  patches that binding through `SimpleNamespace(monotonic=...)`.

Shared paths. Do not duplicate:

- `drive_until` for reactor stepping; `WeftTestHarness` as a context manager
  for lifecycle; the canonical `TERMINAL_TASK_EVENTS` mapping in
  `weft/_constants.py`.

Comprehension questions. Answer them in the Execution log before the first
edit. A wrong answer means stop and re-read.

1. *If Task 2's restored load turns `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
   red on macOS, what may the implementer do, and what may they not do?*
   Expected: stop corrective work and acceptance reruns, and preserve the
   command, backend, worker count, and failure artifacts ([TS-0] contention
   stop, [ALT-WT-017], Pattern 1). State the convergence and lifetime
   behavior the theory predicts, and locate the causal flaw in the product,
   harness, oracle, or environment. A lower-load run is a labelled
   diagnostic only. Never restore the platform reduction or pad the timeout
   to obtain green. On Windows, the 2026-11-01 trigger table also refuses
   raising a deadline or lowering the worker count when the proposal cites
   CI stalls.
2. *Under Ruling D1, `test_publish_checks_completed_main_push_once_without_polling`
   still inspects the `actions/github-script` body as text. Why may it check
   for loop and timer constructs and for GitHub REST parameter names, but
   not for `expectedSha` or `latest`?* Expected: the retention bar keeps
   source inspection "when it is the cheapest independent guard" and the
   check survives an identifier-only refactor. The gate's JavaScript runs
   only in GitHub Actions, so text inspection is the cheapest guard of the
   [TS-4] clauses (one lookup of a completed Test push run, no polling).
   `expectedSha` and `latest` are names the script author chose; renaming
   them changes no behavior, so a check on them is exactly what the owner's
   criterion calls wrong. Loop keywords, `setTimeout` and `setInterval`, and
   the REST parameters `workflow_id`, `event`, and `head_sha` are defined by
   JavaScript and by the GitHub API, not by the script, so a refactor of the
   script cannot rename them. The same criterion removes the exact dict
   equality in `test_build_precheck_env_overrides_sets_worker_count`: it
   fails when anyone adds an unrelated key, and the forwarding claim has a
   narrower assertion.

## 5. Invariants and Constraints

- No production code change. If a task needs one, stop and re-classify.
- No new fixtures or helper modules, except Task 2's
  `tests/helpers/runner_calibration.py` and its one session fixture,
  `runner_calibration`, in `tests/conftest.py` (Ruling D2 addendum). Only
  Task 9 changes harness usage, and it uses the existing context manager.
- No change to `pyproject.toml` addopts, `bin/pytest-worker-count`, xdist
  worker counts, `SYNC_MODE`, or any per-platform timeout budget. The one
  exception is the Task 2 timeout branches, and only as Ruling D2 and its
  addendum direct: the macOS branch is removed, and the Windows branch
  becomes the Linux budget times the session's calibrated
  `windows_time_factor` (floored at 1.0, capped at
  `RUNNER_TIME_FACTOR_CEILING`, pinned to 1.0 off Windows). No other
  timeout, in this file or elsewhere (`DEFAULT_CLI_SUBPROCESS_TIMEOUT`,
  `WINDOWS_TEMP_DIR_CLEANUP_TIMEOUT_SECONDS`), goes through the factor. On
  Windows, the 2026-11-01 trigger table still binds.
- Do not touch the `WEFT_MANAGER_REUSE_ENABLED` lines. The 2026-09-08 plan
  owns them. If that plan lands first, rebase Task 2 and Task 9 onto it. If
  this plan lands first, leave those lines byte-identical.
- No mocks for queues, process lifecycle, reservations, or result delivery.
  A witness may wrap a dependency, such as a stdlib `queue.Queue.put` or a
  `threading` primitive. It must never replace the operation owner under
  test.
- No retries, `xfail`, skips, or relaxed limits added to obtain green.
- Every rewritten or retargeted test passes the sensitivity check (test-audit
  skill): one focused mutation of the production owner turns it red for the
  intended reason. Restore exactly, verify `git diff` is unchanged, and
  record the control and its failure in the Execution log. (Task 1's
  controls run in a disposable checkout instead; see Task 1.)
- Do not edit source or tests while a test run is in flight in the same
  checkout.
- No commits on the user's behalf unless asked. Stage by explicit file list.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [TS-0] (Task 2 Step 4) | Linux reference baselines measured on `ubuntu-latest` dispatch runs before the stress tests use the factor; Stop-if a baseline would land as `None` | Baselines committed as `None  # unmeasured`; factor 1.0 on every platform until measured; procedure recorded in the Execution log | The implementing session cannot push or dispatch workflows; the series brief directs landing Steps 1, 2, 3, 5, 6 with `None` baselines. Windows budgets equal the Linux budgets until the owner measures | none |
| [TS-0] (Task 8) | Harness teardown logs unconfirmed stops and pre-escalation survivors | Not landed; stopped at the Task 8 Gate | The full fast suite emitted the warning from about 20 tests outside the injected-failure tests; the Gate makes that a stop, and the warning is not to be suppressed | none |
| [TS-0] (Task 3, T3) | Rewrite only with a positive exclusion proof | Retained unchanged, reported | No public subscription surface on the native waiter at this seam | none |
| none (Task 9) | `WEFT_MANAGER_REUSE_ENABLED` lines byte-identical | Same content, one indent deeper inside the `with` block | The env copy must run inside the harness scope, which the context manager makes a block | none |
| none (Tasks 3 to 6) | Separate commits per task | One commit for Tasks 3 to 6 | Tasks 3 and 4 share `tests/tasks/test_task_execution.py` hunks and the watcher test imports the shared witness from it; non-interactive staging cannot split them | none |

## Spec Baseline

- `b7eed9f9`: `docs/specifications/08-Testing_Strategy.md` and
  `docs/program-theory.md` (adopted) at plan authoring time. Plan type:
  implementation against the existing spec. D1 was ruled reading B
  (2026-11-03), so no `## Proposed Spec Delta` is needed and the [TS-4]
  `Verification:` sentence stays as written.

## 6. Tasks

Task 0 gates everything else. Tasks 1 to 10 are otherwise independent and
may land as separate commits in any order. Tasks that share a file must
not be in flight together: Tasks 2 and 9 (`tests/cli/test_cli_run.py`), and
Tasks 8 and 10 (`tests/helpers/weft_harness.py`,
`tests/test_harness_registration.py`). Task 7 was dropped (F4); its number
is kept so earlier review references stay valid.

0. **Record rulings and answers.**
   - Outcome: D1, D1a (if D1 = B), D2, and D3 are filled in above with owner and date. The
     comprehension answers are in the Execution log.
   - Stop if: any ruling is still `pending`. Do not start Tasks 1 to 10.
   - D1 and D1a are ruled (2026-11-03). D1 is reading B, so the reading-A
     branch (Class 5, `## Proposed Spec Delta`) does not apply. D2 is
     ruled `drifted` and D3 `authorized` (owner, 2026-11-03). The D2
     addendum (owner, 2026-11-03) makes the Windows time factor a
     calibrated runner ratio; Task 2 measures its Linux reference baselines
     before the stress tests use it.

1. **(Ruling D1) Narrow the six release tests to the properties they enforce.**
   - Authority: Ruling D1 and Ruling D1a (owner, 2026-11-03). Every
     assertion that restates `bin/release.py` or a workflow file, or that
     breaks on an identifier-only refactor or a routine action upgrade,
     goes. The property it stood in for stays, checked another way, or the
     loss is named below.
   - Files: `tests/system/test_release_script.py` and
     `tests/system/test_release_workflow.py` only. No production or workflow
     file changes in the working checkout. The sensitivity controls below
     mutate `bin/release.py` or a workflow file only in a disposable
     checkout.
   - Shared rules for all six rewrites:
     - The repository root is `Path(__file__).resolve().parents[2]`, not
       `release.PROJECT_ROOT` or any other production directory constant.
     - A workflow's trigger block is `workflow.get("on", workflow.get(True))`.
       PyYAML parses the bare key `on` as the boolean `True`.
     - Release-gate workflows are found by content, not by file name: they
       are the files in `.github/workflows/` whose trigger has `push.tags`.
     - Jobs and steps in `.github/workflows/release.yml` are found by
       content, not by job id. The run-check step is an
       `actions/github-script@` step whose `with.script` calls
       `listWorkflowRuns(`, the workflow-run lookup; the run-check job is the
       job that holds it. Selecting by the lookup operation, not by "any
       `github-script` step", keeps a second, unrelated `github-script` step
       from making the selection ambiguous. At least one run-check step must
       exist, and every run-check step must pass the publish checks. The
       PyPI publish job is the only job
       whose `permissions` grant `id-token: write` (trusted publishing
       needs the identity token). The release-asset job is the job with a
       step whose `uses` starts with `softprops/action-gh-release@`. An
       action upgrade changes only the text after `@`, so these prefixes
       survive it.
     - `.github/workflows/release.yml` and `.github/workflows/test.yml` may
       be named by path: [TS-4] names both files.
     - No string literal in the six tests may contain a job id, a step `env`
       key, or a variable declared in the `actions/github-script` body. No
       expected value may be a set, list, tuple, or dict display of two or
       more literals, except a tuple passed directly to `str.endswith` or
       `str.startswith`. No assertion may index a command or step list by
       position. The lint aid in Verify flags candidate violations of all
       three; it does not prove their absence.

   Group (ii), in `tests/system/test_release_script.py`:

   - `test_build_precheck_commands_cover_release_gate_and_quality_gates`:
     - Keeps: the lookups that prove each gate command is present (SQLite
       pytest, `bin/pytest-pg`, `ruff check`, `ruff format`, `mypy`,
       `bin/pytest-live-providers`); `-m ""` on the SQLite command (all
       markers, so slow tests run: [TS-4] "every SQLite test including the
       slow marker"); `--all` on the PostgreSQL command; `--check` on the
       format command.
     - Replaces the exact extras-set equality with a relation to two other
       files. For every release target other than the root target
       (`release.RELEASE_TARGETS`, with a non-`None` `tag_namespace`), its
       `package_name` must be a requirement of at least one extra that the
       command passes with `--extra`, read from `pyproject.toml`
       `[project.optional-dependencies]` through `tomllib`. The requirement
       name is the leading run of name characters, for example
       `re.match(r"[A-Za-z0-9._-]+", requirement).group()` (a tuple of
       separators or an `[0]` index would trip the lint aid).
       A dropped extra that would leave a first-party package uninstalled
       fails; an added extra breaks nothing.
     - Replaces the literal quality-target and mypy-target sets with the
       same relation. The `ruff check` and `ruff format` targets must
       include `weft`, `tests`, and every non-root release target's
       `package_dir` relative to the repository root. The `mypy` targets
       must include `weft`, `tests`, `bin` ([TS-3]: mypy and ruff cover
       `tests/` and `bin/`), and, for every non-root target,
       `<package_dir>/<package_dir name>` (for example
       `integrations/weft_django/weft_django`). Check each core name with
       its own `in` assertion, not a literal set.
     - Replaces the exact `PRECHECK_ENV_OVERRIDES ==` equality with two
       contract checks. Fail-fast: the tokens of the `PYTEST_ADDOPTS` value
       the precheck pytest invocations run with
       (`release.build_precheck_env_overrides()["PYTEST_ADDOPTS"].split()`,
       with `_pytest_worker_count` patched as in the worker-count test; that
       env is what `bin/release.py:1428` and `:1490` pass to the precheck
       commands) include a fail-fast form, `-x`, `--exitfirst`, or
       `--maxfail=1`. Any one suffices, so keeping only `--maxfail=1`
       passes. Live providers off: each of
       `PRECHECK_ENV_OVERRIDES["WEFT_RUN_LIVE_PROVIDER_CLI_TESTS"]` and
       `PRECHECK_ENV_OVERRIDES["WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS"]` is
       `"0"` (live provider suites run only through their own command).
       These key names are the env interface that
       `tests/tasks/test_agent_execution.py:913–917`
       (`_require_live_provider_executable`) reads and that
       `bin/pytest-live-providers:20–21` names, not script-local names.
     - Drops: `command[:2] == ("uv", "run")` on the gate commands and on the
       live-provider command, `"python"` and `"dev"` in the live-provider
       command, and `--config-file pyproject.toml` on mypy. Each restates a
       command tuple and breaks on a routine runner or config-file change.
       Guards lost: a gate command that runs outside the uv project
       environment (the environment side stays held by
       `test_merge_command_env_drops_non_project_virtualenv`), and a mypy
       run that ignores the repository config. Neither failure publishes a
       wrong artifact.
     - Drops the exact `pytest_targets ==` set. It duplicates
       `test_build_precheck_commands_skip_extension_tests_when_unavailable`
       (same arguments, same assertion), which is group (i) and stays, so no
       guard is lost.
     - Drops the `WEFT_EAGER_FAILURE_TRACEBACK` value. It is a diagnostic
       setting, not a correctness property.
   - `test_build_precheck_env_overrides_sets_worker_count`: assert
     `result["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "17"` (the patched helper
     value; the forwarding check is behavioral and stays) and
     `release.PRECHECK_ENV_OVERRIDES.items() <= result.items()` (the base
     overrides are included). Drop the literal copy of the whole dict.
   - `test_build_postupdate_steps_build_all_publishable_packages`: build
     steps are the steps whose `command` contains `"build"`. Assert that the
     set of their resolved `cwd`s equals the set of `package_dir` values the
     release-gate workflows pass to `.github/workflows/release.yml`,
     resolved against the repository root. That is an independent production
     artifact. [TS-4] requires suites and packages to be added to the
     release-gate workflows and the release helper "atomically", and this
     check fails when they diverge. Drop the `steps[0]` to `steps[5]` index
     checks, the literal `pytest tests/system/test_constants.py` step, and
     the production directory constants used as the oracle. Per Ruling D1a,
     assert no order among the post-update steps.

   Group (iii). Disposition of each assertion:

   | Test | Assertion today | Disposition | How the kept property is checked |
   |---|---|---|---|
   | `test_release_gate_routes_each_tag_to_exactly_one_package` | The five gate file names equal a literal `expected` dict's keys | Goes (copies the workflow directory listing) | The set of tag-triggered workflow paths equals the set of `ROOT / target.release_gate_workflow` for every target in `release.RELEASE_TARGETS`. A gate with no helper target, or a target with no gate, fails. |
   | same | `workflow[True]["push"]["tags"] == [tag]` against a literal tag | Goes (copies the trigger) | Routing over the supported tag language, not over a sample tag. (1) Supported patterns: every entry of each gate's `push.tags` has the form `<literal>*`, one trailing `*` and no other filter metacharacter (`*`, `?`, `+`, `[`, `]`, `!`) in the literal prefix. Anything else, including `**`, fails the test, so an unsupported pattern cannot pass silently. Under GitHub filter semantics (`*` matches any characters except `/`) such a pattern matches exactly the tags that begin with its literal prefix and contain no `/` after it. (2) Coverage: a target's tag namespace is `target.tag_name("")` (`v` for the root target, `<tag_namespace>/v` otherwise; a version contains no `/`). A gate pattern covers a target when the namespace begins with the pattern's literal prefix and the rest of the namespace after that prefix contains no `/`. Each target must be covered by exactly one gate, and that gate must be the target's `release_gate_workflow`; each gate must cover exactly one target. (3) Exclusivity: no literal prefix of one gate is a prefix of, or equal to, a literal prefix of another gate. Then no tag of any version can match two gates. Readable sanity case only, not the proof: `target.tag_name("1.2.3")` matches its own gate's patterns and no other gate's. |
   | same | `set(workflow["jobs"]) == {"publish-release"}` | Goes (names a job id) | The publishing job exists and is reached: the gate has a job whose `uses` starts with `./` and names an existing workflow file whose trigger has `workflow_call`, and that job has no `if:` condition, so every matching tag reaches it. Other jobs in the gate are not restricted. |
   | same | `publish["uses"] == "./.github/workflows/release.yml"` | Goes (copies the step text) | Every gate's publishing job has the same `uses` value, it starts with `./`, and the file it names exists. [TS-4]: each gate "calls the sole package build/publish workflow". |
   | same | `publish["with"] == {...}`, a literal copy of six inputs | Goes (copies the block and names the inputs) | Identifier-free relations to the matched target: `target.package_name` is one of the `with` values and no other target's `package_name` is; exactly one `with` value, resolved against the repository root, equals `target.package_dir`; and the release-enabled flag equals `target.github_release_enabled`. The flag is the input the reusable workflow consumes as its release-enabled switch: the input named by the `${{ inputs.<name> }}` expression in the release-asset job's `if:`, read from the parsed YAML and located by that name in the reusable workflow's `on.workflow_call.inputs` block, not chosen by type. The gate's `with` value for `<name>`, or that input's declared `default` when the gate omits it, must equal `target.github_release_enabled`. Other boolean inputs are not checked. The tagged-commit relation moves to the publish-check rows below. |
   | same | `with` values `${{ github.ref_name }}` and `${{ github.sha }}` | Goes, not replaced | Only a copy of the expression text can check it. Guard lost: a gate that forwards the wrong expression as the tag to verify and attach. The commit identity is still checked (next test). |
   | same | `publish["permissions"]["actions"] == "read"` | Goes, not replaced | Copies a permission value. Guard lost: none for correctness. Without the permission the run check's API call fails, the step fails, and publication is blocked: a loud failure, not a wrong publication. |
   | `test_publish_checks_completed_main_push_once_without_polling` | `jobs["verify-main-test-workflow"]` lookup | Goes (job id) | The run-check step and job are found by the workflow-run lookup (shared rules). |
   | same | `env["EXPECTED_SHA"] == "${{ inputs.expected_tag_commit }}"` | Goes (step `env` key and input name) | Identifier-free relation, the first half of the commit data-flow check: the run-check step's `env` holds at least one entry whose value is a `${{ inputs.<name> }}` reference with `<name>` carrying the built commit, that is, every release gate passes the same `with` value for `<name>` as for the input that the build job's `actions/checkout@` step uses as `ref`. That entry's key, `<KEY>`, is taken from the parsed `env`, never written in the test. Other `env` entries are not restricted. |
   | same | `'workflow_id: "test.yml"' in script` | Narrowed | The script's `workflow_id:` value (regex `workflow_id:\s*["']([^"']+)["']`) equals the file name of `.github/workflows/test.yml`, and that workflow's top-level `name` is `Test` ([TS-4]: "The `Test` workflow is the sole automated test owner"). `workflow_id` is a GitHub REST parameter, not a script variable. |
   | same | `'event: "push"' in script` | Kept, quote-agnostic | Regex `\bevent:\s*["']push["']`. `event` is a REST parameter and `push` is the [TS-4] run type. |
   | same | `"head_sha: expectedSha" in script` | Replaced by a data-flow check | The second half of the commit data-flow check. In the run-check step's script, find the binding `(?:const\|let\|var)\s+([A-Za-z_$][\w$]*)\s*=\s*process\.env\.<KEY>\b`, with `<KEY>` (passed through `re.escape`) the env key from the row above, and require `\bhead_sha\s*:\s*<id>\b`, with `<id>` the identifier that binding captures. The test builds both regexes from parsed values, so neither the env key nor the variable name is written in it. The lookup is then filtered by the commit the gate passed in. `head_sha: context.sha` (control (j)) fails it. (The `\|` in this cell is Markdown table escaping; the regex alternation uses a plain `|`.) |
   | same | `'["main", "master"]' in script` | Narrowed | Regexes `["']main["']` and `["']master["']` each match ([TS-4]: "on `main` or `master`"). The exact array text, spacing, and quote style are not checked. |
   | same | `'latest.status !== "completed"'`, `'latest.conclusion !== "success"'` | Kept unchanged (owner, 2026-11-03: "Can we just keep it as is?") | The two substring assertions stay exactly as they are today. What they prove: each of the two comparisons is present with the inequality polarity, so flipping either `!==` to `===` (control (k)) or deleting either comparison fails the test. What they do not prove, stated so nobody relies on it: the Boolean connective between the two comparisons (an `&&` in place of the `||` passes both substrings while letting a completed, failed run publish; the owner's outside reviewer found this on 2026-11-03), and they name the script variable `latest`, so renaming it fails the test with no behavior change. The owner weighed the alternatives and kept the assertions as they are: the truth-table check proposed on 2026-11-03 reduced the JavaScript condition to a Python formula by balanced-parenthesis scans and `eval`, a small compiler inside a test, withdrawn; a behavioral run of the script under Node with the three Actions globals stubbed (probe recorded in the Execution log: the gate fails every canned API case except completed-and-successful, the `&&` mutant publishes a completed failure) was offered and declined because it adds Node as a test-time requirement for one test. The script has changed substantively twice in its history; a change to its condition is reviewed by eye for the connective. |
   | same | `"while (" not in script`, `"setTimeout" not in script` | Kept and narrowed to polling | No match for the regex `\bset(?:Timeout\|Interval)\s*\(` in the script, and no loop whose body contains the workflow-run lookup: for each `for (`, `while (`, or `do {` in the script, take the loop body (the `{...}` block by a balanced-brace scan, or the single statement up to `;`) and require that it does not contain `listWorkflowRuns`. Any other loop, for example a logging loop over the returned runs, is allowed. (The `\|` in this cell is Markdown table escaping; the regex alternation uses a plain `|`.) A polling loop needs a timer or a repeated lookup; none of these words is a script variable. |
   | same | `"verify-main-test-workflow" in jobs["publish-to-pypi"]["needs"]`, `"publish-to-pypi" in jobs["github-release"]["needs"]` | Narrowed | The transitive `needs` closure of the PyPI publish job contains the run-check job, and the transitive closure of the release-asset job contains the PyPI publish job. Jobs are found by content (shared rules); their ids are read from the parsed YAML, never written in the test. |
   | `test_github_release_uploads_only_python_distributions` (in `tests/system/test_release_workflow.py`) | `workflow["jobs"]["github-release"]` lookup | Goes (job id) | The release-asset job is found by its `softprops/action-gh-release@` step (shared rules). |
   | same | `set(files) == {"dist/*.tar.gz", "dist/*.whl"}` | Narrowed | Every entry of the step's `with.files` ends with `.whl` or `.tar.gz` (one `str.endswith` call with a tuple), and at least one entry ends with each. A `dist/*` glob or a sidecar suffix (`.sigstore.json`, `.sig`, `.crt`) fails; moving the dist directory does not. |
   | same | `"id-token" not in github_release_job["permissions"]` | Kept | Unchanged, applied to the job found by content. `id-token` is a GitHub permission name. |
   | same | `all("sigstore" not in step.get("uses", "") ...)` | Goes, not replaced | It names one signing action. The suffix check above stops this step from uploading sidecar files, and keyless signing needs the identity token the job does not have. Guard lost: a signing step in the same job that uses a stored key and uploads its own sidecar through a different upload step. |

   - Sensitivity: one control per rewritten claim. Every control runs in a
     disposable checkout, never in the working checkout: create it with
     `git worktree add --detach <scratch dir> HEAD` (copy the two edited
     test files into it if Task 1 is not committed yet), apply one mutation
     at a time there, run the focused test there, record the failure, and
     discard the mutation before the next. Remove the checkout afterward
     with `git worktree remove --force <scratch dir>`.
     (a) Delete one `CommandStep(("uv", "build"), cwd=...)` from
     `bin/release.py::build_postupdate_steps`; the build-set test fails.
     (b) Flip `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS` to `"1"` in
     `PRECHECK_ENV_OVERRIDES`; the precheck test fails.
     (c) In `bin/release.py::build_precheck_env_overrides` (:715), replace
     `_pytest_worker_count()` with a fixed string such as `"4"`; the
     worker-count test fails because the patched `"17"` is no longer
     forwarded. Controls (a) and (b) do not exercise that forwarding claim.
     (d) Delete the `"--extra", "django"` pair from the PostgreSQL precheck
     command in `bin/release.py`; the precheck test's extras relation fails.
     (e) Delete `"extensions/weft_docker"` from the `ruff check` precheck
     command; the quality-target relation fails.
     (f) Change the tag pattern in `.github/workflows/release-gate-docker.yml`
     to `weft_django/v*`; the routing test fails because the Django tag
     matches two gates and the Docker tag matches none.
     (g) Change `expected_tag_commit` in one release-gate workflow to
     `${{ github.event.before }}`; the publish-check test's commit relation
     fails.
     (h) Insert `await new Promise((resolve) => setTimeout(resolve, 1000));`
     into the `actions/github-script` body; the no-poll check fails.
   - (n) Not a control. Changing the failure condition's `||` to `&&` in
     `release.yml` passes the kept substring assertions; it is the named
     blind spot of the unchanged test (owner, 2026-11-03), recorded so the
     controls are not read as proving the connective.
     (i) Add a `dist/*` line to the release-asset step's `files`; the asset
     test fails.
     (j) In the run-check script, change `head_sha: expectedSha` to
     `head_sha: context.sha`; the commit data-flow check fails.
     (k) In the run-check script, flip `!==` to `===` in the `.status`
     comparison, and, as a second run, in the `.conclusion` comparison;
     the unchanged substring assertions fail in both runs.
     (l) Add `weft_django/v2*` to the tag patterns of
     `.github/workflows/release-gate-docker.yml`; the routing test's
     exclusivity check fails, because that prefix extends Django's
     `weft_django/v`.
     (m) Change the root gate's pattern in `.github/workflows/release-gate.yml`
     from `v*` to `v1*`; the routing test's coverage check fails for the
     core target's namespace `v`.
   - Verify, lint aid, not proof (one-time, not a standing gate): from the
     repository root, after `. ./.envrc`, run the command below once before
     the edits (it exits 1; at `b7eed9f9` it reported 44 findings) and once
     after them. Record both outputs in the Execution log, and after the
     edits fix each finding or record it there as a false positive with its
     reason. The command flags four syntactic patterns. It has false
     positives (a one-entry literal dictionary is flagged) and false
     negatives (a copied scalar such as `expected == "weft-docker"`, a
     variable index, an unhyphenated job id such as `build`), so it proves
     nothing about the acceptance sentence. Task 1's acceptance is the
     per-assertion disposition table plus controls (a) to (m), verified by
     reading the diff and running the controls (Ruling D1, consequence 5).
     Do not add the command to CI, to `bin/`, to the test tree, or to the
     final gates; adding it as a standing check would fire the "Class 3+P"
     escalator.

```bash
./.venv/bin/python - <<'PY'
import ast, re, sys
from pathlib import Path
import yaml

TARGETS = {
    "tests/system/test_release_script.py": {
        "test_build_precheck_commands_cover_release_gate_and_quality_gates",
        "test_build_precheck_env_overrides_sets_worker_count",
        "test_build_postupdate_steps_build_all_publishable_packages",
        "test_release_gate_routes_each_tag_to_exactly_one_package",
        "test_publish_checks_completed_main_push_once_without_polling",
    },
    "tests/system/test_release_workflow.py": {
        "test_github_release_uploads_only_python_distributions",
    },
}
deny: set[str] = set()
for wf in Path(".github/workflows").glob("release*.yml"):
    doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
    for job_id, job in doc.get("jobs", {}).items():
        if "-" in job_id:
            deny.add(job_id)
        for step in job.get("steps", []):
            deny.update(step.get("env", {}) or {})
            script = (step.get("with") or {}).get("script", "")
            deny.update(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)", script))
words = [(n, re.compile(rf"(?<![\w$-]){re.escape(n)}(?![\w$-])")) for n in sorted(deny)]

def leaves(node: ast.AST) -> list[ast.AST] | None:
    if isinstance(node, ast.Constant):
        return [node]
    kids = (node.elts if isinstance(node, (ast.Set, ast.List, ast.Tuple))
            else [*node.keys, *node.values] if isinstance(node, ast.Dict) else None)
    if kids is None:
        return None
    out: list[ast.AST] = []
    for kid in kids:
        sub = leaves(kid) if kid is not None else None
        if sub is None:
            return None
        out += sub
    return out

def literal_container(node: ast.AST) -> bool:
    found = leaves(node) if not isinstance(node, ast.Constant) else None
    return found is not None and len(found) >= 2

hits = []
for path, names in TARGETS.items():
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = set()
    for fn in tree.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name in names):
            continue
        found.add(fn.name)
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        for stmt in body:
            exempt = {id(arg) for node in ast.walk(stmt) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute)
                      and node.func.attr in ("endswith", "startswith") for arg in node.args}
            for node in ast.walk(stmt):
                if id(node) not in exempt and literal_container(node):
                    hits.append((path, node.lineno, fn.name, "R1 literal inventory"))
                if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                        and type(node.slice.value) is int
                        and not (isinstance(node.value, ast.Attribute) and node.value.attr == "parents")):
                    hits.append((path, node.lineno, fn.name, "R2 positional index"))
                if isinstance(node, ast.Attribute) and node.attr.endswith(("_DIR", "_ROOT")):
                    hits.append((path, node.lineno, fn.name, f"R3 production constant oracle {node.attr}"))
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for name, rx in words:
                        if rx.search(node.value):
                            hits.append((path, node.lineno, fn.name, f"R4 names workflow-local identifier {name!r}"))
    missing = names - found
    if missing:
        hits.append((path, 0, ",".join(sorted(missing)), "target test not found"))
for hit in hits:
    print("%s:%d %s: %s" % hit)
print(f"{len(hits)} finding(s); deny list: {sorted(deny)}")
sys.exit(1 if hits else 0)
PY
```

The check flags, inside the six tests (docstrings excluded): R1, a set,
list, tuple, or dict display of two or more literals (a copied
inventory), except a tuple passed directly to `str.endswith` or
`str.startswith`; R2, an integer subscript other than `.parents[n]`
(a positional index into a step or command list); R3, an attribute
ending in `_DIR` or `_ROOT` (a production directory constant used as
the oracle); R4, a string literal containing, as a whole word, a job id
with a hyphen, a step `env` key, or a variable declared with `const`,
`let`, or `var` in any `release*.yml` workflow. The deny list is read
from the workflow files when the command runs, so it is not itself a
copy. It also fails if any of the six tests is missing, so a rename
cannot make it pass vacuously.
   - Leave group (i) unchanged (Ruling D1, consequence 2, and the scope
     note under it).
   - Done when: `./.venv/bin/python -m pytest tests/system/test_release_script.py tests/system/test_release_workflow.py -q`
     passes; the diff has been read against the disposition table; controls
     (a) to (m) are recorded with their failures; and the lint aid's before
     and after outputs, with each after-edit finding dispositioned, are in
     the Execution log.

2. **(Ruling D2 and addendum) Run the two stress tests at full load on every
   platform; give Windows one calibrated time factor.**
   - Authority: Ruling D2 (owner, 2026-11-03), consequences 2 to 6, and the
     Ruling D2 addendum (owner, 2026-11-03, "Yes, let's try that for 9").
   - Files: `tests/cli/test_cli_run.py`, `tests/conftest.py`,
     `tests/helpers/runner_calibration.py` (new), and
     `tests/system/test_runner_calibration.py` (new).
   - Step 1, load. In
     `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
     set `iterations = 8` and `max_workers = 4` with no platform condition
     (lines 3003-3004 at `b7eed9f9`). In
     `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
     set `iterations = 5` and `max_workers = 4` (lines 3044-3045).
   - Step 2, predicate. Delete `constrained_parallelism` (lines 3002 and
     3043). Its only other uses are the three timeout choices in the same
     test (lines 3005-3011 and 3046-3052). On the measurement branch
     (Step 4) replace it there with a Windows-only predicate,
     `sys.platform == "win32"`, the form the module already uses at lines
     2768, 2851 and 2882; Step 5 deletes those interim branches in favour
     of the helper, which applies the same predicate. macOS now takes the
     Linux values: 60.0 s submit, 5.0 s status, 10.0 s task completion.
   - Step 3, helper and probe (RED first: write Step 6's tests before the
     symbols exist and record their `ImportError` or `AttributeError`
     failure). Add `tests/helpers/runner_calibration.py`, modelled on
     SimpleBroker's `performance_calibration.py` and
     `timing.scale_timeout_for_calibration` (cited in the addendum; not
     imported). It holds:
     - `RunnerCalibration`, a frozen dataclass with `spawn_median_s` and
       `sqlite_median_s` (floats, seconds) and `error` (`str | None`).
     - `probe_runner() -> RunnerCalibration`. Process creation: start one
       `multiprocessing.get_context("spawn").Process` whose target is a
       module-level function that returns immediately, time `start()`
       through `join(timeout=RUNNER_PROBE_JOIN_TIMEOUT_S)` with
       `time.perf_counter()`, repeat `RUNNER_PROBE_REPETITIONS` times (5),
       take the median. The join is bounded (`RUNNER_PROBE_JOIN_TIMEOUT_S =
       30.0`, a constant in the helper with a docstring saying why: the
       probe runs in `pytest_configure`, before the test protocol, so the
       installed pytest-timeout timer does not cover it, and an unbounded
       `join()` on a stalled child would hang every session before the
       error fallback or the ceiling could apply); if the child is still
       alive at the deadline, the probe terminates it (`terminate()`, then
       `join(5.0)`, then `kill()` if still alive), records the timeout as the
       `error`, and the factor is 1.0 with the header showing the error. The
       disk probe is bounded by the same rule through a wall-clock deadline
       around its five cycles. Disk I/O: in a
       fresh `tempfile.TemporaryDirectory()`, open SimpleBroker's
       `Queue("weft.calibration.probe", db_path=<dir>/probe.db,
       persistent=True)`, write one message, close it, and let the
       directory be deleted, timing the whole cycle; repeat 5 times, take
       the median. The `Queue` runs SimpleBroker's default
       `SYNC_MODE=FULL` (`simplebroker/_constants.py:450`), the same fsync
       path the tests use. If either probe raises, the result carries the
       exception text in `error` and the factor is 1.0, as SimpleBroker's
       `_machine_performance_ratio` returns a neutral 1.0 on failure; the
       header shows the error.
     - `REFERENCE_SPAWN_MEDIAN_S` and `REFERENCE_SQLITE_MEDIAN_S`
       (`Final[float | None]`), the Linux reference baselines. Until Step 4
       measures them they are `None` with the comment `# unmeasured`
       (SimpleBroker's `REFERENCE_BASELINES` carries measured values with
       the reference machine named in a comment; Weft commits the same
       once measured). Their docstring names the runner
       (`ubuntu-latest`, GitHub-hosted), the Python versions, the
       dispatch run IDs, and the date.
     - `RUNNER_TIME_FACTOR_CEILING: Final[float] = 20.0`, the upper end of
       the published process-creation gap; the owner may change it.
     - `windows_time_factor(calibration, *, platform: str = sys.platform)
       -> float`: 1.0 when `platform != "win32"`, whatever the probe says
       (Ruling D2: "MacOS should not get any breaks"); 1.0 when
       `calibration.error` is set or a reference baseline is `None`;
       otherwise `min(RUNNER_TIME_FACTOR_CEILING, max(1.0,
       spawn_median_s / REFERENCE_SPAWN_MEDIAN_S, sqlite_median_s /
       REFERENCE_SQLITE_MEDIAN_S))`. Each ratio is floored at 1.0, so the
       factor never tightens a budget, and the larger ratio wins. Its
       docstring states the owner's reason (process spawning and disk
       access are far slower on Windows; a legitimate platform difference,
       not a load reduction) and cites Ruling D2 and the addendum.
     - `scale_linux_budget(linux_budget: float, calibration, *, platform:
       str = sys.platform) -> float`, returning `linux_budget *
       windows_time_factor(calibration, platform=platform)`. It never
       changes load.
     The `platform` keyword is how Step 6 drives the Windows branch off
     Windows; monkeypatching `sys.platform` is not used.

     In `tests/conftest.py`, run the probe once per session. The conftest
     keeps session-wide values as module-level constants
     (`DEFAULT_CLI_SUBPROCESS_TIMEOUT`, line 55) and session state in
     `pytest_configure` (line 631). A module-level computation or a
     session fixture would run in every xdist worker, concurrently, which
     inflates both medians; so the probe runs in `pytest_configure` only
     in the controller (no `config.workerinput`), or in the single process
     when xdist is off, and stores the `RunnerCalibration` in
     `config.stash`. An `@pytest.hookimpl(optionalhook=True)`
     `pytest_configure_node(node)` copies its fields into
     `node.workerinput["weft_runner_calibration"]`; a worker's
     `pytest_configure` reads them back into its stash (the
     `optionalhook` pattern is already used in
     `tests/helpers/xdist_titles.py:49` and
     `tests/helpers/run_diagnostics.py:129`). The probe runs before the
     workers start, so it does not contend with them. A session fixture
     `runner_calibration` returns the stashed value.
     `pytest_report_header` prints one line in the controller:
     `weft runner calibration: spawn_median=<s>s sqlite_median=<s>s
     spawn_ratio=<r> sqlite_ratio=<r> factor=<f> platform=<sys.platform>
     <applied|pinned|capped|unmeasured|error: ...>`. On macOS and Linux
     the probe still runs and its numbers are logged with `pinned`, so a
     slow Linux or macOS runner is visible but not accommodated. When the
     uncapped factor exceeds the ceiling the line shows the uncapped value
     and `capped`, and the session uses the ceiling.
   - Step 4, measure the Linux reference baselines (test-audit: a platform
     budget correction must be measured). Commit Steps 1 to 3 on a
     measurement branch with both baselines `None`; the two stress tests
     keep today's Windows values (120.0 s, 10.0 s, 120.0 s), labelled in a
     comment as interim and unmeasured; they do not land. Pushing the
     branch alone runs nothing: the `Test` workflow's `push` trigger is
     restricted to `main` and `master` (`.github/workflows/test.yml:3-5`).
     `test.yml` declares `workflow_dispatch` (`:8`), so push the branch and
     dispatch the workflow on it three times with
     `gh workflow run test.yml --ref <branch>`. Each dispatch runs the `cli`
     slice on `ubuntu-latest`, `macos-latest`, and `windows-latest` for
     Python 3.12, 3.13, and 3.14. List each run's `cli` jobs with
     `gh run view <run-id> --json jobs --jq '.jobs[] | select(.name | endswith(", cli)")) | "\(.databaseId) \(.name) \(.startedAt) \(.completedAt)"'`,
     and read each job's header line with
     `gh run view <run-id> --log --job <job-id> | grep "weft runner calibration:"`.
     The baselines come from the jobs `test (ubuntu-latest, 3.12|3.14, cli)`;
     the Ubuntu 3.13 job runs with coverage and `-m ""`, so its probe is
     not comparable. Stall rule, in this plan: for each job (one OS and
     Python version), compare its wall time (`completedAt` minus
     `startedAt`) and each probe median across the three dispatches, and
     discard a dispatch's job whose wall time or probe median exceeds three
     times the median of the other two as a storage stall; it is not
     fitted. The whole-VM storage-stall phenomenon is documented in the
     2026-11-01 Windows record
     (`docs/plans/2026-10-30-pr48-windows-root-cause-analysis.md`); it is
     cited for the phenomenon, not for a script. A discarded job is read
     through that record's trigger table. If fewer than two dispatches of
     a job survive, dispatch again. Each reference baseline is the median
     of the surviving Ubuntu jobs' medians for that probe. Record every
     header line, every discard, and both baselines in the Execution log,
     with the macOS and Windows header lines for context.
   - Step 5, apply the factor. Commit the two baselines into
     `tests/helpers/runner_calibration.py` with their docstring (Step 3).
     In `tests/cli/test_cli_run.py`, add module-level Linux budgets
     `MANAGER_REUSE_SUBMIT_TIMEOUT_S = 60.0`,
     `MANAGER_REUSE_STATUS_TIMEOUT_S = 5.0`, and
     `MANAGER_REUSE_TASK_COMPLETION_TIMEOUT_S = 10.0`, and one function,
     `_manager_reuse_timeouts(calibration, *, platform: str =
     sys.platform) -> tuple[float, float, float]`, that returns each
     through `scale_linux_budget(..., platform=platform)` (the keyword is
     the explicit platform seam Step 6's tests use; production callers
     leave it at its default). Both stress tests take the
     `runner_calibration` fixture and set their three timeouts from
     `_manager_reuse_timeouts(runner_calibration)` only, with no other
     per-site Windows value; the interim `sys.platform == "win32"`
     branches from Step 2 are deleted. The two tests stop using
     `PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT`. That constant stays,
     because `test_cli_run_parallel_no_wait_adopts_active_manager` (line
     2984) still needs it for its Windows-or-PostgreSQL branch, and the
     factor does not reach PostgreSQL. Change its docstring's
     "Windows/macOS parallel manager reuse tests" to name that remaining
     user; no value changes. Do not pad Windows beyond the factor, and do
     not reduce Windows load.
   - Step 6, tests for the mechanism, in
     `tests/system/test_runner_calibration.py` (`pytestmark =
     pytest.mark.shared`, so the conftest backend-scope check at
     `tests/conftest.py:696` admits it). A constructed `RunnerCalibration`
     with chosen medians stands in for the measurement; it is the only
     stand-in, and nothing replaces the probe's subprocess or `Queue`
     work in the stress tests. RED where the symbol does not exist yet.
     - Off Windows: `windows_time_factor` is 1.0 for `platform="darwin"`
       and `platform="linux"` with medians 30 times the baselines. Catches
       a factor applied on macOS or Linux.
     - Floor: on `platform="win32"` with medians below the baselines, the
       factor is 1.0. Catches a factor below 1.0 (a budget tightened on a
       fast runner).
     - Larger ratio: on `win32` with a spawn ratio of 6 and a SQLite ratio
       of 3, the factor is 6, and with the ratios swapped it is still 6.
       Catches a factor that averages or takes one probe only.
     - Ceiling: on `win32` with a ratio of 50, the factor equals
       `RUNNER_TIME_FACTOR_CEILING`. Catches an uncapped factor.
     - Budgets: `_manager_reuse_timeouts(calibration, platform=...)`
       returns the three `MANAGER_REUSE_*_TIMEOUT_S` constants times
       `windows_time_factor(calibration, platform=...)` for `win32` and
       the constants unchanged for `darwin` and `linux`, read through the
       helper rather than re-derived from literals (the `platform=`
       keyword on the wrapper is what lets the three cases run on any
       runner).
     - Bounded probe: with the spawn target replaced by one that sleeps
       past `RUNNER_PROBE_JOIN_TIMEOUT_S` (patched to 0.5 s for the test),
       `probe_runner()` returns within a few seconds with `error` set, no
       child process left alive, and the factor is 1.0. Catches an
       unbounded `join()` (the test itself would hang; give it a pytest
       timeout) and a leaked child. Catches a budget the
       function computes off the helper (a literal Windows value or a
       macOS branch inside it).
     - Not caught by a test: a stress test that sets a timeout without
       calling `_manager_reuse_timeouts`. Asserting call sites would be a
       structural gate over the test tree (+P, see Out of Scope), so the
       Done-when grep below checks it instead.
     The tests take `platform=` explicitly and do not depend on the
     runner, so they pass identically on all three platforms.
   - Owner note: do not touch
     `test_cli_run_parallel_no_wait_adopts_active_manager` beyond that
     docstring. It has no load cut and no macOS branch; its Windows (and
     PostgreSQL) timeout ratios (2, 4, and 12) are not tasked here and are
     left for the owner.
   - Stop if: either test fails on any platform at the restored load or on
     macOS at the Linux budget. Apply [TS-0] (comprehension question 1).
     Keep the failure open; do not restore the reduced load, the macOS
     branch, a raised ceiling, or a fixed factor to land. Also stop if a
     reference baseline would land as `None`.
   - Done when: Step 6's tests pass and their RED failures are recorded;
     both stress tests pass at default parallelism on macOS and Linux
     locally; `grep -nE "(submit|status|task_completion)_timeout = " tests/cli/test_cli_run.py`
     shows the two stress tests' six assignments coming only from
     `_manager_reuse_timeouts`; the reference baselines, their discards and
     the first Windows and Linux header lines from the `Test` dispatches
     are in the Execution log; and the Windows `cli` jobs are green with
     the factor in place, their header lines recorded. A Windows failure
     goes through the 2026-11-01 trigger table before anything is
     named.

3. **Rewrite the three sleep-then-assert-absence sites (T1 to T3).**
   - T1, `tests/cli/test_cli_serve.py::test_serve_forces_no_idle_timeout`:
     delete `time.sleep(0.2)` and its "test invariant" comment. Replace the
     absence window with a positive witness at the owner boundary. Read the
     serve manager's own published TaskSpec (its `weft.log.tasks` event
     carries `taskspec`, `weft/core/tasks/base.py:2030`) and assert
     `metadata["idle_timeout"] == 0.0` while the child env sets
     `WEFT_MANAGER_LIFETIME_TIMEOUT=0.05`. That proves serve overrides the
     configured lifetime. The idle path's `<= 0` return (`weft/core/manager.py:8459`)
     carries the rest of the claim. Keep the `manager stop` tail. Stop if:
     redaction strips `metadata.idle_timeout` from the event. Then use
     `weft manager status --json` or the registry record if either carries
     it. Otherwise report back; do not keep the sleep.
   - T2, `tests/tasks/test_task_execution.py::test_base_task_worker_result_queue_backpressures_when_full`:
     wrap `task._worker_result_queue.put` so the wrapper sets a `blocked`
     event when the real `put` raises `queue.Full`, then re-raises. The
     wrapper must still call the real `put`. Replace `time.sleep(0.05)` with
     `assert blocked.wait(timeout=2.0)`, then assert
     `publisher_finished.is_set() is False`. The rest stays.
   - T3, `tests/tasks/test_multiqueue_watcher.py::test_postgres_dynamic_membership_rebinds_native_waiter`:
     retain the existing proof unless an equivalent exclusion proof can be
     written. The `true_count == quiet_baseline` equality after the 0.2 s
     window is the only assertion that the rebuilt `(a, c)` native waiter is
     not notified for `b`, which is the test's named claim. An ordering fence
     (write `b`, then `c`, and assert `not b_handled.is_set()` once `c_second`
     is handled) proves dispatch membership only. A waiter that stays
     subscribed to `b` while dispatch correctly excludes `b` passes that fence.
     The fence therefore cannot replace the window.
     Rewrite only if you can write a positive proof, at this seam, that the
     rebuilt native waiter excludes `b` from notification. That means an
     assertion on the waiter's own subscription or notification, with an
     expected value from an independent contract, plus a control that keeps
     `b` subscribed in the rebuilt waiter while dispatch still excludes it.
     The control must turn the new proof red. Skipping `remove_queue` is not a
     valid control: the test then fails at the
     `proxies[("pg-dynamic.a", "pg-dynamic.c")]` lookup, before any proof.
     If no such proof can be written at this seam, retain the test unchanged,
     sleep and equality included, and report back. Do not record the loss in a
     ledger as a substitute: a ledger entry cannot replace the only executable
     proof. Do not present a retained T3 as repaired.
   - Sensitivity: T1, change `idle_timeout_override=0.0` to `None` in
     `serve_manager_foreground`. T2, make `_publish_worker_result` return
     `True` without blocking when the queue is full. T3 (only if rewritten),
     the subscription control named above.
   - Done when: T1 and T2 pass and fail under their controls, and T3 is
     either rewritten with its control recorded or retained and reported
     back. T3 runs under `./bin/pytest-pg tests/tasks/test_multiqueue_watcher.py`.

4. **Give the T4 negative windows an entry witness, or retain them on the record.**
   - Files: `tests/tasks/test_task_execution.py`,
     `tests/tasks/test_multiqueue_watcher.py`, `tests/core/test_manager.py`.
   - For each of the five sites, find the primitive the stopper or cleaner
     blocks on (the task lifecycle condition or lock; the watcher topology
     condition; the Manager launch-worker join). Wrap that dependency
     primitive's `wait` or `acquire` so the wrapper signals that it was
     entered and then delegates. Assert the entry signal before asserting
     that the call has not returned. Keep the existing release-then-returns
     positive assertions.
   - Stop if: the only witness point is a production method rather than a
     dependency primitive. Then leave that site unchanged and add a one-line
     comment naming the false-pass mode and Pattern 12. These windows can
     only pass falsely; none can fail spuriously.
   - Sensitivity: for each rewritten site, remove the blocking wait in the
     owner (for example, make `stop()` skip its interlock wait). Record the
     failure.

5. **Scope the stdlib clock patch in the wrapper test to the driver module (T5).**
   - Files: `tests/tasks/test_task_observability.py` only. Keep
     `test_drive_task_until_applies_ready_result_after_wall_deadline`. It is
     the only proof that the module's `drive_task_until` wrapper forwards
     `pending_work=(task._has_pending_worker_results,)` to `drive_until`. The
     reactor-driver keeper calls `drive_until` directly, so it stays green if
     the wrapper drops that argument (cross-family round 1, F3).
   - Replace `monkeypatch.setattr(time, "monotonic", ...)` with a patch of
     the driver module's own `time` binding, as
     `tests/system/test_reactor_driver.py` does:
     `monkeypatch.setattr(reactor_driver, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values)))`,
     with `from tests.helpers import reactor_driver` and
     `from types import SimpleNamespace`. The stdlib clock seen by the
     heartbeat thread (`tests/conftest.py:615`) is then untouched. Remove
     `import time` only if nothing else in the module uses it. Keep the
     two-value iterator, the `drive_task_until` call, and the
     `process_calls == 2` assertion.
   - Sensitivity: delete the `pending_work=(task._has_pending_worker_results,)`
     argument from `drive_task_until` and confirm the rewritten test fails.
     Record the failure, restore, and confirm `git diff` is unchanged.
   - Done when: the control is recorded and
     `./.venv/bin/python -m pytest tests/tasks/test_task_observability.py tests/system/test_reactor_driver.py -q`
     passes.

6. **Decouple the three private-cursor Manager tests (C1).**
   - Files: `tests/core/test_manager.py` only.
   - Write the candidate-evidence ledger first (test-audit skill fields) for
     `test_manager_does_not_probe_inactive_public_spawn_queue`,
     `test_manager_pending_precheck_activates_public_spawn_queue`, and
     `test_manager_idle_discovery_skips_reserved_queues`. For each, name the
     observable contract (for example, "a spawn request on
     `weft.spawn.requests` is claimed after a real activity wake" or
     "reserved queues are never spawn sources") and the stronger
     owner-boundary proof, if any exists.
   - Then for each one: rewrite at the public seam if the contract is real.
     Write the request with a real `Queue`, drive with `process_once` or
     `drive_until`, and assert queue contents (`spawn_queue.peek_one`,
     reserved queue contents) without assigning cursors. Or delete it if a
     stronger keeper exists and passes its sensitivity check.
     `test_manager_idle_discovery_skips_reserved_queues` must end with a
     visible assertion in the test body.
   - Stop if: the contract is only "the precheck flag gates the probe", an
     internal sequencing claim with no observable effect. Retain the test
     and record why. Do not invent a public effect.

7. **Dropped (cross-family round 1, F4).** The terminal-event selector fix
   (H1) changes teardown cleanup behavior, so it is out of this plan. See H1
   under Dropped and Out of Scope. Do not edit the harness's local
   `TERMINAL_TASK_EVENTS` under this plan.

8. **Report harness teardown failures instead of discarding them (H2).**
   - Files: `tests/helpers/weft_harness.py`, `tests/test_harness_registration.py`.
   - In `_stop_active_managers`, capture `stop_manager`'s `(ok, error)`.
     In `cleanup` and `_stop_active_managers`, capture the survivor list
     from `_wait_for_registered_pids_to_exit()`. When either reports a
     failure, log one warning through the module logger. The warning names
     the manager TIDs and the surviving PIDs (no command lines or
     environments). It also names its phase, because the phases mean
     different things. A `stop_manager` failure is an unconfirmed stop, not a
     proven orphan. A survivor list from `cleanup` (:779) or
     `_stop_active_managers` (:1217) is taken before
     `_terminate_registered_pids()` (:781) escalates on managed PIDs. Those
     PIDs may still be terminated afterward; only owner PIDs, which are never
     force-terminated, are left without escalation. Word the warning as
     "survived before force termination", not as an orphan or a leak. This follows "best-effort paths try, catch, and log"
     ([WT-4] 4.4 "No over-armoring"). Do not raise, do not add new kill
     paths, and do not start terminating owner PIDs. Those would reverse
     the posture held by
     `test_harness_force_termination_targets_managed_pids_only` and
     `test_harness_pid_termination_failure_warns_and_continues_cleanup`,
     which is an owner decision this plan does not make.
   - Existing doubles: consuming the tuple makes the four existing
     `stop_manager` doubles in `tests/test_harness_registration.py` raise
     `TypeError` on unpacking. Each is `lambda *_args, **kwargs: True` (at
     `:808`, `:892`, `:953`, `:1005` at `b7eed9f9`; find them with
     `grep -n '"stop_manager"' -A 1 tests/test_harness_registration.py`).
     Change all four to return `(True, None)`, the real return shape. This is
     part of Task 8, not a separate change. Re-grep after the edit; no double
     may still return a bare bool.
   - Test: in the harness suite, patch `manager_runtime.stop_manager` to
     return `(False, "forced failure")` and assert the warning text through
     `caplog`. Patch `_wait_for_registered_pids_to_exit` to return a PID
     list and assert the warning lists it and names its phase. This is test
     infrastructure as the subject, so it is allowed under the skill.
   - Gate: no unexpected warning. The two new `caplog` tests, and any
     existing harness test that injects a stop or termination failure, emit
     the warning by design and are excluded. Stop if any other test emits it
     in the full fast suite. Read the warning's phase before naming anything.
     An unconfirmed stop or a pre-escalation survivor is a lead, not a
     located leak. Name a leak only when a process is shown alive after
     teardown completes. In either case apply [TS-0]: identify the test and
     its owner path, and do not suppress the warning.
   - Sensitivity: drop the new logging call; both tests fail.

9. **Use the harness context manager in the repeated-bootstrap test (H3).**
   - Files: `tests/cli/test_cli_run.py` only, in
     `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`.
   - Replace `harness = WeftTestHarness(); harness.__enter__(); try ... finally`
     with `with WeftTestHarness() as harness:` per iteration. Remove the
     `_closed` and `_tempdir` branches and the swallowed `PermissionError`.
     Leave the env lines untouched (owned by the 2026-09-08 plan).
   - Leave `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
     as it is. It calls `cleanup(preserve_database=True)`, which detaches
     the tempdir finalizer and leaves the tempdir for the caller to dispose.
     That is a real API gap, not duplication.
   - Done when: the test passes. A forced failure inside the body (local
     control: raise in the loop) produces the harness's diagnostics note on
     the exception.

10. **Remove the inert env key; run the serve service test with the production default (H4, H5).**
    - Files: `tests/helpers/weft_harness.py`, `tests/test_harness_registration.py`,
      `tests/cli/test_cli_serve.py`.
    - Delete `"WEFT_TEST_MODE": "1"` from `_patch_environment`. In
      `tests/test_harness_registration.py:154`, `:1612`, and `:1638`, probe
      `WEFT_MANAGER_LIFETIME_TIMEOUT` instead. It is a real override the
      harness sets and restores.
    - In `test_serve_restarts_singleton_services_without_duplicates`, also
      set `"WEFT_LIVENESS_MONITOR_ENABLED": "1"` (the production default)
      and add `INTERNAL_SERVICE_KEY_LIVENESS_MONITOR` to the
      single-live-service wait and the kill-and-replace loop. Do not change
      the harness-wide defaults.
    - Authoring gate: this protects singleton convergence and restart of
      every production-default Manager service under real process topology.
      The regression it catches: LivenessMonitor duplicates or fails to
      restart under `manager serve`. Existing coverage is in-process only
      (`tests/tasks/test_liveness_monitor.py`); this adds the CLI process
      boundary. It needs no production seam.
    - The LivenessMonitor restarts on the TaskMonitor backoff
      (`Manager._liveness_monitor_service_spec`), so the test's existing
      `WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS` override covers it.
    - Sensitivity: in `Manager._reconcile_managed_services`, skip the
      `if self._liveness_monitor_enabled:` append of
      `_liveness_monitor_service_spec()` (`weft/core/manager.py:7160-7161`)
      (control). The single-live-service wait for the LivenessMonitor must
      fail; record the failure. Do not use
      `Manager._liveness_monitor_supervision_allowed` (`:5917`): nothing
      calls it, so mutating it changes nothing.

11. **Close out.**
    - Fill the Execution log, Review Log, and Deviation Log (rows only for
      real departures). Flip `Status: completed` and the
      `docs/plans/README.md` index row (present as `draft`) in the same
      change.

## 7. Testing Plan

- Harness: `WeftTestHarness` for CLI and lifecycle (Tasks 2, 3/T1, 9, 10);
  `broker_env` with real `Queue` for Tasks 3/T2, 3/T3, 4, 6; the harness's
  own suite for Tasks 8 and 10/H4.
- Do not mock: queues, Manager or Consumer lifecycle, reservations, process
  exit. A witness wraps only a dependency primitive.
- Red first: every rewrite proves sensitivity against a named control (see
  each task). Rewrites of existing tests have no pre-fix product bug, so the
  control is a focused production mutation, as the skill allows.
- Tempting to skip: T3 on PostgreSQL. It skips on SQLite, so a SQLite-only
  run proves nothing for it.
- Acceptance for Task 1 (Ruling D1): after Task 1, no assertion in the six
  edited release tests equals a value copied from `bin/release.py` or from
  a workflow file, and none names a workflow-local identifier (job id, step
  `env` key, or `actions/github-script` variable). The acceptance is Task
  1's per-assertion disposition table plus controls (a) to (m), verified by
  reading the diff against the table and running the controls. The AST
  command in Task 1's Verify is a one-time lint aid, not proof; it is not a
  standing policy gate over the test tree and is not added to the final
  gates or to CI.

## 8. Verification

Load the repo environment first:

```bash
. ./.envrc
./bin/pytest-worker-count            # record the worker count used
```

Per task: the focused commands in each task, at default parallelism (no
`-n` override).

Final gates:

```bash
./.venv/bin/python -m pytest                                  # fast suite
./.venv/bin/python -m pytest -m ""                            # incl. slow (harness change affects all suites)
./bin/pytest-pg --all                                         # harness and T3 run on PostgreSQL
./.venv/bin/python -m pytest tests/system/test_release_script.py tests/system/test_release_workflow.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check tests
git diff --check
```

Success means every gate passes, no unexpected Task 8 harness warning
appears in the fast suite (that is, none outside the injected-failure tests;
see Task 8 Gate), and every sensitivity control is recorded with its
failure.
CI: the Windows lane is read through the 2026-11-01 trigger table.

## 9. Rollback

Every change is test-only, and nothing persists. Roll back by reverting the
task's commit. Task 2 is the only change that raises load; its two tests,
the calibration probe in `tests/conftest.py`, the
`tests/helpers/runner_calibration.py` helper and its tests revert with its
commit, and no product code changes. If it is reverted,
that is a recorded decision in the Deviation Log; it is never a quiet way to
get green ([TS-0]). Task 8 adds only logging, plus the four test doubles
corrected to the real `stop_manager` return shape. Reverting it restores
silent teardown.

## 10. Out of Scope

- Deleting `WEFT_MANAGER_REUSE_ENABLED` (2026-09-08 plan).
- Changing harness-wide service defaults, owner-PID termination policy, or
  making teardown raise on survivors (owner decision; Task 8 only reports).
- Aligning the harness terminal-event selectors with the canonical mapping
  (H1). It changes teardown STOP/KILL fan-out and preserve-database
  lingering-task handling, so it needs its own plan with a cleanup-risk
  assessment and a proof that cleanup still settles.
- Consolidating the `TaskRunner` fakes (N2), swapping harness readers for
  production readers (N3), and constant re-assertion tests in
  `tests/system/test_constants.py`. `bin/release.py` runs that file as a
  post-update step.
- Any new standing structural gate over the test tree. Such a gate is
  valid: under the owner's 2026-11-03 ruling (Ruling D3), a test that
  mechanically gates an aspect of `docs/program-theory.md` is a valid test.
  It is not added here, because adding one is a process change (+P) that
  this plan does not take on. Recurrence is stopped instead by the
  rewritten tests themselves, the existing Pattern 12 and test-audit review
  rules, and the Task 8 warning.
- Treating Task 2's calibration probe as a structural gate. The probe and
  `runner_calibration` helper are test infrastructure, not a standing gate
  over the test tree, so they do not raise the class to 3+P: they change no
  rule about which tests are valid, only how two tests' three budgets are
  sized on Windows. Extending the factor to other timeouts
  (`DEFAULT_CLI_SUBPROCESS_TIMEOUT`, `WINDOWS_TEMP_DIR_CLEANUP_TIMEOUT_SECONDS`,
  `test_cli_run_parallel_no_wait_adopts_active_manager`) is not tasked.
- The extension suites' `-n 0` lanes (third 4.4-f candidate under D2).
  Ruling D2 does not reach them; they stay unclassified and untasked.
  Task 1's AST lint aid is a one-time aid for Task 1's edits, not such a
  gate.
- The group (i) release assertions in the scope note under Ruling D1. They
  stay unless the owner widens Task 1's scope to them.

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 14 corrections — group (i) count 31 to 32; Class-5 escalator names A1/A2; Reading A consequence de-garbled; Task 1 pytest-order fork moved to D1a; D2 history (Windows cut since `28b089e2`, `c4915b04` extended it to macOS); D2 owner note on unmeasured kept timeouts and the `-n 0` extension lanes; Windows trigger table paraphrased exactly; T1 lines 685-689; H3 `__exit__`/`_attach_failure_diagnostics` lines; H5 lines 828-829; `run_cli` line 412; T3 control fixed (skipping `remove_queue` fails at setup) and coverage loss of the `true_count` claim flagged; Task 10 control retargeted (`_liveness_monitor_supervision_allowed` has no caller) plus shared-backoff note; index-row text updated (row exists)
- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED. F1 (stop_manager doubles), F2 (T3 fence proves membership not exclusion; retain the proof), F3 (keep the wrapper proof in Task 5), F4 (Task 7 dropped: harness change was not reporting-only), F5 (warning gate), F6 (D1 evidence), F7 ('measured budgets'), F8 (tuple count): all P2, accepted and applied. Round 2 (codex 0.161.0, 2026-11-02, scoped to the applied dispositions of F1–F5, F6–F8 confirmed): no blocker; all eight closed with live-code evidence (four `stop_manager` doubles at `tests/test_harness_registration.py:808,892,953,1005`; T3 retains its native-wake proof; the `drive_task_until` wrapper proof kept; Task 7 dropped because the selectors feed STOP/KILL fan-out and lingering-task handling at harness lines 1183 and 1250; survivor checks precede force termination at line 781). Review loop closed; the plan waits on the owner's D1, D1a, D2 and D3 rulings.
- owner ruling applied 2026-11-03: D1 ruled on the criterion 'tests that enforce the correctness of the release process stay; tests that make it brittle, restate a canonical file, or break on routine change go' (reading B with the criterion applied per assertion): both files kept as [TS-4]'s owners; group (ii)'s copied-configuration assertions and group (iii)'s identifier-dependent string checks narrowed in Task 1 to the properties they enforce; D1a dropped. D2 and D3 still pending. Cross-family round on the D1 change pending.
- owner ruling applied 2026-11-03: D2 ruled drifted for the load cuts (full concurrency on every platform; no macOS timeout branch; a single named Windows time factor justified as a legitimate platform difference in process spawning and disk access, to be measured on the Windows CI slice at full concurrency). D3 ruled authorized (owner, 2026-11-03: Tasks 3–10 land here; a test that mechanically gates an aspect of the theory is valid). Cross-family round on the D1, D2 and D3 changes pending.
- Cross-family review round 1 on the D1–D3 changes (codex 0.161.0, 2026-11-03): BLOCKED. F1 (the publish check lost the commit data-flow and the success polarity), F2 (routing proved one sample version), F3 (several replacements brittle in the other direction): accepted and applied with new controls (j)–(m). F4 (the AST command is a lint aid, not proof), F5 (`workflow_dispatch` or a draft PR triggers `Test`; three dispatches, median, stall discard), F6 (live-provider reader citation): accepted and applied. The residual D2 factor question removed (measure-first is the procedure); the group (i) owner question carries the per-location assessment; controls run in a disposable checkout. Round 2 pending.
- owner ruling applied 2026-11-03 (D2 addendum, 'Yes, let's try that for 9'): the Windows time factor becomes a calibrated runner ratio in the shape of SimpleBroker's `scale_timeout_for_calibration` (session-start probe of process creation and SQLite write cost against committed Linux reference baselines; the larger ratio, floored at 1.0, capped, applied on Windows only to the two stress tests' three timeouts; macOS and Linux pinned to 1.0; medians and factor printed in the pytest header). Cross-family round 2 pending covers this with the round-1 dispositions. Round 2 (codex 0.161.0, 2026-11-03, scoped to F1–F6 and the D2 addendum): F1–F6 closed with live evidence (release.yml:80–110 binding and negative comparisons; `ReleaseTarget.tag_name` at release.py:293 for the namespaces; the release switch consumed at release.yml:170; `build_precheck_env_overrides` at release.py:715; the AST command still reports 44 findings and is labelled a lint aid; test.yml:3–8 confirms `workflow_dispatch`; the live-provider citations). The addendum returned two residuals inside its span, applied here without a further round under the one-scoped-round rule: `_manager_reuse_timeouts` gains the `platform=` keyword its Step 6 tests need, forwarded to `scale_linux_budget`; the probe's child `join()` is bounded by `RUNNER_PROBE_JOIN_TIMEOUT_S` with terminate-then-kill cleanup and the error fallback (pytest-timeout does not cover `pytest_configure`), with a mechanism test. Review loop on the rulings closed; the plan waits on the owner's group (i) per-assertion question and the sibling stress test's Windows-and-PostgreSQL ratios (owner note).

Reviewer brief for round 1 (Tier-1 via `skills/call-agent/SKILL.md`): read
this plan and the cited tests and specs. Check D1's group classification
against the release tests. Check that every rewrite's witness observes a
dependency rather than the owner, that each sensitivity control is
falsifiable, and that Task 2 does not conflict with the 2026-11-01 Windows
triggers. Check [ALT-WT-017] and [ALT-WT-018] for reversals.
- owner ruling applied 2026-11-03 (outside review, "Let's keep it in Python, just fix the logic"): the publish gate's polarity check becomes a truth-table check of the failure condition (the condition is reduced to a formula over the two comparisons and evaluated under all four assignments; it must fail the gate for every assignment but completed-and-successful), with the no-run case checked separately and control (n), the `&&` mutant, added. A verification-instruction correction inside an already reviewed span, applied without a further round.
- owner ruling applied 2026-11-03 ("Can we just keep it as is?"): the publish gate's two polarity substring assertions stay unchanged; the truth-table check (a JavaScript-to-Python `eval` reduction, a compiler inside a test) is withdrawn, and the behavioral Node run that the probe showed feasible (script executed under Node with `github`, `context` and `core` stubbed; six API cases; the `&&` mutant publishes a completed failure) is declined as a new test-time toolchain requirement. The connective blind spot and the `latest` identifier dependence are named in the Task 1 row; control (n) is recorded as the blind spot, not a control. Probe harness kept at the session scratchpad `series/plan9/harness.js` and summarized in the Execution log.

- implementation 2026-11-03 (owner's implement instruction): group (i) scope note closed on the owner's implement instruction (2026-11-03) with the recommended option: apply the per-location recommendation table to all six locations (applied in Task 1). Tasks 0 to 6, 9 and 10 landed; Task 8 stopped at its Gate (not landed, leads recorded); Task 2 Step 4 pending the owner-run measurement; T3 retained. Status stays `draft` because Task 2's Done-when (baselines and Windows green) and Task 8 are open. No cross-family review of the implementation was run in this session.

## Execution log
- 2026-11-03 probe (session lead, before the owner's keep-as-is ruling): the `actions/github-script` body of `release.yml`'s run-check step runs under local Node 24 inside `new Function("github","context","core","process", ...)` with `github.rest.actions.listWorkflowRuns` returning canned `workflow_runs`, `context.repo` fixed, and `core.setFailed` capturing. Results, one lookup call per case with `head_sha` equal to `EXPECTED_SHA`: no runs → fails; feature-branch run only → fails; in progress → fails; completed/failure → fails; completed/success → passes; older failure plus newer success → passes. The `&&` mutant: completed/failure → passes (publishes), in progress → fails. Not adopted; recorded as the known feasible behavioral test if the owner ever wants one.

Authoring-time resolution evidence (`b7eed9f9`, 2026-11-02). Implementers
append below.

- `grep -c "^def test_" tests/system/test_release_script.py` → 37;
  `pytest --collect-only -q` on the two release files → 46 and 1.
- `grep -n addopts pyproject.toml` → `:140 ... -n logical --maxprocesses 12`;
  `bin/pytest-worker-count` `MAX_PYTEST_XDIST_WORKERS: Final[int] = 12`;
  `git log -G maxprocesses -- pyproject.toml` → `7b9c5356` (12),
  `dc4fb4e1` (4), `8a221e35` (8), `75f5a977` (12).
- `grep -n "constrained_parallelism\s*=" tests/cli/test_cli_run.py` → :3002, :3043;
  `git log -S constrained_parallelism` → `c4915b04` 2026-05-07;
  `git log -S 'max_workers = 2 if os.name == "nt"'` → `28b089e2` 2026-04-09,
  `fa4e1435`, `c4915b04`.
- `sed -n 684,689p tests/cli/test_cli_serve.py`; `sed -n 4336,4337p tests/tasks/test_task_execution.py`;
  `sed -n 2482,2485p tests/tasks/test_multiqueue_watcher.py`: sleep sites confirmed.
- `grep -rnE "assert not [A-Za-z_.\[\]\"]+\.wait\(" tests | wc -l` → 9;
  plus `... .wait(...) is False` hits at `tests/tasks/test_task_execution.py:672`, `:1144`,
  `tests/core/test_manager_burst_bookkeeping.py:567`, `tests/core/test_manager.py:4088`.
  Each read and classified as T4 or N1.
- `grep -rnE 'monkeypatch\.setattr\((time|os|sys)\s*,\s*"(monotonic|sleep|time|time_ns|getpid|platform)"' tests`
  → 1 hit (`tests/tasks/test_task_observability.py:151`); `tests/conftest.py:615`
  `now = time.monotonic()` in the heartbeat thread.
- `grep -o "manager\._[a-z_]*" tests/core/test_manager.py | wc -l` → 942;
  private-cursor assignments across `tests/` → 23 (13 in `tests/core/test_manager.py`).
- `grep -rnE "WEFT_TEST|test_mode|PYTEST|pytest|TESTING" weft --include=*.py | wc -l` → 0;
  `grep -rn WEFT_TEST_MODE --include=*.py .` → only the harness and `tests/test_harness_registration.py`.
- `grep -rn MANAGER_REUSE weft integrations extensions --include=*.py` → `weft/_constants.py:1827`, `:2786` only;
  `grep -rn MANAGER_REUSE_ENABLED tests --include=*.py | wc -l` → 11.
- `grep -rnE "LIVENESS_MONITOR_ENABLED\"?\s*[:\]=]+\s*\"?(1|true)" tests` → `tests/core/test_manager.py:1699`, `tests/system/test_constants.py:755` (none under `tests/cli`);
  `TASK_MONITOR_ENABLED ... (1|true)` under `tests/cli` → `tests/cli/test_cli_serve.py:550` only.
- `grep -n "^TERMINAL_TASK_EVENTS" -A 9 weft/_constants.py` → 8 keys; harness local set → 6 names.
- `grep -n "_wait_for_registered_pids_to_exit()\|manager_runtime.stop_manager(" tests/helpers/weft_harness.py`
  → :779, :1206, :1217; `stop_manager` signature returns `tuple[bool, str | None]` (`weft/core/manager_runtime.py:2387`).
- `grep -n "monkeypatch.setattr(consumer_module, \"TaskRunner\"" tests/tasks/test_task_execution.py | wc -l` → 9.
- `grep -rnE "^\s*(async )?def test_" tests --include=*.py | wc -l` → 3512; `find tests -name 'test_*.py' | wc -l` → 239;
  `grep -rn "time\.sleep(" tests --include=*.py | wc -l` → 180 (context only).
- `ps -axo pid,ppid,lstart,command | grep -E "weft-[a-z0-9-]+:manager"` → no live manager.
- All paths cited in this plan were checked with `test -e`. Symbols were
  checked with `grep -n` at the cited lines (`serve_manager_foreground`
  :2301/:2313, `build_manager_spec` :1255, `_publish_worker_result` :796,
  `INTERNAL_SERVICE_KEY_LIVENESS_MONITOR` `weft/_constants.py:1608`,
  `test_drive_until_applies_one_ready_result_after_the_deadline`
  `tests/system/test_reactor_driver.py:123`).
- Index row: present in `docs/plans/README.md` as `draft` (uncommitted
  series change); `tests/specs/test_plan_metadata.py` passes.

### Implementation execution (2026-11-03, landing after plan 10 at `f2d9b36f`)

Task 0, rulings and comprehension answers (recorded before the first edit).
D1 (reading B, per-assertion criterion), D1a (dropped), D2 (`drifted`, with
the calibrated-ratio addendum) and D3 (authorized) are all recorded above
with owner and date; none is `pending`, so Tasks 1 to 10 may start.

1. If Task 2's restored load turns
   `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
   red on macOS: stop corrective work and acceptance reruns at once, and
   preserve the exact command, backend (SQLite), worker count (default
   xdist parallelism), and the failure output and harness diagnostics
   ([TS-0] contention stop, [ALT-WT-017], Pattern 1). Re-derive what the
   theory predicts: repeated concurrent `weft run` bootstraps in one context
   converge on exactly one live Manager (a Manager belongs to the context;
   supersession changes who takes future work and never kills children),
   and every submitted task completes within its budget. Locate the causal
   flaw in the product, the harness, the oracle, or the environment before
   any correction. A lower-load run is allowed only as a labelled
   diagnostic of a stated hypothesis and never closes the failure. I may
   not restore the platform load reduction, re-add a macOS timeout branch,
   raise the ceiling, fix a factor, or pad any timeout to obtain green. On
   Windows the 2026-11-01 trigger table additionally refuses raising a
   deadline or lowering the worker count when the proposal cites CI stalls.
2. The publish-check test may inspect the `actions/github-script` body as
   text because the gate's JavaScript runs only in GitHub Actions, so text
   inspection is the cheapest independent guard of the [TS-4] clauses (one
   lookup of a completed Test push run, no polling), and the retention bar
   keeps source inspection when it is that guard and survives an
   identifier-only refactor. Loop keywords, `setTimeout`/`setInterval`, and
   the REST parameters `workflow_id`, `event`, `head_sha` are defined by
   JavaScript and the GitHub API, so no refactor of the script can rename
   them. `expectedSha` and `latest` are names the script author chose;
   renaming them changes no behavior, so checking them is what the owner's
   criterion calls wrong (the two `latest` polarity substrings are kept
   anyway by the owner's explicit 2026-11-03 ruling, blind spots named). The
   same criterion removes the exact dict equality in
   `test_build_precheck_env_overrides_sets_worker_count`, which fails when
   anyone adds an unrelated key while a narrower assertion holds the
   forwarding claim.

Task 1 (Ruling D1, D1a, and the group (i) scope-note closure).

- Lint aid before the edits (the Verify command, run from the repository
  root after `. ./.envrc`): exit 1, `44 finding(s)`, the same count as at
  `b7eed9f9`; deny list `['EXPECTED_SHA', 'REPOSITORY', 'TAG_NAME',
  'expectedSha', 'github-release', 'latest', 'leftTimestamp',
  'publish-release', 'publish-to-pypi', 'response', 'rightTimestamp',
  'runs', 'verify-main-test-workflow', 'verify-tag-current']`.
- Lint aid after the edits: exit 1, `2 finding(s)`, both
  `R4 names workflow-local identifier 'latest'` in
  `test_publish_checks_completed_main_push_once_without_polling`, on the two
  polarity substrings `'latest.status !== "completed"'` and
  `'latest.conclusion !== "success"'`. Disposition: false positive by
  ruling. The owner kept both substrings unchanged on 2026-11-03 ("Can we
  just keep it as is?"); their blind spots are named in the Task 1 table
  and in a comment above them in the test.
- Shared rules applied: module-level `_REPO_ROOT =
  Path(__file__).resolve().parents[2]`; triggers read through
  `workflow.get("on", workflow.get(True))` (`_workflow_trigger`); gates
  found by content (`_release_gate_workflows`: every `.github/workflows/*.yml`
  whose trigger has `push.tags`); the run-check step found as an
  `actions/github-script@` step whose script calls `listWorkflowRuns(`;
  the PyPI publish job is the only job whose `permissions` grant
  `id-token: write`; the release-asset job is the job with a
  `softprops/action-gh-release@` step. Helper functions live at module
  level next to `_release_state`.
- Group (ii), as the table directs:
  `test_build_precheck_commands_cover_release_gate_and_quality_gates` keeps
  the six gate lookups (the live-provider lookup is now an `any(...)`
  presence check), `-m ""`, `--all`, `--check`; the extras equality became
  the `pyproject.toml` `[project.optional-dependencies]` relation over every
  non-root target's `package_name` (names normalized PEP 503 style); the
  quality and mypy target sets became per-name `in` checks plus the
  per-target `package_dir` and `<package_dir>/<name>` relations; the
  `PRECHECK_ENV_OVERRIDES ==` equality became the fail-fast check on
  `build_precheck_env_overrides()["PYTEST_ADDOPTS"].split()` (with
  `_pytest_worker_count` patched) and the two live-provider `"0"` checks.
  Dropped as the table says: `command[:2] == ("uv", "run")`, `"python"` and
  `"dev"` in the live-provider command, `--config-file pyproject.toml`, the
  `pytest_targets ==` set, and the `WEFT_EAGER_FAILURE_TRACEBACK` value.
  `test_build_precheck_env_overrides_sets_worker_count` asserts the `"17"`
  forwarding and `PRECHECK_ENV_OVERRIDES.items() <= result.items()`.
  `test_build_postupdate_steps_build_all_publishable_packages` compares the
  set of resolved `cwd`s of steps whose command contains `"build"` with the
  set of package directories the gates pass to the reusable workflow, with
  no index checks and no order (Ruling D1a). Deviation, small: the gate's
  package-directory value is identified as the one `with` value that names
  a directory holding a `pyproject.toml` (exactly one per publishing job is
  asserted), because naming the `package_dir` input would write an input
  name into the test; the routing test checks the same value against
  `target.package_dir` independently.
- Group (iii), per the disposition table: routing over the supported tag
  language (pattern form `<literal>*`, coverage of `target.tag_name("")`,
  exactly one target per gate, prefix exclusivity across gates, and the
  `tag_name("1.2.3")` sanity case); the publishing job found by
  `uses: ./...` naming an existing `workflow_call` workflow with no `if:`;
  one shared `uses` value across gates; the `with` relations (own
  `package_name` present, no other target's, exactly one value resolving to
  `target.package_dir`, and the release switch read from the release-asset
  job's `if:` expression with the declared default). The publishing-job
  relations sit in the module helper `_assert_gate_publishes_target`, and
  the exclusivity loop is one flat pair loop, because the inline form
  exceeded ruff's C901 complexity limit (14 > 10); the assertions are the
  ones the table names. The publish-check test: run-check found by the
  lookup; the commit data-flow check (an `env` entry `${{ inputs.<name> }}`
  where every gate passes the same value for `<name>` as for the build
  job's checkout `ref` input, then the `process.env.<KEY>` binding whose
  identifier is the `head_sha:` value); `workflow_id` equals the file name
  of `.github/workflows/test.yml`, whose `name` is `Test`; quote-agnostic
  `event: push`, `main`, `master`; the two polarity substrings unchanged;
  no `setTimeout`/`setInterval` and no loop body containing
  `listWorkflowRuns`; the `needs` closures computed from parsed ids.
  `test_github_release_uploads_only_python_distributions`: the release-asset
  job by content; every `files` entry ends with `.whl` or `.tar.gz`, at
  least one of each; `id-token` absent from the job's permissions; the
  sigstore check is gone (guard lost as the table names).
- Group (i) locations (scope-note closure, each row applied):
  `:191` `test_inspect_release_state_uses_target_package_name_and_tag_namespace`
  now runs on a test-owned target (`dataclasses.replace` of the Docker
  target with `example-package` and `example_namespace`) and asserts
  `example_namespace/v0.1.0`; `:201`-`:203`
  `test_django_release_target_uses_namespaced_tag_and_package_dir` asserts
  the Django target is namespaced, its `pyproject_path` sits in its
  `package_dir`, the `pyproject.toml` project name equals its
  `package_name`, and tag composition on a test-owned namespace; `:711` and
  siblings in `test_main_dry_run_defaults_to_core_only` assert
  `target.tag_name("") not in output` for every non-root target; `:872`
  and `:896` assert inclusion of every non-root target's `tests` suite
  path, and exclusion of the Docker and macOS-sandbox suites when those
  runners are unavailable; `:943` uses test-owned overrides
  (`-k example`, `EXAMPLE_FLAG`) and asserts `--lf -k example`; `:984`-`:985`
  asserts `PYTEST_ADDOPTS='-k example'` and `EXAMPLE_FLAG=1`.
- Sensitivity controls, run one at a time in a disposable checkout
  (`git worktree add --detach <scratchpad>/plan9/wt HEAD`, the two edited
  test files copied in, each mutation restored before the next, then
  `git worktree remove --force`), each with
  `pytest <test> -q -n 0 -o addopts=`:
  (a) Docker `uv build` step deleted: postupdate test fails at the
  `built_dirs == gate_package_dirs` set equality.
  (b) `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS` set to `"1"`: precheck test fails,
  `assert '1' == '0'`.
  (c) `_pytest_worker_count()` replaced by `"4"`: worker-count test fails,
  `assert '4' == '17'`.
  (d) `"--extra", "django"` removed from the PostgreSQL command: precheck
  test fails, `assert 'weft-django' in {...}` for that command.
  (e) `"extensions/weft_docker"` removed from `ruff check`: precheck test
  fails, `assert 'extensions/weft_docker' in (...)`.
  (f) Docker gate pattern changed to `weft_django/v*`: routing test fails
  on coverage for target `docker`, `assert set() == {...release-gate-docker.yml}`.
  (g) Docker gate `expected_tag_commit` set to `${{ github.event.before }}`:
  publish-check test fails, `commit_keys` empty for the run-check job.
  (h) `setTimeout` promise inserted: publish-check test fails on the timer
  regex.
  (i) `dist/*` added to `files`: asset test fails on the suffix `all(...)`.
  (j) `head_sha: context.sha`: publish-check test fails, `filtered_keys`
  empty.
  (k) `.status` `!==` to `===`, then `.conclusion`: the unchanged substring
  assertions fail in both runs.
  (l) `weft_django/v2*` added to the Docker gate: routing test fails on the
  exclusivity assertion (Docker prefix extends Django's).
  (m) root pattern `v1*`: routing test fails on coverage for target `core`.
  (n), not a control: `||` to `&&` in the failure condition; the
  publish-check test passes, as the owner's ruling records (the named blind
  spot).
  Unmutated: `pytest tests/system/test_release_script.py
  tests/system/test_release_workflow.py -q` passes (47 cases).

Task 2 (Ruling D2 and addendum).

- Steps 1 and 2: both stress tests now run `iterations = 8` /
  `max_workers = 4` and `iterations = 5` / `max_workers = 4` with no
  platform condition; `constrained_parallelism` is deleted. The interim
  `sys.platform == "win32"` branches of Step 2 were not written, because
  the measurement branch of Step 4 is not run here (see Step 4); Step 5's
  final form landed directly.
- Step 3: `tests/helpers/runner_calibration.py` holds `RunnerCalibration`,
  `probe_runner()` (spawn-context start-to-join of a no-op child and a
  SimpleBroker `Queue("weft.calibration.probe", persistent=True)` write
  cycle in a fresh temporary directory, five samples each, medians; the
  child join bounded by `RUNNER_PROBE_JOIN_TIMEOUT_S = 30.0` with
  terminate, join 5.0 s, kill; the disk cycles bounded by one deadline of
  the same length), `REFERENCE_SPAWN_MEDIAN_S` and
  `REFERENCE_SQLITE_MEDIAN_S` (both `None  # unmeasured`),
  `RUNNER_TIME_FACTOR_CEILING = 20.0`, `windows_time_factor`,
  `scale_linux_budget`, and `describe_calibration` (the header line). A
  failed or stalled probe catches `OSError`, `RuntimeError`,
  `sqlite3.Error`, and SimpleBroker's `BrokerError` (not a blind
  `Exception`, which ruff's BLE001 refuses without a [TS-3.1] registry
  entry) and returns the error text with a neutral factor.
  `tests/conftest.py` probes in `pytest_configure` in the controller only
  (no `config.workerinput`), stores the result in `config.stash`, hands it
  to workers through an `optionalhook` `pytest_configure_node` into
  `workerinput["weft_runner_calibration"]`, prints it from
  `pytest_report_header` in the controller, and exposes the session
  fixture `runner_calibration`. A worker-side check (a throwaway test,
  deleted after use, run with `-n 2`) showed `gw0` reading the controller's
  medians with `error=None`. The header shows at the CI verbosity
  (`pytest -v` with `addopts=-q`); with the local default `-q` alone pytest
  prints no header.
- Step 4: pending: owner-run measurement. This session cannot push or
  dispatch workflows. Procedure, copy-pastable from the repository root:

  ```bash
  git switch -c plan9-runner-baselines main      # main already holds Task 2
  git push -u origin plan9-runner-baselines
  for n in 1 2 3; do gh workflow run test.yml --ref plan9-runner-baselines; sleep 20; done
  gh run list --workflow test.yml --branch plan9-runner-baselines --event workflow_dispatch --limit 3
  # for each <run-id>:
  gh run view <run-id> --json jobs --jq '.jobs[] | select(.name | endswith(", cli)")) | "\(.databaseId) \(.name) \(.startedAt) \(.completedAt)"'
  # for each cli <job-id>:
  gh run view <run-id> --log --job <job-id> | grep "weft runner calibration:"
  ```

  Read the medians from the `test (ubuntu-latest, 3.12, cli)` and
  `test (ubuntu-latest, 3.14, cli)` jobs only (the Ubuntu 3.13 job runs with
  coverage and `-m ""`). Apply this plan's stall rule per job across the
  three dispatches (discard a job whose wall time or probe median exceeds
  three times the median of the other two; dispatch again if fewer than
  two survive). Each baseline is the median of the surviving Ubuntu jobs'
  medians. Fill `REFERENCE_SPAWN_MEDIAN_S` and `REFERENCE_SQLITE_MEDIAN_S`
  in `tests/helpers/runner_calibration.py` (drop `# unmeasured`; name the
  runner, Python versions, run IDs, and date in their docstrings), and
  record every header line, every discard, and the Windows and macOS
  header lines here. Then
  `windows_time_factor` goes from neutral (`unmeasured` in the header) to
  active (`applied` or `capped`) on the Windows `cli` jobs, which is where
  `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
  and `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
  receive the scaled budgets; `tests/system/test_runner_calibration.py`
  is independent of the committed values. Until then the Windows budgets
  equal the Linux budgets (factor 1.0), a tightening from today's
  unmeasured 120.0 s / 10.0 s / 120.0 s; a Windows red in that window is
  read through the 2026-11-01 trigger table, not answered by padding.
  Local data point, not a reference (Apple M4 Max, macOS 27.0.1,
  Python 3.14.9, three `probe_runner()` calls):
  `spawn_median=0.0537s sqlite_median=0.0028s`,
  `spawn_median=0.0574s sqlite_median=0.0029s`,
  `spawn_median=0.0600s sqlite_median=0.0030s`, each
  `factor=1.00 platform=darwin pinned`.
- Step 5: `MANAGER_REUSE_SUBMIT_TIMEOUT_S = 60.0`,
  `MANAGER_REUSE_STATUS_TIMEOUT_S = 5.0`,
  `MANAGER_REUSE_TASK_COMPLETION_TIMEOUT_S = 10.0`, and
  `_manager_reuse_timeouts(calibration, *, platform=sys.platform)` in
  `tests/cli/test_cli_run.py`; both stress tests take `runner_calibration`
  and set all three timeouts from it only.
  `PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT` keeps its value; its
  docstring now names `test_cli_run_parallel_no_wait_adopts_active_manager`
  on Windows or PostgreSQL. That test is otherwise untouched (owner note).
  Done-when grep, `grep -nE "(submit|status|task_completion)_timeout = "
  tests/cli/test_cli_run.py`: `:3013`, `:3014`, `:3015` belong to
  `test_cli_run_parallel_no_wait_adopts_active_manager` (untouched); the
  two stress tests assign all three by tuple unpacking from
  `_manager_reuse_timeouts(runner_calibration)` (`:3036`, `:3072`).
- Step 6: `tests/system/test_runner_calibration.py` (`shared`), 12 cases.
  RED before the symbols existed: `ImportError: cannot import name
  'MANAGER_REUSE_STATUS_TIMEOUT_S' from 'tests.cli.test_cli_run'` (the
  helper module was also absent). GREEN: `12 passed`. Sensitivity controls
  in a disposable worktree (`git worktree add --detach`, new files copied
  in, each mutation restored, worktree removed): factor applied off
  Windows, pinned test fails `assert 20.0 == 1.0`; floor removed, floor test
  fails `assert 0.5 == 1.0`; ratios averaged, larger-ratio test fails
  `assert 4.5 == 6.0`; spawn ratio only, it fails `assert 3.0... == 6.0`;
  cap removed, ceiling test fails `assert 50.0 == 20.0`; unbounded
  `join()`, the bounded-probe test fails `Timeout (>60.0s) from
  pytest-timeout`; a literal 120.0 s Windows completion budget in
  `_manager_reuse_timeouts`, budgets test fails
  `(240.0, 20.0, 120.0) == (240.0, 20.0, 40.0)`; a macOS doubling inside
  it, budgets test fails `(60.0, 5.0, 20.0) == (60.0, 5.0, 10.0)`.
- Stop-if check on macOS (default parallelism, both tests together, three
  runs, SQLite): repeated-bootstrap 17.02 s, 16.83 s, 17.45 s; harness
  cleanup integrity 10.52 s, 10.33 s, 10.61 s; all passed at full load and
  the Linux budgets. Linux and Windows are verified only by the owner's CI
  run.

Task 3 (T1 to T3).

- T1, `test_serve_forces_no_idle_timeout`: the `time.sleep(0.2)` window
  and its "test invariant" comment are gone. The module helper
  `_published_manager_idle_timeouts` reads `weft.log.tasks` and collects
  `taskspec.metadata.idle_timeout` from the serve Manager's own events
  (`row["tid"] == record["tid"]`); the test asserts the set is `{0.0}`
  while the child env sets `WEFT_MANAGER_LIFETIME_TIMEOUT=0.05`, then keeps
  `process.poll() is None` and the `manager stop` tail. Redaction does not
  strip the field (`redact_taskspec_dump` touches only configured paths),
  so the Stop-if fallback was not needed. Control (disposable worktree):
  `idle_timeout_override=0.0` to `None` in `serve_manager_foreground`; the
  test fails `assert {0.05} == {0.0}`.
- T2, `test_base_task_worker_result_queue_backpressures_when_full`: the
  instance's `_worker_result_queue.put` is wrapped; the wrapper calls the
  real `put` and re-raises `queue.Full`. Deviation, small: the wrapper sets
  `blocked` on the second `Full`, not the first, so the witness shows the
  publisher retried after a `Full` (it is in its loop) rather than only that
  one `put` timed out; with the first-`Full` form the control below would
  race the `publisher_finished` check. `time.sleep(0.05)` is replaced by
  `assert blocked.wait(timeout=2.0)`, then `publisher_finished.is_set() is
  False`. Control: `_publish_worker_result` returns `True` on `Full`; the
  test fails at `assert blocked.wait(timeout=2.0)`.
- T3, `test_postgres_dynamic_membership_rebinds_native_waiter`: retained
  unchanged, sleep and `true_count == quiet_baseline` equality included,
  and not presented as repaired. No positive exclusion proof exists at
  this seam without reading SimpleBroker's PostgreSQL waiter internals:
  `_RecordingNativeWaiter.signature` is computed by the test from the
  queue list it passes, so a delegate that stays subscribed to `b` would
  not change it, and the waiter exposes no public subscription set to
  assert on. Reported back. It passes under `./bin/pytest-pg
  tests/tasks/test_multiqueue_watcher.py` (see below).

Task 4 (T4). Each site now asserts an entry witness on the dependency
primitive the stopper or cleaner blocks on before it asserts that the call
has not returned; the witnesses delegate to the real primitive. The
release-then-returns assertions are unchanged.

- `test_base_task_process_entry_publishes_turn_active_with_owner_claim`
  and `test_base_task_run_loop_entry_publishes_active_with_owner_claim`:
  the paused claim runs inside `with self._task_lifecycle_lock`, so the
  first block is that lock's acquire. `AcquireEntryWitness` (module class
  in `tests/tasks/test_task_execution.py`) replaces
  `task._task_lifecycle_lock` with a delegating wrapper and signals when
  the stop thread enters `acquire`; `not stop_returned.wait(timeout=0.05)`
  became `lifecycle_lock.entered.wait(timeout=2.0)` then
  `not stop_returned.is_set()`. The second, immediate
  `not stop_returned.is_set()` (after the claim is released) now follows a
  witness that the stop thread entered `drive_thread.join`
  (`witness_thread_join`), the owner join that `stop()` performs while the
  drive is active; this extends the same claim's second check, which the
  plan did not list.
- `test_base_task_stop_waits_for_starting_interlock`: `run_in_thread`
  holds `_task_lifecycle_lock` across the blocked `thread.start()`, so the
  witness is the same lock wrapper, watched for the stopper;
  `stop_returned.wait(timeout=0.05) is False` became
  `lifecycle_lock.entered.wait(timeout=2.0)` then
  `stop_returned.is_set() is False`.
- `test_background_mutation_committed_before_stop_returns_success`: the
  committing mutation holds the watcher's `_topology_lock` (an `RLock`);
  the same `AcquireEntryWitness` (imported from
  `tests/tasks/test_task_execution.py`, no new module) wraps it, watched
  for the stopper.
- `test_manager_cleanup_waits_for_active_child_launch_worker`: cleanup's
  `_drain_active_child_launches_for_cleanup` waits on
  `manager._worker_result_event.wait`; `_witness_event_wait` (module helper
  in `tests/core/test_manager.py`) wraps that `threading.Event.wait` for
  the cleanup thread; `not cleanup_returned.wait(timeout=0.25)` became
  `drain_wait_entered.wait(timeout=3.0)` then
  `not cleanup_returned.is_set()`. The helper was extracted because the
  inline wrapper exceeded ruff's C901 limit.
- Controls (disposable worktree; each restored): `stop()` returning at the
  top of `_request_stop_and_maybe_finalize` fails the process-entry,
  run-loop, and starting-interlock tests at `lifecycle_lock.entered.wait`;
  removing the `owner.join(timeout=remaining)` call fails the process-entry
  and run-loop tests at `join_entered.wait`; `MultiQueueWatcher.stop`
  without its `with self._topology_lock:` fails the watcher test at
  `topology_lock.entered.wait`; `_drain_active_child_launches_for_cleanup`
  returning at once fails the Manager test at `drain_wait_entered.wait`.
- The seven rewritten Task 3 and Task 4 tests passed five consecutive runs
  at default parallelism.

Task 6 (C1), candidate-evidence ledger, written before the edits.

| Test | What it can detect today | Observable contract | Non-test callers of the seam | Stronger owner-boundary proof | Decision before edits |
|---|---|---|---|---|---|
| `test_manager_does_not_probe_inactive_public_spawn_queue` (`tests/core/test_manager.py`) | With cursors forced to "no active queue, discovery not due, no precheck", one `process_once` leaves a written request on `weft.spawn.requests` | A quiet Manager does not claim a request on an inactive public spawn queue until broad discovery is due or an activity wake confirms pending work ([WT-4] 4.3 "A quiet task does nothing"; `MultiQueueWatcher._update_active_queues`) | `_update_active_queues` is the drain loop's own step; the cursors are its private state | None found; the reserved-custody idle test proves a different claim | Rewrite at the public seam: real `Queue` write, `process_once`, queue contents; control discovery timing through the watcher module's own `time` binding (a module-owned clock seam, Pattern 12) instead of assigning `_next_inactive_probe_at`; add the positive half (claimed once discovery is due) so the absence is reached |
| `test_manager_pending_precheck_activates_public_spawn_queue` | With the precheck flag forced on, `process_once` claims the request | A spawn request is claimed on the turn after a real activity wake, without waiting for broad discovery | `_mark_pending_messages_prechecked` is called from the wait path (`MultiQueueWatcher` activity-hint handling) and Manager service convergence | None found | Rewrite: real write, real `wait_for_activity` through `drive_until`, discovery held not due by the clock seam, assert claim and reserved custody |
| `test_manager_idle_discovery_skips_reserved_queues` | A patched `_queue_has_pending` raises if discovery probes a reserved queue; no assertion in the body | Reserved queues are custody, never spawn sources or discovery activity (`Manager._queue_counts_as_wait_activity`) | `_queue_counts_as_wait_activity` is used by the watcher's discovery and wait-activity paths | `test_manager_idle_pending_work_excludes_retained_reserved_custody` covers idle pending work through `_manager_owned_work_pending`, a different function | Try a public rewrite (retained reserved row, discovery due, `process_once`, row untouched and nothing launched) and keep it only if the reserved-exclusion mutation turns it red; otherwise retain the original with a visible body assertion |

Task 5 (T5). `test_drive_task_until_applies_ready_result_after_wall_deadline`
now patches the driver module's own binding,
`monkeypatch.setattr(reactor_driver, "time", SimpleNamespace(monotonic=...))`
(`from tests.helpers import reactor_driver`, `from types import
SimpleNamespace`), with the two-value iterator, the `drive_task_until` call,
and `process_calls == 2` unchanged; `import time` stays (the module still
uses `time.time_ns`). Control (disposable worktree): deleting
`pending_work=(task._has_pending_worker_results,)` from `drive_task_until`
fails the test, `reactor evidence did not match before timeout
(timeout=5.0, turns=1, latest=False, ...)`.
`pytest tests/tasks/test_task_observability.py tests/system/test_reactor_driver.py`
passes (52 cases).

Task 6 (C1), after the ledger above.

- `test_manager_does_not_probe_inactive_public_spawn_queue` and
  `test_manager_pending_precheck_activates_public_spawn_queue` are rewritten
  at the public seam with no cursor assignment. Shared setup moved to
  `_discovery_manager` and `_INACTIVE_QUEUE_SPAWN_PAYLOAD`. Discovery timing
  is controlled by `_WatcherClock`, which replaces the
  `weft.core.tasks.multiqueue_watcher` module's `time` binding (its only use
  is `time.monotonic`) with a real-rate clock plus an offset. The first test:
  initial `process_once`, write a real request, hold discovery not due,
  `process_once` leaves the request on `weft.spawn.requests` and out of the
  reserved queue; then make discovery due and the next `process_once`
  claims it (the positive half shows the decision was reached). The second
  test: discovery held not due, a real write, then `drive_until` with
  `manager.process_once` and `manager.wait_for_activity` until the launch is
  recorded; it asserts the claim and reserved custody.
- `test_manager_idle_discovery_skips_reserved_queues`: no queue-level
  effect exists to observe (the reserved queues' peek handler,
  `BaseTask._handle_reserved_message`, only logs), and the stronger-looking
  keeper `test_manager_idle_pending_work_excludes_retained_reserved_custody`
  stays green under the reserved-exclusion mutation below, so it is not a
  keeper. The test is rewritten without cursor assignment and ends with
  visible assertions: discovery is made due through `_WatcherClock`, one
  public `process_once` runs, and a delegating wrapper on
  `Manager._queue_has_pending` records the probed queue names (the real
  probe still runs); the test asserts `weft.spawn.requests` was probed
  (discovery ran) and that no reserved queue name was. The witness is a
  wrapper on a production helper, not a dependency primitive; it is
  recorded here as the remaining coupling, chosen over retaining the cursor
  assignments.
- Controls (disposable worktree; each restored): `discovery_due = True` in
  `MultiQueueWatcher._update_active_queues` fails the first test,
  `assert ['<tid>'] == []`; `_mark_pending_messages_prechecked` as a no-op
  fails the second, `reactor evidence did not match before timeout
  (timeout=5.0, turns=85, latest=[])`; removing the reserved-name exclusion
  from `Manager._queue_counts_as_wait_activity` fails the third at
  `reserved_names.isdisjoint(probed)` (and leaves the reserved-custody idle
  test green).

Task 7: dropped (cross-family round 1, F4); not executed, number kept.

Task 8 (H2): stopped at its Gate; not landed. The implementation was
written and its two tests passed (`test_harness_teardown_warns_on_unconfirmed_manager_stop`,
`test_harness_teardown_warns_on_pids_surviving_before_force_termination`;
control: both logging calls disabled, both tests fail; the four
`stop_manager` doubles changed to `(True, None)`). The Gate run, the full
fast suite with `-rP` so passed tests' captured logs print
(`6037 passed, 47 skipped`), showed the warning from tests other than the
two injected-failure tests, which the Gate names as a stop condition:
- phase `stop_manager`, message `Manager <tid> stop is unconfirmed; no host
  PID is available for --force`, in about 20 tests across the suite, among
  them `test_background_residents_recover_without_manager_checkpoint`,
  `test_cold_chain_contracts_removed_intermediate_snapshot`,
  `test_dispatch_cleanup_scope_clears_after_exception[explicit_public]`,
  `test_harness_cleanup_manager_records_include_startup_log_tids`,
  `test_live_command_success_seals_public_result_before_completion[inherited-output]`,
  `test_manager_autostart_active_launch_blocks_duplicate_restart`,
  `test_missing_snapshot_crash_preserves_custody_and_descendant_progress[False]`
  and `[True]`, `test_monitor_snapshot_and_stop_failures_do_not_replace_primary_outcome`,
  `test_register_from_json_routes_manager_tids_to_worker_tracking`,
  `test_reservation_before_checkpoint_crash_requeues_exact_request_once`,
  `test_reserved_policy_keep_on_error`, `test_run_work_item_executes_payload`,
  `test_task_monitor_custom_processor_exception_is_a_failed_cycle`,
  `test_wait_for_completion_timeout_includes_tid_debug_snapshot`
  (attribution read from xdist `-rP` sections, so approximate);
- phases `stop_active_managers` and `cleanup`, real PIDs surviving before
  force termination, in
  `test_postspawn_no_pid_checkpoint_preserves_unknown_frontier_and_stalls_new_root`.
  Reading the phases: the `stop_manager` leads are unconfirmed stops of
  Manager TIDs the harness knows without a host PID (inline, fake, or
  already-exited Managers); none is shown to be a live process after
  teardown, so none is a located leak. The survivor lead is
  pre-escalation; whether those PIDs outlive `_terminate_registered_pids`
  is not shown. Per the Gate and [TS-0] the warning is not suppressed or
  narrowed to pass, and the change does not land; the diff is kept at the
  session scratchpad (`series/impl/plan9/task8_stopped.patch`) with the
  attribution table (`series/impl/plan9/task8_gate_attribution.txt`).
  Open for the owner: whether an unconfirmed stop of a PID-less Manager
  record is a reportable teardown event at all (if not, the warning needs
  a narrower trigger defined by the owner, which this plan does not give),
  and the owner path of the survivor lead in the family-recovery test.

Task 9 (H3): `test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
uses `with WeftTestHarness() as harness:` per iteration; the `_closed` and
`_tempdir` branches and the swallowed `PermissionError` are gone. The
`env = os.environ.copy()` and `env["WEFT_MANAGER_REUSE_ENABLED"] = "1"`
lines are unchanged in content but now one indent deeper, because they must
run inside the harness scope; see the Deviation Log. The sibling
`test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
is untouched. Local control (disposable worktree): a `raise
RuntimeError("forced failure inside the harness body")` in the loop body
surfaces with the note `Pre-cleanup harness diagnostics: <path>`. The test
passes (18.05 s on macOS).

Task 10 (H4, H5): `"WEFT_TEST_MODE": "1"` removed from `_patch_environment`
(no remaining reference in the tree); the three probes in
`tests/test_harness_registration.py` now use `WEFT_MANAGER_LIFETIME_TIMEOUT`
(the set value is `str(harness._manager_timeout)` during rollback, and the
original value after restore). `test_serve_restarts_singleton_services_without_duplicates`
sets `WEFT_LIVENESS_MONITOR_ENABLED=1`, waits for one live
`INTERNAL_SERVICE_KEY_LIVENESS_MONITOR` service (three distinct service
TIDs), and kills and replaces it in the restart loop. Harness-wide
defaults are unchanged. Control (disposable worktree): dropping the
`if self._liveness_monitor_enabled:` append in
`Manager._reconcile_managed_services` fails the test, `Timed out waiting
for exactly one live service _weft.service.liveness_monitor; live=[];
all=[]`. Harness suite plus the serve test: `62 passed`; the serve tests
passed three further runs.

Final gates (after Tasks 9 and 10, on top of `fc0c0f84`; `bin/pytest-worker-count`
→ 12): fast suite `6035 passed, 47 skipped`; `pytest -m ""`
`6036 passed, 58 skipped`; `./bin/pytest-pg --all` `5988 passed, 35 skipped`;
release tests `47 passed`; mypy `Success: no issues found in 483 source
files`; `ruff check .` `All checks passed!`; `ruff format --check tests`
`290 files already formatted`; `bin/check-doc-paths` OK; `git diff --check`
clean; `tests/specs/test_plan_metadata.py` `4 passed`. Earlier
`./bin/pytest-pg tests/tasks/test_multiqueue_watcher.py`: `87 passed`,
including both T3 parameterizations. Linux and Windows CI not run from
this session.

Closeout (Task 11): Status stays `draft` and the index row stays `draft`.
Open: Task 2 Step 4 (owner-run baseline measurement; Done-when also needs
the Windows `cli` jobs green with the factor), Task 8 (stopped at its Gate;
owner decision on the warning's trigger and the survivor lead), T3
(retained). Flip the plan and its row to `completed` when those close.
