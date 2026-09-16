# Single CI Release Gate

Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-4]; explicit user requirements in the 2026-09-16 session
Superseded by: none

Class: 4+P — this materially changes the repository verification and publication
gate, and publication ordering is a one-way boundary. The effective class is 5.

## Goal

Make `.github/workflows/test.yml` the only owner of automated test execution.
It must run the complete main suite first, then run every first-party extension
suite in parallel. The release helper must wait for that workflow to succeed
before it pushes any requested release tag. Tag-triggered release gates then
verify, build, and publish without rerunning tests or occupying runners while CI
is still in progress.

## Source Documents

- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
  [TS-4] owns the staged CI and release-gate contract.
- [`README.md`](../../README.md) is the current release-flow contract.
- [`docs/plans/2026-04-06-release-gated-tag-workflow-plan.md`](./2026-04-06-release-gated-tag-workflow-plan.md)
  records the existing tag-gate rationale; its duplicated release suites are
  superseded only by this plan's shared-CI proof.
- [`../simplebroker/bin/release.py`](../../../simplebroker/bin/release.py),
  [`../simplebroker/.github/scripts/require_green_workflows.py`](../../../simplebroker/.github/scripts/require_green_workflows.py),
  and its release-gate workflows are the requested reference design.
- `AGENTS.md`, `docs/agent-context/decision-hierarchy.md`, and
  `docs/agent-context/runbooks/hardening-plans.md` govern this change.

No product-runtime spec governs package publication. This plan changes the
repository's release-process contract, not Weft runtime behavior.

## Baseline

- Git baseline: `195f43496993e04a88aaec5f4ca0c2e4fd4bb909`.
- Promotion baseline: that Git baseline plus this plan's uncommitted
  `docs/specifications/08-Testing_Strategy.md` [TS-4] contract change.
- `.github/workflows/test.yml` runs the default main matrix and extension jobs
  concurrently; extension jobs skip release commits.
- `.github/workflows/release-gate.yml` reruns all extension suites plus complete
  SQLite and PostgreSQL main suites.
- each package gate invokes `.github/workflows/release.yml`, whose
  `verify-main-test-workflow` job may occupy a runner for up to 50 minutes.
- `bin/release.py` pushes `main`, then immediately pushes the root and every
  unpublished first-party package tag.

## Promoted Contract Delta

Promotion strategy A (text first): [TS-4] was added to the canonical Testing
Strategy before implementation. No Weft runtime behavior changes. The promoted
contract states:

1. `Test` is the sole automated test workflow. Its main stage runs every main
   test, including SQLite slow tests and PostgreSQL-compatible tests, plus the
   static gates. Only a green main stage starts all extension suites in
   parallel.
2. the release helper pushes the release commit to `main` and waits locally for
   the matching protected-branch `push` run of `test.yml`. No release tag is
   pushed on missing, failed, cancelled, or timed-out CI.
3. the release CLI accepts `core`, `django`, `docker`, `macos-sandbox`,
   `microsandbox`, or `all`; defaults to `core`; and pushes only explicitly
   selected unpublished targets. After CI succeeds it refetches `origin/main`,
   revalidates publication and tag state, and creates immutable tags at the
   tested SHA.
4. release workflows do not run pytest and do not poll a still-running Test
   workflow. Their test verification is an immediate, fail-closed check of the
   already completed run.

## Context and Key Files

Files to modify:

- `.github/workflows/test.yml`
- `.github/workflows/release-gate.yml`
- `.github/workflows/release-gate-django.yml`
- `.github/workflows/release-gate-docker.yml`
- `.github/workflows/release-gate-macos-sandbox.yml`
- `.github/workflows/release-gate-microsandbox.yml`
- `.github/workflows/release.yml`
- `bin/release.py`
- `README.md`
- `docs/specifications/08-Testing_Strategy.md`
- `tests/system/test_release_script.py`
- this plan and `docs/plans/README.md`

Read first:

- current versions of every file above
- `tests/system/test_release_script.py` and
  `tests/system/test_release_workflow.py` as contract evidence. The user later
  authorized deleting brittle CI-shape assertions while retaining tests that
  enforce release correctness.
- SimpleBroker's corresponding helper, workflow-status script, workflows, and
  release tests

Comprehension checks before editing:

- Can every `tests/` path and marker run be assigned to exactly one CI job while
  retaining the intentional OS/Python compatibility matrix?
- Does any release tag become reachable before the exact commit's protected-
  branch `push` run of `test.yml` is green?
- Can a package gate publish a package other than the tag that triggered it?

## Invariants and Constraints

- Do not change product/runtime tests. Replace only brittle CI-shape assertions
  with checks for test ownership, ordering, exact release routing, and
  fail-closed publication behavior.
- Every current main, PostgreSQL-compatible, Django, Docker, macOS sandbox, and
  Microsandbox test remains in CI.
- Preserve the multi-OS and multi-Python compatibility matrix. Avoid duplicate
  same-environment release reruns; intentional compatibility matrix overlap is
  not duplication.
- Extension tests start only after all main-stage test and static jobs succeed,
  then run in parallel.
- No tag is pushed before CI success. Recheck mutable publication/tag state
  after the wait and before pushing tags.
- A package-specific tag publishes only its package. Preserve independent
  namespaced package releases. The default target is `core`; `all` is explicit.
- Keep `bin/pytest-pg` as the PostgreSQL test entry point.
- Keep `.github/workflows/release.yml` as the sole package build/publish path.
- Keep exact-SHA checkout and tag-current verification before publication.
- No new dependency, no test-content changes, no Weft runtime change, and no
  unrelated CI refactor.

## Hardening

- Hidden coupling: `bin/release.py` currently calculates tag actions before the
  release commit exists. It must refresh external publication and remote-tag
  state after CI completes and calculate the final immutable action then.
- Fatal failures: missing/failed/cancelled CI, timeout, moved tag, changed
  publication state, or a release SHA absent from `origin/main` all stop before
  tag creation or publication.
- Do not mock the workflow graph as proof. Parse the YAML and exercise the real
  release helper's pure/status logic through the existing tests.
- Retagging: `--retag` may only replace an unpublished remote tag after the
  post-CI refresh proves it still has the same stale identity observed for this
  invocation; otherwise fail closed. Newly created release tags are immutable.
- Rollout: land helper and workflow changes atomically. Using a new helper with
  old workflows, or old helper with new gates, may bypass the intended order.
- Rollback: revert the atomic change before starting a release. Do not roll back
  while a tag-triggered publish is in progress.
- Post-landing observation: the next ordinary push must show main jobs first and
  extension jobs only afterward; the next release must show no pytest steps and
  no long-running Test poll in any release-gate run.

## Tasks

1. Consolidate tests in `.github/workflows/test.yml`.
   - Make the Ubuntu/Python 3.13 shards include slow tests so every main test
     fires without multiplying slow tests across the compatibility matrix.
   - Move the PostgreSQL-compatible shards into this workflow's main stage.
   - Gate all four extension jobs on the exact same main owners: `test`,
     `test-postgres`, `lint`, and `coverage`. Do not use `always()` on extension
     jobs. Remove the release-commit skip conditions.
2. Move the wait before tag creation.
   - Port the minimal SimpleBroker workflow-run evaluator into `bin/release.py`
     and call it after pushing `main`; keep the release workflow's independent
     one-shot JavaScript check semantically aligned through contract tests.
   - Accept explicit release targets matching SimpleBroker: `core`, each named
     extension, and `all`; default to `core`.
   - Wait for the one protected-branch `push` run of `test.yml` at the exact
     release SHA, then revalidate origin ancestry, publication state, and tag
     actions before pushing only requested tags.
3. Strip test execution from release gates.
   - Remove pytest/setup jobs from every tag gate.
   - Keep immediate completed-Test verification, tag-current verification, and
     the existing reusable build/publish path.
   - Replace the long polling loop in `.github/workflows/release.yml` with one
     fail-closed API query for the already completed `test.yml` push run.
4. Promote the documentation contract and reconcile traceability.
   - Rewrite the README workflow and release sequence.
   - Keep [TS-4]'s ownership mapping and README release sequence synchronized.
   - Record verification and review findings here, then close the plan/index.

Stop and re-evaluate if implementing the graph requires a second test workflow,
if any tag can precede CI success, if independent package tags are lost, or if
an existing test can only pass through dead workflow metadata.

## Verification

With CI-structure tests rewritten to assert behavior rather than the retired
shape, and without changing product/runtime tests:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/system/test_release_script.py tests/system/test_release_workflow.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check bin .github/scripts
./.venv/bin/mypy bin .github/scripts --config-file pyproject.toml
python - <<'PY'
from pathlib import Path
import yaml
for path in sorted(Path('.github/workflows').glob('*.yml')):
    yaml.safe_load(path.read_text())
PY
```

Then inspect the parsed job graph to prove main-stage dependencies, extension
fan-out, absence of release-gate pytest commands, exact-SHA verification, and
publish dependency ordering. Full suite verification follows the repository
handoff gate if local time and platform dependencies permit.

## Review Plan

- Independent plan review before implementation.
- Independent workflow/release-boundary review after implementation.
- Different-agent-family pre-landing review if available, as required for +P.

## Deviation Log

| Contract ref | Planned behavior | Actual behavior | Rationale | Contract proposal |
|---|---|---|---|---|

## Review Log

| Review | Result | Disposition |
|---|---|---|
| Independent plan review 1 | BLOCKED: target selection was implicit; old brittle tests conflicted; workflow identity, exact DAG, normative delta, and post-wait races were underspecified. | Accepted. User authorized replacement of the brittle CI tests. Plan now specifies explicit targets, exact push-run identity and dependencies, promoted [TS-4], one-shot gate verification, and post-CI state refresh. |
| Independent implementation review 1 | BLOCKED: main test target enumeration omitted root test modules; package gates were not checked against exact tag/package/path/ref mappings; post-CI state was revalidated target-by-target before mutation. | Accepted. Added full `tests/` partition proofs for both backends, exact five-gate routing assertions, and batch revalidation before any tag mutation. |
| Independent implementation review 2 | PASS: 56 release tests passed; Ruff and diff checks passed; all main tests are assigned exactly once per backend; all five gates route exactly; post-CI validation precedes tag mutation; no release workflow runs tests or polls. | Accepted with no further findings. |

## Verification Log

| Command | Result |
|---|---|
| `./.venv/bin/python -m pytest` | `5095 passed, 28 skipped` |
| `./.venv/bin/python -m pytest tests/system/test_release_script.py tests/system/test_release_workflow.py -q` | `56 passed` (independent review) |
| `./.venv/bin/ruff check .` | Passed |
| `./.venv/bin/ruff format --check weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox` | `440 files already formatted` |
| `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml` | `Success: no issues found in 434 source files` |
| Parse every `.github/workflows/*.yml` with `yaml.safe_load` | Passed |
| `./.venv/bin/python bin/ruff_suppression_index.py --check` | Passed |
| `bin/check-dom15-fixtures` | Passed |
| `git diff --check` | Passed |

## Follow-up: Cheap Gates First

On 2026-09-16, the CI graph was tightened so the existing `lint` job (Ruff,
formatting, suppression-index validation, and mypy) must succeed before either
main test matrix starts. This preserves the staged release contract while
avoiding expensive test allocation for commits that fail cheap static checks.
No YAML-shape test enforces this dependency; the workflow and its documented
contract are the source of truth, avoiding a brittle mirror of declarative CI.

Follow-up verification: the release/spec/plan subset, Ruff, formatting, mypy,
workflow YAML parsing, and `git diff --check` all passed.

The local release helper follows the same fail-fast order: Ruff, formatting,
and mypy precede SQLite, PostgreSQL, extension, and live-provider tests. Its
existing coverage test intentionally verifies which gates run without mirroring
their tuple positions.
