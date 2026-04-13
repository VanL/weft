# Release Helper Plan

## Goal

Add a repo-local release helper at `bin/release.py` so maintainers can run a
single command to validate a release, update both canonical version sources,
tag the repo, and optionally publish the GitHub Release that triggers the
existing package-publish workflow.

## Source Documents

Source spec: None — tooling change

Relevant docs and behavior:

- [`README.md`](../../README.md)
- [`pyproject.toml`](../../pyproject.toml)
- [`weft/_constants.py`](../../weft/_constants.py)
- [`.github/workflows/test.yml`](../../.github/workflows/test.yml)
- [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
- [`tests/system/test_constants.py`](../../tests/system/test_constants.py)

## Context and Key Files

Files to modify:

- `bin/release.py`
- `README.md`
- `tests/system/test_release_script.py`

Files to add for process traceability:

- `docs/plans/2026-04-06-release-helper-plan.md`

Read first:

- `README.md`
- `.github/workflows/test.yml`
- `.github/workflows/release.yml`
- `tests/system/test_constants.py`

Shared paths and behavior to reuse:

- `pyproject.toml` and `weft/_constants.py` are the two canonical version
  sources and must stay in sync.
- `.github/workflows/test.yml` defines the release preflight quality bar.
- `.github/workflows/release.yml` remains the publish path of record; the new
  script should not create a second packaging pipeline.

## Invariants and Constraints

- Do not add `invoke` or any new release dependency.
- Do not add a new installed user-facing CLI entry point in `[project.scripts]`.
- Keep the release publish path anchored on the existing GitHub Actions
  workflow.
- Update both `pyproject.toml` and `weft/_constants.py` together or fail.
- Refuse to proceed from a dirty git worktree.
- Keep the version format strict and explicit for this initial helper.
- Do not touch unrelated repo files in the existing dirty tree.

## Tasks

1. Add `bin/release.py`.
   - Implement argument parsing with an explicit `--version`.
   - Add helpers to read the current version, validate the target version,
     update both version files, and run shell commands from the repo root.
   - Support `--dry-run`, `--skip-checks`, and optional GitHub release
     publication.

2. Reuse the existing quality bar before creating the release commit.
   - Run the local equivalents of the CI checks from `.github/workflows/test.yml`.
   - Run `uv build` after updating the version files so the built metadata
     matches the release tag.

3. Implement the git/release flow.
   - Fail fast on a dirty tree.
   - Commit only `pyproject.toml` and `weft/_constants.py`.
   - Tag `v<version>`.
   - Push the branch head and tag.
   - If requested, create and publish the GitHub Release with `gh release create`
     so `.github/workflows/release.yml` runs.

4. Add targeted tests.
   - Load `bin/release.py` as a module from its file path.
   - Verify the helper updates both version files correctly.
   - Verify the version parser rejects invalid version strings.

5. Update `README.md`.
   - Replace the manual bump instructions with the new `bin/release.py`
     command.
   - Keep the note that GitHub Actions performs package publishing.

## Testing Plan

- Add `tests/system/test_release_script.py`.
- Use `tmp_path` fixtures for isolated file rewrites.
- Avoid git/network integration tests; unit-test the pure file-update and
  validation helpers instead.
- Rely on targeted command verification for the end-to-end script behavior.

## Verification

Run:

```bash
uv run pytest tests/system/test_release_script.py -q
uv run pytest tests/system/test_constants.py -q
uv run ruff check bin/release.py tests/system/test_release_script.py
uv run mypy weft
```

Success looks like:

- the release helper tests pass,
- the existing version-consistency test still passes,
- lint passes for the new script and test,
- and mypy still passes for the package.

## Out of Scope

- Auto-calculating the next version number.
- Publishing directly to PyPI outside GitHub Actions.
- Adding `invoke`, `make`, or another task runner.
- Expanding the installed `weft` CLI surface.
