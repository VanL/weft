# Release-Gated Tag Workflow Plan

## Goal

Add a tag-driven GitHub Actions workflow that runs the full SQLite release
suite and the PG-compatible release suite before creating the GitHub Release.
Keep package publishing anchored on the existing release-published workflow so
Weft has one publish pipeline, but prevent the normal helper-driven release path
from bypassing the backend gate.

## Source Documents

Source spec: None — tooling / release-process change

Relevant docs and behavior:

- [`README.md`](../../README.md)
- [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
- [`.github/workflows/test.yml`](../../.github/workflows/test.yml)
- [`bin/release.py`](../../bin/release.py)
- [`bin/pytest-pg`](../../bin/pytest-pg)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`../simplebroker/.github/workflows/release-simplebroker.yml`](../../../simplebroker/.github/workflows/release-simplebroker.yml)

## Context and Key Files

Files to modify:

- `.github/workflows/release-gate.yml`
- `README.md`
- `bin/release.py`
- `tests/system/test_release_script.py`

Files to read first:

- `.github/workflows/test.yml`
- `.github/workflows/release.yml`
- `bin/pytest-pg`
- `docs/specifications/08-Testing_Strategy.md`
- `tests/system/test_release_script.py`

Shared paths and behavior to reuse:

- `bin/pytest-pg` is the canonical PG-backed test runner and should stay the
  only workflow entry point for Postgres parity.
- `.github/workflows/release.yml` is already the canonical package-publish path
  and should continue to own build / PyPI / TestPyPI / signing steps.
- The release helper should still own local version bumping, commit, tag, and
  push behavior.

## Invariants and Constraints

- Do not create a second package publishing pipeline.
- The GitHub Release must be created only after the SQLite and Postgres test
  jobs both succeed.
- SQLite / file-backed tests must continue to run only in the SQLite suite.
- Postgres parity must continue to run through `bin/pytest-pg`.
- Keep changes narrow to release workflow / helper / docs; do not refactor the
  broader CI setup.
- Do not touch unrelated files in the existing dirty tree.

## Tasks

1. Add a release-gate workflow on version tags and backend test success.
   - Add `.github/workflows/release-gate.yml` for version-tag pushes.
   - Add a SQLite job that runs the full SQLite release suite.
   - Add a Postgres job that runs `uv run bin/pytest-pg --all`.
   - Create the GitHub Release only after both test jobs succeed.

2. Keep the publish pipeline on the existing release-published workflow path.
   - Leave `.github/workflows/release.yml` as the package build / publish /
     signing workflow.
   - Ensure the new gate creates the GitHub Release from the tested tag so the
     existing publish workflow still runs unchanged.

3. Remove the normal helper bypass.
   - Update `bin/release.py` so the helper no longer creates a GitHub Release
     directly.
   - Keep the helper focused on local checks, version updates, commit, tag, and
     push so the GitHub Action becomes the standard release trigger.

4. Update release documentation and tests.
   - Rewrite the README release section to describe the tag-push gate.
   - Add or update a targeted release-helper test for the no-direct-publish
     behavior.

## Testing Plan

- Keep test coverage targeted to the release helper; do not attempt live git or
  GitHub integration tests.
- Use `tmp_path` and monkeypatch-based tests in
  `tests/system/test_release_script.py` for helper behavior.
- Validate the workflow YAML structurally by inspecting it after the patch and
  rely on existing repo conventions for Actions syntax.

## Verification

Run:

```bash
uv run pytest tests/system/test_release_script.py -q
python3 - <<'PY'
import yaml
from pathlib import Path
workflow = yaml.safe_load(Path('.github/workflows/release-gate.yml').read_text())
assert 'push' in workflow['on']
assert 'tags' in workflow['on']['push']
assert workflow['jobs']['create-release']['needs'] == ['test-sqlite', 'test-postgres']
PY
```

Success looks like:

- the release helper tests pass,
- the workflow parses and shows the expected backend-test dependencies,
- and the README / helper now describe the gated tag-driven release flow.

## Out of Scope

- Reworking the main test matrix in `.github/workflows/test.yml`
- Adding environment protection rules or branch protection settings
- Changing how version numbers are chosen
