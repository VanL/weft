# Dependency Refresh

Status: draft
Source specs: None — user-directed dependency and CI tool maintenance, with no product behavior change.
Superseded by: none

Class 3: refreshes the lock and separate CI/release tool pins across multiple surfaces; no intentional public contract or execution-path change. Implementation began before the plan was written; this sequencing deviation is recorded here and the branch remains open pending verification and independent review.

## Goal and context

Resolve the newest compatible dependency releases and run CI and release workflows with uv 0.13.6. Update setup-uv to v10.3.3 and download-artifact to v8.0.2 (commit `0d7613df`). `pyproject.toml` declares minimum supported APIs, `uv.lock` records selected packages, and `.github/workflows/test.yml` and `release.yml` independently pin uv. The existing unbounded dependency policy and minimums stay unchanged. Workflow actions remain SHA pinned.

## Invariants and boundaries

Preserve package minimums, optional extras and platform markers, execution behavior, CI jobs, release gates, and `hatchling.build`. Real installed dependencies and the real uv resolver must be used; mocks cannot prove dependency compatibility. This is reversible by reverting the lock, uv runtime pins, and action pin changes, including commit `0d7613df`. Release publication, provider calls, and unrelated runtime repairs are out of scope.

## Tasks and verification

1. Compare action releases and PyPI direct packages with current state; run `uv lock --upgrade` and inspect the resolution. Stop if a supported package cannot resolve.
2. Set both workflow uv pins to the latest stable release and use that version to run `uv lock --check` and `uv sync --all-extras`; run Ruff check and format, mypy, and the hermetic pytest suite. Most CI jobs install directly from PyPI rather than this lock, so local lock check, sync and tests are the lock evidence; CI covers the action and uv pins and its platform jobs. GitHub CI must pass before merging. Review any failure for compatibility rather than assume flakiness. No new tests are planned because the intended change is the real dependencies exercised by existing tests, not a new contract.
3. Obtain independent different-family review of the plan and diff using `skills/call-agent/SKILL.md`, dispose of findings, and record the result. Run a separate fresh-eyes pass. Update status and index together only after all gates pass. Observe the next CI and release run; no release is triggered here.

## Evidence and review

Pending: GitHub CI, PostgreSQL and platform matrix, and plan closure. A green local build cannot establish release workflow execution or all platform support.

2026-10-28: uv 0.13.6 resolved and installed all extras with the refreshed lock; Ruff check and format, mypy (478 files), and plan metadata tests passed. Hermetic pytest passed: 5914 passed and 38 PostgreSQL-specific skips on macOS with four workers. Cross-family review is recorded below. The live release and platform matrix remain unverified.

Cross-family Claude review (2026-10-28) returned no blocker. P2-1: most CI installs ignore the lock; accepted and clarified above. P2-2: reviewer assumed local uv 0.13.5 was used for the version check; rejected on direct command evidence: `uvx --from uv==0.13.6 uv lock --check` and `uvx --from uv==0.13.6 uv sync --all-extras` both passed. Claude containment was not independently probed this session, so review eligibility remains unverified. Grok review failed closed because its sandbox could not apply due to a runtime-socket symlink. A fresh-eyes pass found no further action. GitHub CI and PostgreSQL/platform checks remain open.

Final cross-family review (2026-10-28): Claude with `--permission-mode dontAsk --tools Read,Grep,Glob` returned no blocker. A write-attempt probe using the same containment refused to create a file. P2-1: plan omitted action changes and rollback; corrected above. P2-2: action SHA provenance missing; `gh api` verified setup-uv v10.3.3 resolves to `68559e99cc807c1eb7e9238fd43a15bba460eec2` and download-artifact v8.0.2 to `9000827ccba6bdab643e8b6fd33ac0654aef8333`. P2-3: stale pending-suite statement corrected. Pre-existing pip CI ignores uv constraints and PostgreSQL CI does not enforce a locked resolution; these are outside this update and local frozen resolution is the lock evidence. Fresh-eyes remains required by the review runbook. Release execution is a later observation, not evidence of this change until a release occurs.
