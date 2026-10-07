# Validated Dependency Floor Alignment

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1]
Superseded by: none

Class: 5, strategy D for synchronizing the existing broker-floor spec statement.
Hardening: N/A. This adopts already installed and validated versions; there is
no dependency upgrade, runtime change, persistence change, or rollout dependency.

## Goal

Align every existing dependency floor in core and all first-party extensions
and integrations with the versions currently installed and locked. Refresh all
existing dependency locks and commit the bounded change at the user's request.

## Source Documents and Baseline

- User instructions in this session authorize all floor changes and the commit.
- Baseline: `7e931c3a`, including `pyproject.toml`, four subpackage manifests,
  and `uv.lock`. Installed versions match the lock for every declared package.
- [SimpleBroker integration](../specifications/04-SimpleBroker_Integration.md)
  [SB-0.1] states current broker requirements. Its older 8.3.0/4.3.0 statement
  must match the already declared and validated 8.5.1/4.5.1 pair.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/testing-patterns.md` govern verification.

The package metadata work was initially classified as Class 2 after the user
corrected upgrade framing. The documentation sweep found a stale normative
broker-floor statement, requiring Class 5 solely for its synchronization.

## Files and Ownership

The task owner edits `pyproject.toml`,
`extensions/weft_docker/pyproject.toml`,
`extensions/weft_macos_sandbox/pyproject.toml`,
`extensions/weft_microsandbox/pyproject.toml`,
`integrations/weft_django/pyproject.toml`, `uv.lock`, `README.md`,
`integrations/weft_django/README.md`, `tests/system/test_optional_extras.py`,
the two numeric floors in spec [SB-0.1], and this plan/index.

Reuse uv's existing local editable sources and lock generation. Hatchling is
build-isolated rather than in `uv.lock`; the baseline wheel's generator records
Hatchling 1.32.4. Only root `uv.lock` is an existing dependency lock.

## Invariants and Scope

Preserve package versions, Python >=3.12, dependency names, optional-extra
membership, platform markers, upper bounds, editable sources, locked package
versions, locked wheel/sdist URLs and hashes, and runtime behavior. Existing dev/constraint
floors and pytest's invocation minimum are included. Do not introduce new
dependencies or new extension lock files. The release-specific Django 0.9.42
statement in [DJ-19] remains historical compatibility evidence; current Django
support is documented in the integration README.

All queue, state, TaskSpec, spawn, cleanup, and public CLI contracts remain
unchanged. No migrations or execution-path edits are involved.

## Proposed Spec Delta

In [SB-0.1], replace the current SimpleBroker minimum 8.3.0 with 8.5.1 and
the PostgreSQL plugin minimum 4.3.0 with 4.5.1. No other broker rule changes.
Strategy D applies this documentation synchronization before final verification.
Promotion baseline: the two numeric replacements and plan backlink against
`7e931c3a`; verification reads the resulting spec rather than this proposal.

## Tasks

1. Inventory and prove drift against the installed/locked baseline. Update the
   two existing metadata assertions first; observe both failing on old floors.
   A complete requirement audit must report every lagging floor before edits.
2. Align all five manifests, preserve bounds/markers/extras, regenerate the
   existing lock with `uv lock --offline`, and sync with
   `uv sync --all-extras --locked --offline`. Stop if any locked version or
   artifact changes, or a dependency outside the baseline is needed.
3. Synchronize current support documentation and spec [SB-0.1]. Independently
   review the numeric delta before applying it. Verify original release-specific
   historical statements are retained.
4. Audit every floor and every locked package, build all five wheels, run metadata
   and extension/integration tests, type checking and lint. Review the final
   diff; commit only the explicit file list after successful verification.

## Verification

The temporary audit compares every requirement with the captured installed/locked
baseline, and checks unchanged upper bounds, markers, extras, package versions,
sources, and lock artifacts. This substitutes for adding a permanent snapshot
test: the pre-change audit observed 25 mismatched declarations and the post-change
audit must report zero. Real uv resolution/building and the existing packaging
tests provide the correction proof, without mocking the dependency resolver.

Run `uv lock --check --offline`, `uv pip check`, five offline wheel builds,
`python -m pytest tests/system/test_optional_extras.py tests/specs extensions
integrations -m 'not slow'`, the repo-wide mypy command from `AGENTS.md`,
`ruff check .`, and `git diff --check`, using `.envrc` and the repo environment.
Inspect each built wheel's published requirements against its manifest.
Live container-service tests may skip when their existing opt-in is absent.

## Rollback and Review

Revert the manifest, lock metadata, test expectation, and support-document
delta together and resync. No persisted state needs rollback and no release
order is required. Self-review checks every declaration against the baseline.
An independent same-family reviewer is available in this session; review scope
is floor coverage, preservation of the named invariants, and final evidence.

## Deviation Log

None. Dependency versions remain the captured validated baseline.

## Evidence

- Pre-change full audit: 25 floor mismatches. The two updated existing packaging
  assertions failed before the manifest repairs, on Channels and Microsandbox.
- Post-change audit: all 61 declarations in the five manifests match the
  captured baseline; bounds, extras, markers, Python requirement, package
  versions, and editable sources are preserved. All 86 locked packages retain
  their versions, sources, distribution URLs/hashes, and dependency edges.
- `uv lock --offline`, `uv lock --check --offline`, and
  `uv sync --all-extras --locked --offline` succeeded. `uv pip check` found
  every installed package compatible.
- Five offline wheel builds succeeded. Inspection of each wheel verified all
  published requirements against its manifest and Hatchling 1.32.4 as generator.
- Packaging, spec, extension, and integration tests: 537 passed, one
  PostgreSQL-target proof skipped under the current SQLite environment.
- Repo-wide mypy: no issues in 439 files. Ruff lint and the touched test's
  formatter check passed. `git diff --check` passed.
- Fresh-eyes self-review found no missing floor or unintended dependency change.
  Independent plan/delta review passed. Its artifact-wording clarification was
  accepted: preservation concerns locked distribution URLs/hashes, not the
  bytes of newly built local wheels, whose metadata intentionally changes.
