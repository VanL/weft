# Builtin Contract And Doc Drift Reduction Plan

## Goal

Make builtin TaskSpecs a first-class current-contract surface instead of a thin
convenience layer, and reduce drift across runtime code, CLI help, specs, and
README. The goal is not to add a new feature family. The goal is to make the
existing builtin surface explicit, stable, and easy to extend without turning
Weft into a second framework inside itself.

## Source Documents

Source specs:

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], [CLI-1.4]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
  [CLI-X0], [CLI-X1]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-7] for the current `probe-agents` builtin boundary

Current public behavior:

- [`README.md`](../../README.md)

Roadmap and related plans:

- [`docs/plans/2026-04-14-weft-road-to-excellent-plan.md`](./2026-04-14-weft-road-to-excellent-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

Source spec gap:

- None for the basic surface. The current docs already acknowledge builtin
  TaskSpecs, but the contract is still too thin. This plan strengthens the
  current-contract docs rather than inventing a new surface.

## Context and Key Files

Files to modify:

- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `README.md`
- `weft/commands/specs.py`
- `weft/commands/run.py`
- `weft/cli.py`
- `weft/builtins/__init__.py`
- `pyproject.toml` only if builtin asset packaging or packaging tests need
  tightening
- `tests/cli/test_cli_spec.py`
- `tests/cli/test_cli_run.py`
- add one narrow packaging/contract test file only if the current CLI tests are
  not enough to prove builtin asset availability and per-builtin documentation

Read first:

- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1], [CLI-1.4]
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` [CLI-X0], [CLI-X1]
- `README.md`
- `weft/commands/specs.py`
- `weft/commands/run.py`
- `weft/cli.py`
- existing builtin CLI tests in `tests/cli/test_cli_spec.py` and
  `tests/cli/test_cli_run.py`

Shared paths and helpers to reuse:

- `weft/commands/specs.py` already owns explicit `NAME|PATH` spec resolution
- `weft/commands/run.py` already owns the explicit `--spec` and `--pipeline`
  runtime entrypoints
- `weft/builtins/__init__.py` already owns builtin asset lookup
- existing black-box `run_cli()` coverage in `tests/conftest.py`

Current structure:

- builtin task assets currently live under `weft/builtins/tasks/`
- builtin resolution happens only through explicit spec surfaces:
  existing file path, then local `.weft/tasks/<name>.json`, then builtin task
  asset
- local stored task specs already shadow builtin task specs of the same name
- builtin-only specs are already read-only through `weft spec delete`
- `README.md` mentions builtin helpers, but the behavior is scattered across
  quick-start examples and command reference comments rather than one clear
  explanation
- `10B-Builtin_TaskSpecs.md` is currently more of a catalog than a full public
  contract

Comprehension questions:

1. Which module is the single current owner of explicit task-spec resolution,
   and how does it order file, local stored spec, and builtin lookup today?
2. What keeps bare `weft run foo` from becoming builtin spec lookup?
3. Which docs currently describe builtin behavior, and where do they still rely
   on implication rather than an explicit contract?

## Invariants and Constraints

The following must stay true:

- builtin helpers remain ordinary TaskSpecs on the existing durable spine
- bare command execution stays a command-execution surface; no command-or-spec
  guessing is introduced
- builtin lookup stays explicit to `weft run --spec`, `weft spec ...`, and any
  other explicit spec-management surface; it must not leak into bare command
  parsing
- local stored task specs in `.weft/tasks/` shadow builtin task specs of the
  same name
- builtin assets stay read-only package data and cannot be deleted through the
  local spec-management commands
- no default copying of builtin specs into `.weft/`
- no new scheduler, daemon, or plugin-control plane is introduced
- no new dependency
- docs, help text, and runtime behavior must say the same thing when this slice
  lands

Hidden couplings to call out explicitly:

- `weft/commands/specs.py` and `weft/commands/run.py` both shape the public
  explicit-spec contract
- `weft/cli.py` help text is part of the public contract and can drift even
  when runtime behavior is correct
- `README.md` is the first user-facing contract surface many operators will
  read before the specs
- builtin asset discovery depends on package data actually shipping with the
  distribution, not just on local source-tree imports

Out of scope:

- adding a bare `weft run foo` spec lookup fallback
- copying builtin specs into `.weft/` by default
- adding builtin pipeline specs unless a spec gap proves that is required
- adding a large builtin catalog just to justify the contract
- inventing a plugin marketplace or a second builtin extension system
- reworking delegated-agent behavior beyond keeping the current `probe-agents`
  builtin correctly documented

## Tasks

1. Tighten the builtin TaskSpec contract in the current specs.
   - Outcome: `10B-Builtin_TaskSpecs.md` becomes a first-class current contract,
     not just a list of shipped helpers.
   - Files to touch:
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/11-CLI_Architecture_Crosswalk.md`
   - Read first:
     - current builtin spec doc
     - current CLI explicit-spec sections
     - current CLI crosswalk
   - Required additions to the contract:
     - what builtin TaskSpecs are
     - where they live
     - how explicit lookup works
     - local shadowing rules
     - read-only semantics
     - listing and show semantics
     - packaging/discovery guarantee
     - requirement that each shipped builtin has dedicated documentation in
       `10B-Builtin_TaskSpecs.md`
     - acceptance criteria for adding a new builtin in the future
   - Reuse:
     - existing `NAME|PATH` terminology from `10-CLI_Interface.md`
   - Stop if:
     - the draft starts changing runtime behavior rather than describing the
       current or intended current contract
     - the draft starts implying builtin lookup under bare command execution
   - Done when:
     - the spec can stand on its own as the current contract for builtins
     - the CLI doc and crosswalk say the same thing without relying on side
       comments or examples

2. Align the code and CLI help with the tightened builtin contract.
   - Outcome: the runtime and help text expose one obvious builtin contract
     rather than several partially aligned fragments.
   - Files to touch:
     - `weft/commands/specs.py`
     - `weft/commands/run.py`
     - `weft/cli.py`
     - `weft/builtins/__init__.py`
     - `pyproject.toml` only if packaging treatment needs tightening
   - Read first:
     - the tightened spec text from task 1
     - current explicit spec resolver in `weft/commands/specs.py`
     - current `--spec` command wiring in `weft/commands/run.py`
     - help text in `weft/cli.py`
   - Required action:
     - audit help text, command output, and error wording for builtin behavior
     - ensure all explicit-spec surfaces describe the same resolution order
     - make source labeling (`stored`, `builtin`, `file`) consistent where the
       CLI exposes it
     - keep builtin asset lookup centralized rather than duplicating path logic
   - Reuse:
     - `ResolvedSpecReference`
     - existing builtin asset helper in `weft/builtins/__init__.py`
   - Constraints:
     - no new lookup path
     - no new top-level command
     - no speculative abstraction beyond shared current helpers
   - Stop if:
     - the code change wants a second builtin lookup helper path
     - the change starts broad CLI cleanup unrelated to explicit spec surfaces
   - Done when:
     - runtime behavior and help text match the tightened contract exactly

3. Add black-box contract tests that make drift harder.
   - Outcome: builtin behavior is locked by tests at the same level users
     experience it.
   - Files to touch:
     - `tests/cli/test_cli_spec.py`
     - `tests/cli/test_cli_run.py`
     - add one additional narrow test file only if needed for package-data or
       per-builtin-doc coverage
   - Read first:
     - existing builtin tests
     - `tests/conftest.py` `run_cli()`
   - Tests to add or tighten:
     - explicit resolution order: file path, local stored spec, builtin fallback
     - local shadowing over builtin
     - builtin list/show output shape
     - builtin-only delete rejection
     - `--help` / command-reference wording for `--spec NAME|PATH`
     - package-data availability for builtin task assets
     - dedicated documentation presence for each shipped builtin entry in
       `10B-Builtin_TaskSpecs.md`
   - Keep real:
     - real CLI subprocesses through `run_cli()`
     - real builtin asset lookup from the package tree
   - Stop if:
     - the tests need to mock the spec resolver to prove the contract
   - Done when:
     - the public builtin contract is proven through CLI-visible behavior and
       asset presence rather than only through internal helper tests

4. Update README so the public user guide matches the shipped contract.
   - Outcome: README explains builtin helpers and explicit spec resolution in
     the same terms as the specs and CLI help.
   - Files to touch:
     - `README.md`
   - Read first:
     - tightened spec text from tasks 1 and 2
     - current README quick start and command reference sections
   - Required action:
     - add one clear explanation of builtin TaskSpecs as explicit helpers
     - keep the quick-start examples honest about `weft run --spec NAME|PATH`
     - mention local shadowing in one durable place rather than only in an
       inline comment
     - document `probe-agents` as an example helper without overselling it as a
       required setup step
   - Constraints:
     - README should explain the contract, not duplicate the full spec corpus
     - no startup magic or hidden probe language
   - Stop if:
     - the README starts documenting behavior the runtime does not implement
   - Done when:
     - the README is a trustworthy short version of the builtin contract and
       points readers to `10B-Builtin_TaskSpecs.md` for the full current details

5. Run a final drift pass before landing.
   - Outcome: the slice lands as one aligned contract update rather than as
     code, docs, and help that only mostly agree.
   - Files to touch:
     - any of the above, but only to reconcile proven mismatches
   - Required action:
     - compare the final README, spec text, CLI help, and test expectations
     - resolve wording mismatches in the same change
   - Verification:
     - one human-readable checklist in the final implementation note:
       README, specs, help, runtime, tests
   - Stop if:
     - this pass starts turning into a drive-by cleanup of unrelated docs
   - Done when:
     - the slice can be described as one coherent builtin contract

## Testing Plan

- `source .envrc && uv run pytest tests/cli/test_cli_spec.py -q`
- `source .envrc && uv run pytest tests/cli/test_cli_run.py -k 'spec or builtin' -q`
- `source .envrc && uv run ruff check weft tests`
- run one narrow packaging/asset test if task 3 adds it

If CLI help text changes, include at least one black-box `--help` assertion in
the touched CLI tests rather than trusting docs alone.

## Rollback

Rollback is straightforward if this slice stays disciplined:

- revert the builtin contract/code/help/tests together
- do not leave half-landed wording where README or specs describe rules the CLI
  does not implement
- if a packaging assertion reveals a distribution-specific problem, revert the
  packaging-only change separately and keep the contract clarification only if it
  still matches the shipped wheel behavior

