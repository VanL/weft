# Release Helper Unpublished Version Reuse Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Allow the local release helper to reuse the current repo version when that
version has not yet produced a GitHub Release or a PyPI publication, while
preserving safe tag behavior. The helper should only require a version bump
once the version is externally published, and it should never silently move an
existing tag to a different commit.

## Source Documents

Source spec: None — release tooling change

Relevant docs and behavior:

- [`bin/release.py`](../../bin/release.py)
- [`README.md`](../../README.md)
- [`.github/workflows/release-gate.yml`](../../.github/workflows/release-gate.yml)
- [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
- [`tests/system/test_release_script.py`](../../tests/system/test_release_script.py)

## Context and Key Files

Files to modify:

- `bin/release.py`
- `README.md`
- `tests/system/test_release_script.py`

Read first:

- `bin/release.py`
- `.github/workflows/release-gate.yml`
- `.github/workflows/release.yml`
- `tests/system/test_release_script.py`

Shared paths and behavior to reuse:

- `pyproject.toml` and `weft/_constants.py` remain the canonical version files.
- The release gate is still triggered by `v*` tags; the helper should not add a
  second publishing path.
- Existing local precheck and post-update command execution should stay intact.

## Invariants and Constraints

- Do not silently retag a different commit with an existing version.
- Require a new version once a GitHub Release or PyPI publication already
  exists for that version.
- Keep the helper compatible with the current tag-gated GitHub Actions flow.
- Do not add non-stdlib dependencies for GitHub/PyPI checks.

## Tasks

1. Add release-state inspection helpers to `bin/release.py`.
   - Inspect GitHub Release existence for a tag.
   - Inspect PyPI publication existence for a version.
   - Inspect local and remote git tag commits for the target tag.

2. Make `--version` optional and resolve the target version from release state.
   - Default to the current repo version when it has not been externally
     published.
   - Require an explicit new version when the current version is already
     published.
   - Reject reuse of an existing tag when it points at a different commit.

3. Preserve the existing release flow with version-aware branching.
   - If the version changes, update files and commit as before.
   - If the version is reused unchanged, skip file rewrites and commit creation.
   - Reuse/push/create the tag based on the safe tag plan and print clear notes
     when the remote tag already exists.

4. Add targeted tests and docs.
   - Unit-test version resolution and tag safety as pure helper behavior.
   - Update the README release section to describe when `--version` is optional
     and when a bump is required.

## Testing Plan

- Keep tests focused on `tests/system/test_release_script.py`.
- Use monkeypatching for release-state helpers rather than real network calls.
- Verify only the helper’s pure logic and dry-run output, not live git/GitHub
  side effects.

## Verification

Run:

```bash
uv run --extra dev pytest tests/system/test_release_script.py -q
uv run --extra dev ruff check bin/release.py tests/system/test_release_script.py
```

Success looks like:

- the helper can reuse an unpublished current version in dry-run mode,
- it requires a new version once GitHub/PyPI publication exists,
- it rejects unsafe tag reuse across commits,
- and the targeted tests/lint pass.

## Out of Scope

- Automatically retagging a different commit
- Forcing GitHub Actions to rerun when a remote tag already exists
- Changing the release gate trigger model
