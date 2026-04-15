# System Builtins Command Plan

## Goal

Add `weft system builtins` as the explicit discovery surface for shipped
builtins. The contract is narrow: `weft spec list` remains the project-visible
spec namespace, while `weft system builtins` reports the builtin inventory that
ships with Weft regardless of local shadows.

## Source Documents

Source specs:

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-0.2], [CLI-1.4], [CLI-6]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
  [CLI-X0], [CLI-X1]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)

Roadmap and related plans:

- [`docs/plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](./2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md)
- [`docs/plans/2026-04-14-weft-road-to-excellent-plan.md`](./2026-04-14-weft-road-to-excellent-plan.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

Source spec gap:

- None. This slice extends the current CLI contract with an explicit builtin
  discovery command and clarifies the split between effective spec resolution
  and shipped builtin inventory.

## Context and Key Files

Files to modify:

- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `README.md`
- `weft/builtins/__init__.py`
- `weft/cli.py`
- add one narrow command helper module under `weft/commands/`
- `tests/cli/test_cli_system.py`
- maybe `tests/system/test_builtin_contract.py` if the builtin catalog contract
  needs one more assertion

Read first:

- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/10-CLI_Interface.md` [CLI-1.4], [CLI-6]
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `README.md`
- `weft/builtins/__init__.py`
- `weft/cli.py`
- `tests/cli/test_cli_system.py`
- `tests/cli/test_cli_spec.py`

Shared helpers and paths to reuse:

- `weft/builtins/__init__.py` already owns builtin asset discovery
- builtin JSON assets already carry public summary fields such as `name`,
  `description`, and `metadata.category`
- `weft spec list` already owns the effective project namespace and must stay
  the canonical file-or-stored-or-builtin resolver

Current structure:

- builtin TaskSpecs live under `weft/builtins/tasks/`
- `weft spec list` currently reports stored specs plus builtin task specs,
  filtered by project-local shadowing
- builtin-only specs are read-only
- `system` currently contains maintenance commands such as `dump`, `load`, and
  `tidy`, but no builtin inventory command

Comprehension questions:

1. Which module owns the effective project-visible spec namespace today?
2. What distinction should remain true between `weft spec list` and
   `weft system builtins` after this slice?

## Invariants and Constraints

The following must stay true:

- `weft spec list` remains the effective project/spec namespace surface
- `weft system builtins` reports the shipped builtin inventory, not the
  project-resolved shadowed view
- builtin helpers remain ordinary TaskSpecs on the existing durable spine
- no new top-level command family is introduced
- builtin discovery stays centralized; do not duplicate ad hoc builtin path
  logic in multiple modules
- no new dependency
- no command-or-spec guessing under bare `weft run`
- docs, help text, and runtime behavior must say the same thing

Hidden couplings:

- builtin asset summaries come from the shipped JSON assets, not from local
  stored specs
- `weft spec list` and `weft system builtins` will now expose two related but
  intentionally different views; docs must name that distinction directly
- `weft/cli.py` help text and command registration are part of the contract
- `README.md` is part of the public surface and must not imply that
  `system builtins` resolves or executes specs

Out of scope:

- changing `weft spec list` to stop listing builtin specs
- adding builtin pipeline specs
- adding builtin install/export commands
- turning `system builtins` into a second spec-management surface
- broad `system` command cleanup unrelated to builtin discovery

## Tasks

1. Tighten the docs around the two builtin views.
   - Outcome: the docs clearly separate effective spec discovery from shipped
     builtin inventory.
   - Files to touch:
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/11-CLI_Architecture_Crosswalk.md`
     - `README.md`
   - Required action:
     - state that `weft spec list` is the project-visible spec namespace
     - add `weft system builtins` as the shipped builtin inventory surface
     - make it explicit that local shadows affect `spec list` but not
       `system builtins`
   - Stop if:
     - the docs start implying that `system builtins` resolves or executes
       specs
   - Done when:
     - the distinction is explicit and consistent across specs and README

2. Implement a shared builtin catalog helper and `weft system builtins`.
   - Outcome: the command returns shipped builtin summaries without duplicating
     asset parsing logic.
   - Files to touch:
     - `weft/builtins/__init__.py`
     - new command helper under `weft/commands/`
     - `weft/cli.py`
   - Required action:
     - add one shared helper that returns builtin metadata in stable order
     - expose a `system builtins` command with plain and JSON output
     - keep the command project-independent; it should not depend on local
       `.weft/tasks` contents
   - Reuse:
     - existing builtin asset discovery under `weft/builtins/`
   - Stop if:
     - the implementation starts reusing the spec resolver instead of the
       shipped builtin catalog
   - Done when:
     - `weft system builtins` returns the shipped builtin inventory and basic
       information about each builtin

3. Add black-box CLI tests and verify the distinction.
   - Outcome: drift between the two surfaces becomes harder.
   - Files to touch:
     - `tests/cli/test_cli_system.py`
     - optionally `tests/system/test_builtin_contract.py`
   - Required tests:
     - plain `weft system builtins` lists shipped builtins with summary info
     - JSON output includes the builtin metadata fields we intend to support
     - local project shadows do not change `weft system builtins`
   - Keep real:
     - real CLI execution through `run_cli()`
     - real builtin asset loading from the package tree
   - Done when:
     - the CLI-visible contract is proven without mocking builtin discovery

## Verification

- `source .envrc && uv run pytest tests/cli/test_cli_system.py tests/cli/test_cli_spec.py tests/system/test_builtin_contract.py -q`
- `source .envrc && uv run ruff check weft tests/cli/test_cli_system.py tests/cli/test_cli_spec.py tests/system/test_builtin_contract.py`
- `source .envrc && uv run mypy weft`
