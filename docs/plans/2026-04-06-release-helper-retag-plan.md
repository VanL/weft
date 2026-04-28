# Release Helper Retag Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Add an explicit `--retag` flag to `bin/release.py` so maintainers can delete
and recreate an unpublished remote tag when the helper detects that the target
version's tag exists on `origin` but points at the wrong commit. Keep the
default behavior conservative: without `--retag`, remote tag movement still
fails.

## Source Documents

Source spec: None — release tooling change

Relevant docs and behavior:

- [`bin/release.py`](../../bin/release.py)
- [`README.md`](../../README.md)
- [`tests/system/test_release_script.py`](../../tests/system/test_release_script.py)

## Context and Key Files

Files to modify:

- `bin/release.py`
- `README.md`
- `tests/system/test_release_script.py`

Read first:

- `bin/release.py`
- `tests/system/test_release_script.py`

Shared paths and behavior to reuse:

- Publication-state checks already ensure the version has no GitHub Release and
  no PyPI publication before tag reuse is considered.
- Local stale tags are already auto-repaired; `--retag` should extend that
  model to `origin`, not create a second release path.

## Invariants and Constraints

- Never move a remote tag implicitly; require `--retag`.
- Do not allow `--retag` once the version has a GitHub Release or PyPI
  publication.
- Preserve the current release-gate workflow model.

## Tasks

1. Add `--retag` to the parser and thread the flag into tag planning.
2. Extend tag planning to return a remote-retag action only when explicitly
   requested.
3. Implement the remote-retag flow:
   - delete the remote tag,
   - repair/recreate the local tag at `HEAD` if needed,
   - push the recreated tag.
4. Add focused helper tests and update the README release section.

## Testing Plan

- Keep verification in `tests/system/test_release_script.py`.
- Use monkeypatch-based dry-run tests; do not perform live tag deletion.

## Verification

Run:

```bash
uv run --extra dev pytest tests/system/test_release_script.py -q
uv run --extra dev ruff check bin/release.py tests/system/test_release_script.py
```

## Out of Scope

- Retagging versions that already have a GitHub Release or PyPI publication
- Automatically rerunning GitHub Actions for unchanged remote tags
