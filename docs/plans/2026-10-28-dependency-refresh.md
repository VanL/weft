# Dependency Refresh

Status: draft
Source specs: None — user-directed dependency and CI tool maintenance, with no product behavior change.
Superseded by: none

Class 3: refreshes the lock and separate CI/release tool pins across multiple surfaces; no intentional public contract or execution-path change. Implementation began before the plan was written; this sequencing deviation is recorded here and the branch remains open pending verification and independent review.

## Goal and context

Resolve the newest compatible dependency releases and run CI and release workflows with uv 0.13.6. `pyproject.toml` declares minimum supported APIs, `uv.lock` records selected packages, and `.github/workflows/test.yml` and `release.yml` independently pin uv. The existing unbounded dependency policy and minimums stay unchanged. Workflow actions remain SHA pinned.

## Invariants and boundaries

Preserve package minimums, optional extras and platform markers, execution behavior, CI jobs, release gates, and `hatchling.build`. Real installed dependencies and the real uv resolver must be used; mocks cannot prove dependency compatibility. This is reversible by reverting the lock and both workflow pin edits. Release publication, provider calls, and unrelated runtime repairs are out of scope.

## Tasks and verification

1. Compare action releases and PyPI direct packages with current state; run `uv lock --upgrade` and inspect the resolution. Stop if a supported package cannot resolve.
2. Set both workflow uv pins to the latest stable release and use that version to run `uv lock --check` and `uv sync --all-extras`; run Ruff check and format, mypy, and the hermetic pytest suite. GitHub CI must pass before merging. Review any failure for compatibility rather than assume flakiness. No new tests are planned because the intended change is the real dependencies exercised by existing tests, not a new contract.
3. Obtain independent different-family review of the plan and diff using `skills/call-agent/SKILL.md`, dispose of findings, and record the result. Run a separate fresh-eyes pass. Update status and index together only after all gates pass. Observe the next CI and release run; no release is triggered here.

## Evidence and review

Pending: full test suite, GitHub CI, independent review and plan closure. A green local build cannot establish release workflow execution or all platform support.

2026-10-28: uv 0.13.6 resolved and installed all extras with the refreshed lock; Ruff check and format, mypy (478 files), and plan metadata tests passed. Hermetic tests and cross-family review are running. The live release and platform matrix remain unverified.
