# Postgres Extra And Runtime Hint Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Make Postgres support optional at install time instead of always pulling in
`simplebroker-pg`, while keeping the runtime UX crisp: if a user selects the
Postgres backend without installing the plugin, Weft should fail with an
explicit `weft[pg]` install hint.

## Source Documents

Source spec: None — packaging and runtime UX change

Relevant docs and behavior:

- [`pyproject.toml`](../../pyproject.toml)
- [`README.md`](../../README.md)
- [`weft/context.py`](../../weft/context.py)
- [`weft/commands/init.py`](../../weft/commands/init.py)
- [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
- [`tests/context/test_context.py`](../../tests/context/test_context.py)
- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)

## Context and Key Files

Files to modify:

- `pyproject.toml`
- `README.md`
- `weft/context.py`
- `weft/commands/init.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`

Read first:

- `weft/context.py`
- `weft/commands/init.py`
- `../simplebroker/simplebroker/project.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`

Shared paths and behavior to reuse:

- SimpleBroker already reports the missing-plugin condition for `postgres`;
  Weft should wrap that existing failure, not invent a second backend
  resolution path.
- `build_context()` and `cmd_init()` are the two main user entry points that
  need the same install hint.

## Invariants and Constraints

- Keep SQLite as the default install and runtime path.
- Do not add a second backend-resolution implementation in Weft.
- Postgres backend selection must still happen through SimpleBroker project
  config or translated `WEFT_BACKEND*` / `BROKER_BACKEND*` settings.
- Runtime failures for a missing Postgres plugin should point users to
  `uv add 'weft[pg]'` while still mentioning `simplebroker-pg`.
- Keep release/test tooling working with the new extra layout.

## Tasks

1. Move `simplebroker-pg` out of base dependencies.
   - Remove it from `[project].dependencies`.
   - Add a `[project.optional-dependencies].pg` extra that installs
     `simplebroker-pg>=1.0.0`.

2. Add one shared backend-error normalizer in `weft/context.py`.
   - Detect the existing SimpleBroker “postgres backend not available” error.
   - Re-raise a clearer Weft-facing error with the `weft[pg]` install hint.
   - Use it in both `resolve_broker_target()` and `target_for_directory()`
     call sites inside `build_context()`.

3. Reuse the same error normalization in `weft/commands/init.py`.
   - Wrap the `target_for_directory()` / `sb_cmd_init()` path so `weft init`
     prints the same install guidance.

4. Add targeted tests and docs.
   - Add a context test that monkeypatches backend resolution to simulate the
     missing Postgres plugin.
   - Add an init-command test that verifies the user-visible stderr guidance.
   - Update `README.md` to document `uv add 'weft[pg]'` and show how backend
     selection is declared at runtime.

## Testing Plan

- Use monkeypatch-based tests for the missing-plugin path; do not depend on an
- actually missing local package.
- Keep verification targeted to the files above.
- Use the existing `shared` test files because the behavior is backend-agnostic.

## Verification

Run:

```bash
uv run --extra dev pytest tests/context/test_context.py tests/cli/test_cli_init.py -q
uv run --extra dev ruff check weft/context.py weft/commands/init.py tests/context/test_context.py tests/cli/test_cli_init.py
```

Success looks like:

- the packaging metadata exposes `weft[pg]`,
- `build_context()` and `weft init` both emit the `weft[pg]` install hint for
  a missing Postgres plugin,
- and the targeted tests/lint pass.

## Out of Scope

- Adding a dedicated `--backend` flag to `weft init`
- Reworking SimpleBroker’s plugin discovery behavior
- Changing the release gate or PG test runner
