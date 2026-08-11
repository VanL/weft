# Subprocess Coverage Collection Plan

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]
Superseded by: none

Class: 3+P. The implementation is a multi-surface repository-tooling change
across coverage configuration, CI artifact policy, a firing acceptance probe,
and testing guidance. The `+P` modifier applies because the slice materially
changes a future CI verification gate. It does not change product behavior or
the normative testing contract, so it is not Class 5. Per [DOM-15], `+P`
raises the process bar to effective Class 5 treatment.

Plan type: implementation against the existing spec. Spec delta: none. The
current [TS-0] and [TS-1] contract already requires tests to drive the real
subprocess CLI surface and places CLI behavior under `tests/cli/`; this slice
repairs measurement of that existing behavior. Promotion strategy: N/A.
Hardening: N/A. The work does not change a runtime contract, durable execution
path, public API, persistence or cleanup lifecycle, or one-way door.

## 1. Goal

Make project coverage include Python code executed by subprocess-based CLI
tests, preserve the current xdist and three-slice merge topology, and prevent
CI from publishing a combined report unless every coverage slice completed.
The result should make the report an honest measurement of the tests that ran,
not raise coverage by adding tests or changing a threshold.

## 2. Source Documents

- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
  [TS-0], [TS-1] governs the real subprocess CLI harness, coverage policy, and
  ownership of CLI behavior tests.
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
  [DOM-15] governs the Class 3+P classification and effective Class 5 process
  treatment.
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
  governs real-process proofs and coverage guidance.
- [`docs/agent-context/runbooks/adversarial-acceptance-probes.md`](../agent-context/runbooks/adversarial-acceptance-probes.md)
  requires a firing probe that fails when coverage configuration or shard
  completeness regresses.
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
  requires boundary validation, deterministic tests, and repository-managed
  tooling.
- [pytest-cov 7 subprocess guidance](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html)
  identifies Coverage.py's native patch mechanism as the supported replacement
  for pytest-cov's removed subprocess support.

No historical plan defines the desired behavior. The governing intent is
already active in Spec 08.

## 3. Spec Baseline

- `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2`:
  `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1] at plan
  authoring time.
- Worktree qualification: Spec 08 also had concurrent, user-owned uncommitted
  edits that remove backticks from three path examples under [TS-2]. Those
  edits do not change [TS-0], [TS-1], or this plan's intended behavior. The
  implementation must preserve them.
- Promotion baseline: N/A because there is no proposed normative spec delta.
  Any Spec 08 edit in this slice is limited to implementation mapping and the
  reciprocal `## Related Plans` link.

### Process baseline

- `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2` plus the current worktree
  diff for `docs/agent-context/runbooks/testing-patterns.md`. At authoring,
  that user-owned diff removes backticks from the `tests/property` path example
  and does not overlap the `## Coverage Policy` text proposed below.
- `docs/agent-context/context.index.yaml` is clean at the same commit and has
  `updated_at: 2026-07-17`. Because this slice materially changes a listed
  canonical runbook, the process-doc timestamp must advance without changing
  the read order, roles, or document inventory.

### Proposed process delta

This is the exact text to insert in
`docs/agent-context/runbooks/testing-patterns.md` under `## Coverage Policy`,
after the opening project/patch-coverage paragraph:

```markdown
Python paths exercised only through real subprocesses must be measured through
Coverage.py's native subprocess patch. With pytest-cov 7 or later, configure
`[tool.coverage.run] patch = ["subprocess"]`; do not move a real subprocess test
in-process to improve its measured percentage. A per-slice coverage database is
publishable only when the test command exits zero, no uncombined suffix data
files remain, and the combine job verifies the complete expected set of data
files and success markers before generating reports.
```

Exact `docs/agent-context/context.index.yaml` change:

```yaml
updated_at: 2026-08-10
```

The process delta is part of the `+P` review target. It does not change the
product specification or create a new normative Spec 08 requirement.

## 4. Current State And Root Cause

The report materially undercounts subprocess CLI execution, so the listed
figures are not reliable actual coverage. Some uncovered lines will remain
after collection is fixed; the evidence does not claim that every low number is
artificial.

Current execution and merge path:

1. `tests/conftest.py::run_cli()` launches
   `python -m weft.cli ...` with `subprocess.run()`, usually from an isolated
   temporary project root.
2. pytest-cov measures the pytest worker, but the current repository has no
   Coverage.py subprocess patch configured. `coverage debug config` reports
   `patch: -none-`.
3. The Ubuntu/Python 3.13 coverage matrix writes one base data file per slice:
   `.coverage.cli`, `.coverage.core-commands`, and `.coverage.remaining`.
4. The coverage job downloads those artifacts and combines them before
   producing terminal and XML reports.

Evidence that distinguishes collection from merge:

- Successful CI run `30671400220`, coverage job `91291225042`, downloaded all
  three artifacts, logged `Combined 3 files`, and then printed the reported
  zero/low CLI numbers. The cross-slice merge completed.
- A local baseline run of `tests/cli/test_cli.py` passed while
  `weft/cli/__main__.py` remained at 0%, reproducing the missing child-process
  data.
- The locked environment contains pytest-cov 7.1.0 and Coverage.py 7.15.4.
  pytest-cov 7 removed its old subprocess support and delegates this behavior
  to Coverage.py's patch facility.
- A local patch-only probe using `[run] patch = subprocess`, xdist with two
  workers, a relative `COVERAGE_FILE`, and CLI tests that change working
  directories produced one consolidated base file and measured both
  `weft/__main__.py` and `weft/cli/__main__.py` at 100%.
- Coverage.py automatically enables parallel data files when the `subprocess`
  patch is active. An explicit `parallel = true` setting is therefore
  redundant.

There is a separate report-integrity gap. Coverage artifacts are uploaded with
`if: always()`, and the combine job also runs after failed matrix jobs. A data
file can exist even when its pytest slice did not finish. `coverage combine`
accepts whatever compatible files it finds, so a partial report can look like
a valid combined report unless CI separately proves slice completion.

## 5. Context And Key Files

### Files to modify

- `pyproject.toml`: add the native Coverage.py subprocess patch and register the
  new extensionless Python checker with Ruff.
- `bin/check-coverage-shards`: add a small stdlib-only repository checker for
  the exact three data files and their completion markers.
- `.github/workflows/test.yml`: create and upload a completion marker only
  after each coverage pytest slice succeeds; run the checker before combine.
- `tests/specs/test_coverage_collection_policy.py`: add configuration,
  real-subprocess, xdist, checker, and workflow-wiring acceptance probes.
- `tests/specs/test_ruff_policy.py`: add the checker to the closed
  `EXTENSIONLESS_PYTHON` inventory and prove Ruff discovers it.
- `docs/specifications/08-Testing_Strategy.md`: add only a non-normative
  implementation mapping for [TS-0]/[TS-1] and maintain the plan backlink.
- `docs/agent-context/runbooks/testing-patterns.md`: explain the supported
  subprocess collection mechanism and the complete-shard rule.
- `docs/agent-context/context.index.yaml`: advance only `updated_at` for the
  material canonical-runbook revision.
- `docs/lessons.md`: record the pytest-cov 7 subprocess-collection pitfall and
  the evidence needed to distinguish collection from merging.
- This plan and `docs/plans/README.md`: record progress, review findings,
  deviation state, and final evidence.

### Read before editing

- `tests/conftest.py::run_cli()` to preserve the real CLI process boundary and
  environment setup.
- `.github/workflows/test.yml` coverage matrix, artifact upload, combine, and
  Codecov steps.
- `pyproject.toml` pytest and developer-tool configuration.
- `tests/specs/test_ruff_policy.py` and
  `tests/specs/test_plan_metadata.py` for existing repository-policy test
  style.
- Coverage.py's installed `coverage/config.py` and pytest-cov's installed
  xdist controller only if implementation behavior differs from the grounded
  probe. Do not reimplement their data-combination logic.

### Shared paths and ownership

- Coverage.py owns Python child startup and exit-time data writes.
- pytest-cov owns pytest worker and xdist data collation into the slice's base
  `COVERAGE_FILE`.
- `.github/workflows/test.yml` owns the three coverage slices and their
  cross-slice artifact merge.
- `bin/check-coverage-shards` owns the expected data/marker inventory used by
  both the per-slice post-pytest check and the combine job. The workflow must
  call it rather than duplicate its validation logic in shell.
- `tests/conftest.py::run_cli()` remains the owner of CLI subprocess execution.
  Do not move CLI tests in-process to make coverage easier.

### Comprehension checks before implementation

The implementer should be able to answer both questions before editing:

1. Why does `Combined 3 files` prove that the observed CLI zeros are not, by
   itself, a cross-slice merge failure?
2. Why is a data-file existence check insufficient to prove that a coverage
   slice completed successfully?

## 6. Invariants And Constraints

- Preserve real subprocess CLI execution. Do not replace `run_cli()` with
  Typer's in-process test runner or direct function calls.
- Use Coverage.py's native `patch = ["subprocess"]`. Do not add `sitecustomize`,
  ad hoc `COVERAGE_PROCESS_START` injection, shell wrappers, or a custom child
  tracer.
- Do not add an explicit `parallel = true` unless a new failing probe proves
  the locked Coverage.py version no longer enables it for the subprocess
  patch.
- Preserve xdist and the three named coverage slices: `cli`,
  `core-commands`, and `remaining`.
- Preserve one uploaded base data file per successful slice. Do not widen the
  artifact path to every `.coverage*` file unless a failing probe shows that
  pytest-cov stopped folding worker/child suffixes into the base file.
- A completion marker is written only after the coverage pytest command exits
  zero and the checker proves the base data file exists with no uncombined
  `.coverage.<slice>.*` suffix files. Existing data from a failed or interrupted
  slice is diagnostic, not proof of completeness.
- The combine job must fail before `coverage combine`, `coverage xml`, and
  Codecov upload if any expected data file or completion marker is missing.
- The checker must reject an unexpected coverage data file or completion
  marker. Silent expansion of the slice inventory is not allowed.
- Non-coverage matrix failures must not automatically hide a complete report
  from the three designated coverage jobs. Completeness is established by the
  three markers, not the aggregate result of all OS/Python jobs.
- Use only the existing locked development dependencies and the stdlib. No new
  package or lockfile change is expected.
- The new extensionless Python tool must stay in both closed Ruff inventories:
  `[tool.ruff].extend-include` and
  `tests/specs/test_ruff_policy.py::EXTENSIONLESS_PYTHON`. It must be executable
  (`100755`) and discovered by the real Ruff policy probe.
- Do not change coverage thresholds in this slice. First obtain one clean CI
  baseline after measurement is corrected; threshold work is a separate
  policy decision.
- Do not describe a higher percentage as improved test coverage. The immediate
  change is improved measurement fidelity.
- Preserve patch-coverage policy and all product/runtime behavior.
- The probe must use a real Python child process and real coverage output. A
  mocked `subprocess.run`, parsed config alone, or assertion on command text is
  not sufficient proof.
- Python processes that outlive their test or are force-killed may not flush
  usable coverage data. Tests must wait for and clean up their children before
  the slice completes.
- Preserve all unrelated worktree edits. No drive-by config, CI, test, or docs
  cleanup belongs in this slice.
- Before editing any already-dirty shared file, inspect
  `git diff -- <path>`, apply a narrow patch outside the existing hunk, and
  inspect the path-specific diff again. Stop if new user work overlaps the
  intended hunk. Never use `git checkout`, `git restore`, or a whole-file
  rewrite to clean `docs/specifications/08-Testing_Strategy.md` or
  `docs/agent-context/runbooks/testing-patterns.md`.
- Independent review is required before implementation because the `+P`
  modifier changes a future verification gate. A fresh-eyes implementation
  review is also required before completion.

## 7. Acceptance Criteria

1. A child-only sentinel line executed by `sys.executable` from a pytest test
   appears as executed in Coverage.py JSON output.
2. The sentinel probe passes when its nested pytest run uses xdist and a
   working directory outside the repository root.
3. The probe leaves one requested base coverage data file after pytest-cov
   collation and no suffixed child/worker files; it does not require the
   workflow to upload suffix files.
4. Existing CLI coverage shows `weft/cli/__main__.py` as executed when a
   subprocess CLI test runs. No fixed percentage is required for large modules
   such as `app.py` or `validate_taskspec.py` because their statements may
   change independently.
5. In `slice` mode, the checker exits zero only when the named expected slice
   has its base data file and no suffixed data files.
6. In `merged` mode, the checker exits zero only for the exact three data files
   plus exact three success markers.
7. Missing data, a missing success marker, a leftover suffix file, and an
   unexpected coverage shard each produce exit one with a concise diagnostic.
   Invalid arguments, an unknown slice, and a nonexistent/unreadable artifact
   directory produce exit two with a one-line diagnostic and no traceback.
8. CI creates a success marker after, and only after, each designated coverage
   pytest command and its local suffix check succeed; the artifacts contain the
   base data and marker.
9. CI invokes the shard checker before combination and cannot create or upload
   `coverage.xml` after a completeness failure.
10. The three current data artifacts still combine successfully into terminal
   and XML reports.
11. No runtime source, public CLI, dependency version, coverage threshold, or
    normative specification requirement changes.

## 8. Dependency-Ordered Tasks

Implementation authorization gate: the user's request to write this plan does
not authorize Tasks 1–5. Before Task 1, obtain explicit direction to implement
this plan. A later instruction such as “implement this plan” is sufficient
approval for the named `pyproject.toml` and `.github/workflows/test.yml` edits;
anything narrower is not. Do not infer CI/project-config approval from “continue”
when the active request is still plan authoring.

### Task 1: Add failing coverage-policy acceptance probes

Files:

- add `tests/specs/test_coverage_collection_policy.py`
- read `pyproject.toml`, `.github/workflows/test.yml`, and
  `tests/specs/test_ruff_policy.py`

Implementation detail:

1. Capture the authoring Backstitch report outside the repository using the
   exact roots and command in Section 9. This is the before-side of the keyed
   traceability comparison; do it before adding code citations or spec mapping.
2. Mark the module `pytest.mark.shared`; it is repository-tooling policy and
   must remain backend-neutral.
   Its module docstring must cite the full, unambiguous path
   `docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]`, not bare
   section IDs that collide with TaskSpec's `[TS-*]` namespace.
3. Parse `pyproject.toml` with `tomllib` and require `subprocess` in
   `tool.coverage.run.patch`. This is a structural ownership check, not the
   behavioral proof.
4. Build a tiny package and pytest file under `tmp_path`. The pytest file must
   launch `sys.executable` in a child process; only that child imports and calls
   a function containing a uniquely identified sentinel line.
5. Launch the nested pytest from the temporary project root with a bounded
   120-second timeout and this complete argument shape (construct it as an argv
   list, never a shell string):

   ```python
   [
       sys.executable,
       "-m",
       "pytest",
       str(probe_test_path),
       "--override-ini=addopts=",
       "-n",
       "2",
       "--dist",
       "load",
       f"--cov={sentinel_package_name}",
       f"--cov-config={repo_root / 'pyproject.toml'}",
       f"--cov-report=json:{coverage_json_path}",
   ]
   ```

   `repo_root / "pyproject.toml"`, `coverage_json_path`, and the inner
   `COVERAGE_FILE` must be absolute. The real repository pyproject is what
   flips the probe from red to green; a throwaway config containing the desired
   patch would invalidate the acceptance proof.
6. Sanitize inherited `COVERAGE_FILE`, `COVERAGE_PROCESS_START`,
   `COVERAGE_PROCESS_CONFIG`, `COVERAGE_RCFILE`, `COVERAGE_CORE`,
   `PYTEST_ADDOPTS`, `PYTEST_XDIST_WORKER`, `PYTEST_XDIST_WORKER_COUNT`, and
   `PYTEST_XDIST_TESTRUNUID` in the explicit nested-pytest environment. Then set
   only the unique absolute inner `COVERAGE_FILE`. This prevents outer pytest,
   coverage, or xdist state from satisfying or contaminating the probe; the
   nested pytest-cov controller must establish the serialized config that its
   own child inherits.
7. Assert the nested run exits zero, the JSON report exists, the sentinel line
   is listed in `executed_lines` for the exact child-only source file,
   pytest-cov leaves the requested base data file, and no files match
   `<base-data-name>.*`. Include captured stdout/stderr in assertion failures.
8. Add workflow ownership assertions for the exact slice set, per-slice data
   path, success-marker path, checker invocation before combine, and Codecov
   ordering. Keep them narrow enough to allow unrelated workflow formatting.
9. Add checker contract tests before creating the checker. `slice` mode needs
   exact success, missing base data, leftover suffix data, unknown slice, and
   nonexistent directory cases. `merged` mode needs exact success, missing
   data, missing marker, unexpected data/marker, invalid argv, and nonexistent
   directory cases. Assert the exact 0/1/2 exit-class split, concise stderr,
   and no traceback for anticipated errors.

Red gate:

- The config and behavioral subprocess probes must fail on the baseline because
  no native patch is configured.
- The checker and workflow-completeness probes must fail because neither the
  checker nor markers exist.
- Record these failures in this plan before Task 2. A probe that passes on the
  baseline does not prove the bug and must be redesigned.

Stop and re-evaluate if the probe requires importing Weft in the parent
process, passes without executing the sentinel child, or depends on a fixed
module-wide percentage.

### Task 2: Enable native subprocess collection and add the shard checker

Files:

- modify `pyproject.toml`
- add `bin/check-coverage-shards`
- continue `tests/specs/test_coverage_collection_policy.py`
- modify `tests/specs/test_ruff_policy.py`

Implementation detail:

1. Add:

   ```toml
   [tool.coverage.run]
   patch = ["subprocess"]
   ```

2. Do not add `parallel = true`. The native patch enables it, and the
   behavioral probe must prove that the base file is consolidated correctly.
3. Implement `bin/check-coverage-shards` as a typed, stdlib-only Python tool
   with the repository Python shebang and `from __future__ import annotations`.
   Its module docstring must cite the full path
   `docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]`.
4. Make the file executable and preserve mode `100755`. Add its path in
   alphabetical order to both `[tool.ruff].extend-include` and
   `tests/specs/test_ruff_policy.py::EXTENSIONLESS_PYTHON`. Those are a closed,
   synchronized inventory; do not weaken either policy test.
5. The checker owns the closed expected slice tuple
   `("cli", "core-commands", "remaining")` and exposes two explicit modes:
   `slice <directory> <slice>` and `merged <directory>`.
6. `slice` mode requires `.coverage.<slice>`, rejects
   `.coverage.<slice>.*`, and rejects a slice outside the closed tuple. It does
   not require a success marker because the workflow calls it before creating
   that marker.
7. `merged` mode requires `.coverage.<slice>` and
   `coverage-complete-<slice>` for all three slices. Reject unexpected files
   matching either owned naming family. Ignore unrelated download metadata.
8. Exit zero on the exact set. Exit one with sorted missing/unexpected names on
   a policy mismatch. Invalid invocation, unknown slice, or unreadable/missing
   directory exits two with a concise one-line diagnostic and no traceback.
   Unexpected programming defects retain normal traceback evidence.
9. Keep this a small policy checker. Do not parse coverage databases or
   implement merging.

Green gate:

- Run the new policy test serially and under outer xdist.
- Confirm the child-only sentinel is covered and only the requested base data
  file remains for the nested run.
- Run the focused Ruff policy test, real Ruff discovery, executable-mode check,
  suppression-index check, and mypy on the new checker and policy test.

Stop and re-evaluate if the native patch creates unbounded `.coverage.*`
suffix files after a successful nested pytest run or if it changes normal test
process behavior.

### Task 3: Make CI publication fail closed on incomplete slices

Files:

- modify `.github/workflows/test.yml`
- continue `tests/specs/test_coverage_collection_policy.py`

Implementation detail:

1. In each Ubuntu/Python 3.13 coverage job, chain the current pytest command,
   local suffix check, and marker creation explicitly:

   ```bash
   pytest ... --cov=weft --cov-report=term-missing &&
   python bin/check-coverage-shards slice . "${{ matrix.test_slice }}" &&
   touch "coverage-complete-${{ matrix.test_slice }}"
   ```

   Keep the real pytest argv from the workflow in place of `...`. Do not rely
   only on implicit shell `-e`; the `&&` chain is the marker-order contract.
2. Upload the exact base data file and exact completion marker in the existing
   per-slice artifact. Keep `if: always()` so failed-slice diagnostics can
   still be downloaded. Use this exact `with:` shape so the hidden data file
   remains uploadable:

   ```yaml
   with:
     name: coverage-data-${{ matrix.test_slice }}
     path: |
       .coverage.${{ matrix.test_slice }}
       coverage-complete-${{ matrix.test_slice }}
     if-no-files-found: error
     include-hidden-files: true
   ```

   Marker completeness is still decided by the checker because one of the two
   requested files may exist.
3. After merged artifact download and before `coverage combine`, invoke:

   ```text
   python bin/check-coverage-shards merged coverage-data
   ```

4. Keep `coverage combine coverage-data`, `coverage report`, and
   `coverage xml -o coverage.xml` after the checker. Do not add
   `continue-on-error` to the checker or combine step.
5. Keep Codecov after XML generation. Preserve `fail_ci_if_error: false`; that
   setting concerns remote Codecov availability, not local collection
   completeness.
6. Preserve coverage selection on Ubuntu/Python 3.13 only. This slice does not
   multiply collection across the full OS/Python matrix.

Per-task gate:

- Run the workflow ownership tests.
- Create a local temporary artifact directory with three copied valid coverage
  databases and three markers; run the checker in `merged` mode and
  `coverage combine` against it.
- Remove one marker and prove the checker stops the sequence before XML
  creation.
- Add a suffix file before the local `slice` check and prove the marker is not
  created.

### Task 4: Synchronize implementation guidance and traceability

Files:

- modify `docs/specifications/08-Testing_Strategy.md`
- modify `docs/agent-context/runbooks/testing-patterns.md`
- modify `docs/agent-context/context.index.yaml`
- modify `docs/lessons.md`
- modify this plan if implementation deviates

Implementation detail:

1. Under the existing [TS-0]/[TS-1] discussion, add an
   `_Implementation mapping_` that names:
   `pyproject.toml`, `tests/conftest.py::run_cli()`,
   `tests/specs/test_coverage_collection_policy.py`,
   `bin/check-coverage-shards`, and `.github/workflows/test.yml`.
   Describe current ownership only. Do not add a new normative requirement or
   reference code.
2. Keep this plan in Spec 08's `## Related Plans` section.
3. Apply the exact reviewed `### Proposed process delta` text. Advance only
   `context.index.yaml::updated_at` to `2026-08-10`; do not change its read
   order, roles, or document inventory.
4. Add a concise lesson: a successful cross-slice combine proves only that
   files merged; use a child-only sentinel to determine whether subprocess
   execution was collected, and use completion evidence to determine whether
   every shard finished.
5. Before editing Spec 08 or the runbook, capture their path-specific diffs.
   Apply narrow patches outside the existing [TS-2] and `tests/property`
   path-format hunks. Re-read both diffs afterward and stop if the pre-existing
   hunks changed or new user work overlaps the intended insertion point.
6. Run plan metadata, Spec hygiene, DOM-15, document-path, and traceability
   checks.

This task is documentation synchronization, not spec promotion. If review
finds that the desired mechanism must become a new normative testing
requirement, stop, reclassify the plan as 5+P, add an exact proposed spec delta
and promotion strategy, and obtain a new independent review before continuing.

### Task 5: Reproduce the real CI topology and close the plan

Files:

- update this plan's review, deviation, and completion sections
- re-verify the existing `docs/plans/README.md` row throughout; change only its
  `draft` status after the slice is implemented and committed at the user's
  direction. The row and corpus count already exist at plan-authoring time.

Implementation detail:

1. Run the targeted policy probe on the repo-managed toolchain.
2. Run a focused real CLI coverage command with a unique data file and verify
   `weft/cli/__main__.py` is covered.
3. Reproduce the three Ubuntu coverage slice commands locally, using three
   unique data files and the same targets/marker rule as CI. A lower worker
   count is allowed locally only if the nested acceptance probe has already
   proven xdist collection; record the difference.
4. Run `bin/check-coverage-shards merged`, combine exactly those three data
   files, and generate terminal and XML reports.
5. Compare the corrected report with the last known CI report. Record the
   collection delta for subprocess-heavy modules, but do not encode the
   observed percentages as acceptance thresholds.
6. Run the default suite, lint, formatter, mypy, plan metadata, spec hygiene,
   and traceability gates.
7. Obtain a clean fresh-eyes implementation review. Disposition every finding
   in the review log.
8. Commit only if the user asks. Per repository policy, do not set this plan to
   `completed` or claim it ready to land while the implementation remains
   uncommitted. For an uncommitted review handoff, keep `Status: draft` and
   list every changed file explicitly.

## 9. Verification Commands

Run after loading `.envrc`; use only the in-repo environment:

```bash
./.venv/bin/python -m pytest -q -n 0 tests/specs/test_coverage_collection_policy.py
./.venv/bin/python -m pytest -q -n 2 --dist load tests/specs/test_coverage_collection_policy.py
./.venv/bin/python -m pytest -q -n 0 tests/specs/test_ruff_policy.py
./.venv/bin/ruff check pyproject.toml bin/check-coverage-shards tests/specs/test_coverage_collection_policy.py
./.venv/bin/ruff format --check bin/check-coverage-shards tests/specs/test_coverage_collection_policy.py
./.venv/bin/mypy bin/check-coverage-shards tests/specs/test_coverage_collection_policy.py --config-file pyproject.toml
test -x bin/check-coverage-shards
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/specs/test_test_audit_policy.py
./.venv/bin/python -m pytest
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft tests integrations/weft_django extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
bin/check-dom15-fixtures
bin/check-doc-paths
git diff --check
```

Current three-slice local reproduction template. Cross-check its target lists
against the workflow before running; any workflow change must be reflected in
the closed policy test before this command is treated as evidence:

```bash
set -euo pipefail
coverage_probe_dir=$(mktemp -d)

COVERAGE_FILE="$coverage_probe_dir/.coverage.cli" \
  ./.venv/bin/python -m pytest -v --tb=short -m "not slow" tests/cli \
  --override-ini="addopts=-ra -q --strict-markers -n logical --dist load" \
  --cov=weft --cov-report=term-missing &&
./.venv/bin/python bin/check-coverage-shards slice "$coverage_probe_dir" cli &&
touch "$coverage_probe_dir/coverage-complete-cli"

COVERAGE_FILE="$coverage_probe_dir/.coverage.core-commands" \
  ./.venv/bin/python -m pytest -v --tb=short -m "not slow" \
  tests/commands tests/core \
  --override-ini="addopts=-ra -q --strict-markers -n logical --dist load" \
  --cov=weft --cov-report=term-missing &&
./.venv/bin/python bin/check-coverage-shards slice \
  "$coverage_probe_dir" core-commands &&
touch "$coverage_probe_dir/coverage-complete-core-commands"

COVERAGE_FILE="$coverage_probe_dir/.coverage.remaining" \
  ./.venv/bin/python -m pytest -v --tb=short -m "not slow" \
  tests/architecture tests/context tests/helpers tests/shell tests/specs \
  tests/system tests/tasks tests/taskspec tests/test_harness_registration.py \
  --override-ini="addopts=-ra -q --strict-markers -n logical --dist load" \
  --cov=weft --cov-report=term-missing &&
./.venv/bin/python bin/check-coverage-shards slice \
  "$coverage_probe_dir" remaining &&
touch "$coverage_probe_dir/coverage-complete-remaining"

./.venv/bin/python bin/check-coverage-shards merged "$coverage_probe_dir"
COVERAGE_FILE="$coverage_probe_dir/.coverage" \
  ./.venv/bin/coverage combine "$coverage_probe_dir"
COVERAGE_FILE="$coverage_probe_dir/.coverage" \
  ./.venv/bin/coverage report
COVERAGE_FILE="$coverage_probe_dir/.coverage" \
  ./.venv/bin/coverage xml -o "$coverage_probe_dir/coverage.xml"
```

The positive template must exit zero and create `coverage.xml`. The focused
checker tests separately create an incomplete temporary directory, invoke the
same `merged` command, require its nonzero result, and assert that a chained XML
step did not run and no XML file exists. All ordinary commands above, including
`bin/check-doc-paths`, must exit zero. Backstitch is the only baseline-debt gate
with an expected nonzero aggregate result; its keyed comparison below must exit
zero.

Backstitch has known repository debt and no Weft-local configuration. Capture
the before-report in Task 1 and the after-report at closeout with the same
explicit-root command:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-coverage-backstitch-before.json || test $? -eq 1

../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-coverage-backstitch-after.json || test $? -eq 1
```

At authoring, the explicit-root report contains 45 errors, 1,023 warnings, and
605 infos, with no issue keyed to this plan. Aggregate counts are informational.
The closeout gate compares error/warning tuples by severity, code, path,
section, symbol, and message for every touched file and rejects any addition:

```bash
./.venv/bin/python - \
  /tmp/weft-coverage-backstitch-before.json \
  /tmp/weft-coverage-backstitch-after.json <<'PY'
from collections import Counter
import json
import sys

touched = {
    ".github/workflows/test.yml",
    "bin/check-coverage-shards",
    "docs/agent-context/context.index.yaml",
    "docs/agent-context/runbooks/testing-patterns.md",
    "docs/lessons.md",
    "docs/plans/2026-08-10-subprocess-coverage-collection-plan.md",
    "docs/plans/README.md",
    "docs/specifications/08-Testing_Strategy.md",
    "pyproject.toml",
    "tests/specs/test_coverage_collection_policy.py",
    "tests/specs/test_ruff_policy.py",
}


def keyed_issues(path: str) -> Counter[tuple[object, ...]]:
    payload = json.loads(open(path, encoding="utf-8").read())
    return Counter(
        (
            issue.get("severity"),
            issue.get("code"),
            issue.get("path"),
            issue.get("section_id"),
            issue.get("symbol"),
            issue.get("message"),
        )
        for issue in payload["issues"]
        if issue.get("severity") in {"error", "warning"}
        and issue.get("path") in touched
    )


added = keyed_issues(sys.argv[2]) - keyed_issues(sys.argv[1])
for issue, count in sorted(added.items(), key=repr):
    print(count, issue)
raise SystemExit(bool(added))
PY
```

If implementation changes the touched-file set, update the set before running
the gate. If the sibling Backstitch checkout is unavailable, record that as an
unpassed tooling blocker; metadata and spec-hygiene tests are not a substitute.

The exact three-slice reproduction should use the current commands copied from
`.github/workflows/test.yml` at implementation time, not stale commands copied
from this plan. Store its artifacts in a temporary directory outside the
repository or with unique task-specific names, and clean them after recording
the result.

## 10. Rollout, Observation, And Rollback

Rollout is one atomic CI/tooling slice: config, firing test, checker, workflow,
and guidance land together. Splitting them would create either unguarded
collection behavior or a workflow that calls a missing checker.

On the first CI run after landing, verify:

- all three coverage artifacts contain their expected data file and success
  marker;
- the combine job passes the checker before logging `Combined 3 files`;
- `weft/cli/__main__.py` is measured as executed;
- subprocess-heavy CLI modules rise as expected without duplicate-file or
  source-path warnings;
- Codecov receives one XML report generated from all three completed slices.

Rollback is low risk because no product code or persisted data changes, but the
two failure axes are separable:

- If native child collection is unstable, revert the coverage patch, child-only
  probe, and collection-specific mapping/guidance. Keep the marker/checker
  publication gate if its own tests and CI path remain sound; it improves report
  honesty independently of subprocess tracing.
- If shard publication gating is faulty, revert the marker/checker/workflow and
  completeness-specific guidance. Keep subprocess collection if the child-only
  probe and real CLI report remain sound.
- Revert both groups only when evidence shows a shared failure or when retaining
  either would make the documented coverage policy false.

Before either rollback, retain the failed CI logs and coverage artifact
inventory so the next plan can distinguish collection, flush, and merge
failures. Update Spec 08's implementation mapping and the process guidance to
describe only the mechanism that remains.

## 11. Risks And Counterarguments

| Concern | Decision and evidence |
| --- | --- |
| The low numbers might be real missing tests. | Some uncovered lines will remain real. The child-only sentinel and `__main__.py` result isolate the measurement defect without claiming every low module is well tested. |
| The existing combine step should be replaced. | No. A successful three-file CI combine and the patch-only xdist probe show that cross-slice and worker collation work. Replacing them adds risk without evidence. |
| An absolute `COVERAGE_FILE` is required because CLI tests change cwd. | The patch-only local probe succeeded with a relative file and temp working directories because pytest-cov consolidates child data into its controller-owned base file. The acceptance probe still uses an absolute path to keep its own artifacts isolated. |
| `parallel = true` should be configured explicitly. | Coverage.py automatically enables parallel mode for the subprocess patch. Duplicating the setting adds no behavior and can obscure the actual coupling. |
| Data-file existence proves a shard ran. | It proves only that some data was written. A failed or interrupted pytest run can leave a valid partial database. Success markers close that ambiguity. |
| A fixed module percentage would be a simpler regression gate. | It would couple collection policy to unrelated statement changes and test additions. A child-only executed-line assertion is the direct, stable proof. |
| The coverage job should run only when every matrix cell passes. | That would hide a complete coverage report because of an unrelated OS/Python failure. Three per-slice completion markers provide the narrower proof. |
| The native patch could trace unwanted Python children. | It will measure Python descendants spawned by covered tests, which is the desired semantics. Non-Python commands are not traced. Unexpected long-lived children or duplicate source paths are stop-and-re-evaluate signals. |

## 12. Out Of Scope

- Adding tests solely to raise the corrected percentage.
- Raising project or patch coverage thresholds.
- Expanding coverage to every OS and Python version.
- Changing Codecov service policy or `fail_ci_if_error`.
- Measuring non-Python subprocesses.
- Refactoring `weft/cli/app.py`, `validate_taskspec.py`, or the CLI harness.
- Upgrading pytest-cov, Coverage.py, pytest, or xdist.
- Repairing unrelated CI, docs, plan-corpus, or worktree changes.

## 13. Independent Review Loop

Before implementation:

1. Give a clean reviewer this plan, Spec 08 [TS-0]/[TS-1], the current
   workflow coverage jobs, `run_cli()`, and the patch-only probe evidence.
2. Ask the reviewer to challenge classification, root-cause evidence,
   unnecessary topology changes, acceptance-probe firing behavior, marker
   semantics, workflow failure ordering, and rollback completeness.
3. Record every finding below as accepted, rejected with evidence, or deferred
   with an owner and follow-up. Re-review after any material change.

Before completion, repeat the review against the implementation diff and
verification output. Completion requires `PASS` or an explicit net-positive
decision with no unresolved correctness or report-integrity finding.

## 14. Review Log

| Review | Finding | Disposition | Plan change |
| --- | --- | --- | --- |
| Reviewer A F1 [P1] | New extensionless checker was absent from Ruff's closed inventories. | Accepted. | Added `pyproject.toml` and `test_ruff_policy.py` inventory edits, executable mode, focused policy and suppression gates. |
| Reviewer A F2 [P1] | `+P` lacked a process baseline and exact process-doc delta. | Accepted. | Added the dirty runbook baseline, exact process text, and exact `context.index.yaml` timestamp change. |
| Reviewer A F3 [P1] | Plan request did not authorize later project-config and CI edits. | Accepted. | Added a pre-Task-1 implementation authorization stop gate. |
| Reviewer A F4 [P1] | Nested probe command and outer-state isolation were not executable enough. | Accepted. | Added exact argv, repo pyproject binding, absolute paths, env removal set, xdist IDs, 120-second timeout, and suffix assertion. |
| Reviewer A F5 [P1] | Author fresh-eyes review was not separately recorded. | Accepted. | Added the distinct author pass below; independent round-two review remains separate. |
| Reviewer A F6 [P2] | Root-cause sentence implied every low line was a collection artifact. | Accepted. | Narrowed the claim to material subprocess undercount and retained real uncovered lines. |
| Reviewer A F7 [P2] | Downstream exact-file upload could hide uncombined per-slice suffix data. | Accepted. | Added checker `slice` mode before marker creation and a leftover-suffix firing test. |
| Reviewer A F8 [P2] | Dirty-file preservation was not operational. | Accepted. | Added before/after path-specific diff checks, narrow-patch requirement, and overlap stop gate. |
| Reviewer A F9 [P2] | Three-slice and policy verification was delegated to rediscovery. | Accepted. | Added current exact three-slice template, pass criteria, Ruff policy, DOM-15, suppression, and keyed traceability gates. |
| Reviewer A F10 [P2] | All-or-nothing rollback coupled independent failure axes. | Accepted. | Split collection rollback from completeness-gate rollback and required mappings to follow retained behavior. |
| Claude P1-1 [P1] | Independently reproduced the missing extensionless Ruff inventory blocker. | Accepted; converges with Reviewer A F1. | Same inventory, executable-mode, and focused-policy corrections. |
| Claude P2-2 [P2] | `--cov-config` had to name the real repository pyproject for a valid red/green proof. | Accepted. | Exact nested argv now binds the absolute repo pyproject. |
| Claude P2-3 [P2] | Checker exit-two invocation class had no firing probes. | Accepted. | Added invalid argv, unknown slice, and nonexistent-directory exit-two/no-traceback cases. |
| Claude P2-4 [P2] | Dirty shared-file collision guard was under-specified. | Accepted; converges with Reviewer A F8. | Same path-specific diff and stop gates. |
| Claude P2-5 [P2] | Upload edit could accidentally drop `include-hidden-files: true`. | Accepted. | Added the exact two-path artifact `with:` block including the hidden-file flag. |
| Claude P3-6 [P3] | Marker ordering relied on implicit shell fail-fast behavior. | Accepted. | Required an explicit `pytest && slice-check && touch` chain. |
| Claude P3-7 [P3] | Plan count/coalescing bookkeeping needed confirmation. | Already satisfied. | The draft row exists, metadata passes, and coalescing derives from the index. A concurrent plan moved the corpus count from 172 to 173 during review; its row/count update is preserved and requires no edit from this slice. |
| Claude P3-8 [P3] | Task 5 could imply the plan index row is created only at closeout. | Accepted. | Clarified that Task 5 re-verifies the existing row and changes only status at committed closeout. |
| Claude P3-9 [P3] | Consider shortening the Backstitch keyed comparison. | Rejected with evidence. | Effective Class 5 traceability and known global debt require the explicit before/after keyed gate; aggregate counts cannot substitute. |
| Reviewer availability | Claude wrapper preflight reported `AUTH_MISSING`, but the direct read-only CLI was authenticated through the system credential store and completed. | Recorded as a wrapper preflight defect, not a review limitation. | Different-family Claude review is complete; the local operational learning records that actual invocation is the auth proof. |
| Reviewer A F9-R2 [P1] | The first reproduction template set the combine base to `.coverage.combined`, so Coverage would search for `.coverage.combined.*` instead of the three `.coverage.<slice>` inputs; it also lacked shell fail-fast. | Accepted. | Added `set -euo pipefail` and use `$coverage_probe_dir/.coverage` as the combine/report/XML base. |
| Reviewer A round two/three | Round two found F9-R2 in the reproduction template; round three verified the correction. | `PASS`. | `.coverage` is now the combine base and shell fail-fast is explicit. |
| Claude round two | Verified every accepted P1/P2/P3 fix plus process delta, checker modes, three-slice targets, touched set, and split rollback against live repository state. | `PASS`; no new defect. | No further change required. |

## 15. Fresh-Eyes Review

Author pass: complete after the first review dispositions. Before external
review, the author found and corrected three defects: a nonexistent bare
`backstitch` command, ambiguous bare `[TS-0]`/`[TS-1]` citations, and vague
coverage-environment sanitation. After applying the external findings, the
author re-read the classification, baselines, exact process delta, checker
modes, workflow order, dirty-tree guards, verification template, and rollback
axes. No additional blocker was found.

Residual risk: the nested xdist probe adds process-start cost on every matrix
platform. It is intentionally tiny, uses two workers, and has a 120-second
bound; a timeout or platform-only flake is a stop-and-re-evaluate signal, not a
reason to weaken the child-only assertion.

Status: author fresh-eyes `PASS`; independent Reviewer A `PASS`; independent
different-family Claude `PASS`. The plan review gate is complete. Implementation
still must not begin until the authorization gate at the start of Section 8 is
satisfied.

## 16. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## 17. Completion Evidence

Plan-authoring evidence on 2026-08-10:

- Plan metadata and Spec hygiene: 8 passed.
- `bin/check-dom15-fixtures`, `bin/check-doc-paths`, and `git diff --check`:
  passed.
- Explicit-root Backstitch: retained the known 45 errors, 1,023 warnings, and
  605 infos; the before/after keyed comparison added zero error/warning finding
  on the plan's touched set.
- Author fresh-eyes, independent Reviewer A, and different-family Claude plan
  reviews: passed after all findings were dispositioned and rechecked.

Implementation remains pending and unauthorized until the Section 8 gate is
satisfied. At implementation closeout, record the red/green acceptance proof,
three-slice artifact inventory, combine output, corrected CLI measurement,
full verification commands, fresh-eyes implementation review result, commit
identifier if the user requested a commit, and any remaining unrelated
worktree changes.
