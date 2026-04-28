# Config Precedence and Parsing Alignment Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

This plan fixes the real configuration defects verified in the current Weft
codebase without widening scope into unrelated config surfaces. The goal is to
make broker-target resolution deterministic and documented, keep Weft's
`WEFT_*` alias layer honest across all `build_context()` paths, reject
ambiguous Postgres env input instead of silently picking one shape, and make
invalid manager-timeout env values fail loudly rather than silently falling
back to defaults.

This is boundary work. It touches:

`load_config() -> build_context() -> SimpleBroker target resolution -> manager bootstrap/docs/tests`

The likely failure modes are:

- documenting a rule that the code still does not follow,
- fixing only the explicit-root path while leaving auto-discovery inconsistent,
- dragging unrelated config domains into the broker precedence model,
- creating a second project-discovery path outside `build_context()`,
- or broadening env parsing semantics in ways that silently change existing
  behavior beyond the verified bug.

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. document the actual broker-config domains and one explicit precedence rule
2. make `.broker.toml` authoritative for broker target-shaping fields
   when it exists at the selected project root
3. keep env-selected backend synthesis working when no project target file
   exists
4. make both explicit-root and auto-discovery context paths honor the same
   Weft-resolved config model
5. reject ambiguous Postgres env configuration when a full target DSN is mixed
   with decomposed host/port/user/database fields
6. centralize Weft-owned env parsing so invalid
   `WEFT_MANAGER_LIFETIME_TIMEOUT` values fail clearly instead of silently
   defaulting
7. clean up the docs so they stop implying that `.weft/agents.json`,
   `.weft/config.json`, or TaskSpec metadata participate in broker precedence

Do not implement any of the following in this slice:

- a broad SimpleBroker config refactor outside Weft's current boundary
- a second root-discovery algorithm outside `build_context()`
- TaskSpec metadata precedence changes
- `.weft/agents.json` semantics changes beyond clearer documentation that it is
  not a broker-target source
- a logging-bool behavior change for `WEFT_LOGGING_ENABLED`
- a new CLI flag or user-facing override surface
- a cleanup of every stale env-var mention in the repo that is unrelated to the
  touched config contract

If implementation pressure pulls toward any excluded item, stop and split a
follow-on plan.

## Goal

Make Weft's configuration contract small, explicit, and mechanically
consistent. Broker target selection should have one documented precedence rule.
`WEFT_*` aliases should flow through one Weft-owned parse path. Invalid env
input should be rejected clearly. Unrelated project metadata and delegated-agent
settings should remain separate config domains.

## Design Position

The fix should lock in four rules:

- `.broker.toml` is the authoritative broker target contract for a
  project when present.
- `WEFT_*` is Weft's public env alias layer over `BROKER_*`; within Weft-owned
  config loading, translated `WEFT_*` values are resolved once and then reused.
- env-selected backend synthesis exists only when no project target file is
  present.
- `.weft/config.json`, `.weft/agents.json`, and TaskSpec `metadata` are not
  broker target sources and must not be documented or implemented as such.

This is the steelman position because it preserves reproducible project-local
broker targets, avoids hidden env overrides against checked-in project config,
and keeps Weft from collapsing unrelated config domains into one accidental
precedence tree.

## Source Documents

Primary specs:

- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0], [SB-0.4]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-5]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.5]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-5], [AR-7]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1.4]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [IMPL.5], [IMPL.6]

Local guidance and review standards:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Existing plans to align with, not replace:

- [`docs/plans/2026-04-06-weft-backend-neutrality-plan.md`](./2026-04-06-weft-backend-neutrality-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)

Source spec note:

- no current spec section defines the exact precedence between
  `.broker.toml`, env-selected backend synthesis, and Weft's `WEFT_*`
  alias layer
- no current spec section clearly says that `.weft/agents.json`,
  `.weft/config.json`, and TaskSpec `metadata` are outside the broker-target
  precedence model
- this plan includes doc alignment to restore that missing normative boundary

## Context and Key Files

Files that will likely change in this slice:

- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/00-Quick_Reference.md`
- `weft/_constants.py`
- `weft/context.py`
- `tests/system/test_constants.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`

Read first:

1. [`weft/_constants.py`](../../weft/_constants.py)
2. [`weft/context.py`](../../weft/context.py)
3. [`weft/commands/init.py`](../../weft/commands/init.py)
4. [`weft/core/manager.py`](../../weft/core/manager.py)
5. [`weft/commands/_manager_bootstrap.py`](../../weft/commands/_manager_bootstrap.py)
6. [`tests/system/test_constants.py`](../../tests/system/test_constants.py)
7. [`tests/context/test_context.py`](../../tests/context/test_context.py)
8. [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)

Current structure:

- `weft/_constants.py` parses Weft-owned env vars through a mixed path:
  `_parse_bool(...)`, a special strict `"1"` check for
  `WEFT_LOGGING_ENABLED`, and one-off `float(...)` parsing for
  `WEFT_MANAGER_LIFETIME_TIMEOUT`
- `load_config()` translates `WEFT_*` broker aliases into `BROKER_*`, applies
  Weft defaults, then calls SimpleBroker `resolve_config(...)`
- `build_context(spec_context=...)` passes that resolved config to
  `simplebroker.target_for_directory(...)`
- if `root/.broker.toml` exists, SimpleBroker currently resolves that
  project target through its own project-config path and reloads raw
  `BROKER_*` env internally, which bypasses Weft's translated `WEFT_*` alias
  layer for TOML-backed non-sqlite projects
- `build_context()` without `spec_context` uses
  `simplebroker.resolve_broker_target(...)`, which has the same drift when the
  discovered target comes from `.broker.toml`
- `.weft/config.json` is Weft project metadata loaded by `context.py`; it is
  not a broker target source
- `.weft/agents.json` is delegated-agent launch config only when a delegated
  TaskSpec does not pin an executable; it is not a broker target source
- TaskSpec `metadata` is caller-owned by spec and is not a broker target source
- manager idle timeout is consumed from `taskspec.metadata["idle_timeout"]`
  first and falls back to `config["WEFT_MANAGER_LIFETIME_TIMEOUT"]`

Shared helpers and paths to reuse:

- `load_config()` in `weft/_constants.py`
- `build_context()` and `_resolve_root_and_target()` in `weft/context.py`
- existing SimpleBroker root discovery via `resolve_broker_target(...)` and
  `target_for_directory(...)`
- current broker-target dataclass exposed as `simplebroker.BrokerTarget`

Do not create a second broad root-discovery implementation. Keep root selection
inside `build_context()` and only replace the broken project-target resolution
step that currently drops Weft's resolved config on TOML-backed non-sqlite
paths.

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which current code path makes TOML-backed non-sqlite target resolution honor
   raw `BROKER_*` env instead of the already-resolved Weft config?
2. Which config surfaces are broker target selectors, which are broker tuning,
   and which are unrelated project metadata?
3. Where is manager idle timeout parsed today, and where is it consumed after
   manager bootstrap?

## Invariants and Constraints

The following must stay true:

- `build_context()` remains the canonical entry point for project resolution
- the current durable spine and queue/state contracts remain unchanged
- TIDs, queue names, and forward-only state transitions remain unchanged
- `spec` and `io` remain immutable after resolved TaskSpec creation
- `.broker.toml` remains a project-scoped broker contract, not advisory
- `.weft/config.json`, `.weft/agents.json`, and TaskSpec `metadata` remain
  separate config domains
- env-only Postgres selection remains supported when no `.broker.toml`
  exists
- manager `metadata.idle_timeout` remains the immediate runtime override over
  config defaults
- generic broker tuning env values such as busy timeout, logging, cache, and
  similar non-target settings must still flow into the resolved broker config
- `WEFT_LOGGING_ENABLED` keeps its current strict compatibility behavior in this
  slice unless the user asks to change it separately

Review gates for this slice:

- no new execution path
- no new dependency
- no drive-by refactor
- no broad reimplementation of SimpleBroker project search
- no mock-heavy substitute for the existing context and config proofs
- no spec drift between touched docs and code

Fatal vs best-effort rules:

- invalid Weft env config for touched fields is fatal at config-load time
- corrupt `.weft/config.json` recovery remains best-effort and is out of scope
- unrelated delegated-agent settings parse behavior remains unchanged

## Rollout and Rollback

Rollout order matters:

1. lock the documentation and precedence vocabulary first
2. centralize Weft-owned env parsing and explicit conflict validation
3. switch project-target resolution to a helper that reuses the already
   resolved Weft config on both explicit-root and auto-discovery paths
4. land targeted regression tests for both context paths and env parsing
5. run an independent review on the completed slice

Rollback rule:

- the slice must be revertible without queue migration, TaskSpec migration, or
  broker-data migration

Compatibility rule:

- env-only backend selection remains backward-compatible
- checked-in `.broker.toml` becomes explicitly authoritative for
  broker target-shaping fields; callers that relied on raw env overriding a
  TOML-backed project target must now edit the project file or remove it
- no existing docs or tests promise raw env override over `.broker.toml`,
  so this is a contract clarification plus bug fix, not a spec regression

## Tasks

1. Lock the config domains and precedence rule in the specs before code changes.
   - Outcome: the specs say exactly which files and env surfaces participate in
     broker target resolution, and which do not.
   - Files to touch:
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/00-Quick_Reference.md`
   - Read first:
     - `weft/context.py`
     - `weft/_constants.py`
     - `weft/core/agents/provider_cli/settings.py`
     - `docs/specifications/02-TaskSpec.md` [TS-1.4]
     - `docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-7]
   - Required doc changes:
     - document two context-selection cases explicitly:
       explicit root via `--context` or `spec_context`, and auto-discovery via
       `Path.cwd()`
     - document broker target precedence:
       `project .broker.toml` first; env-selected non-sqlite backend only
       when no project target file exists; sqlite fallback after that
     - document that `.weft/config.json`, `.weft/agents.json`, and TaskSpec
       `metadata` are not broker target sources
     - document that `WEFT_MANAGER_LIFETIME_TIMEOUT` must parse as a
       non-negative float
     - replace or correct stale quick-reference env entries that no longer
       describe the real config surface
   - Stop-and-re-evaluate gate:
     - if the docs start inventing a broader precedence tree that includes
       unrelated project metadata, stop and narrow back to the verified broker
       config domain
   - Verification:
     - manually read the touched sections and confirm that a zero-context reader
       can answer the three comprehension questions above without reading code

2. Centralize Weft-owned env parsing and explicit conflict validation in
   `weft/_constants.py`.
   - Outcome: one Weft-owned parse path loads all Weft env fields touched by
     this slice, and invalid input fails clearly instead of silently defaulting.
   - Files to touch:
     - `weft/_constants.py`
     - `tests/system/test_constants.py`
   - Read first:
     - current `_load_weft_env_vars()`
     - current `SIMPLEBROKER_ENV_MAPPING`
     - current `tests/system/test_constants.py`
   - Required implementation details:
     - introduce one shared normalizer table or equivalent single-entry parse
       path for Weft-owned env keys
     - keep the current strict `"1"` semantics for `WEFT_LOGGING_ENABLED` in
       this slice, but express that behavior through the same shared parse path
     - parse `WEFT_MANAGER_LIFETIME_TIMEOUT` through a named float validator
       that rejects non-numeric and negative values with a clear error message
     - add explicit validation for ambiguous Postgres env input:
       when the resolved backend is `postgres` and a non-empty full target DSN
       is present, reject simultaneous host, port, user, or database fields
     - allow password and schema to remain separate fields because they are
       already treated as orthogonal by the Postgres plugin contract
     - document `WEFT_*` alias precedence over raw `BROKER_*` within Weft-owned
       config loading, but do not add a new conflict rule between the alias and
       the underlying raw key in this slice
   - Tests to add or update:
     - invalid `WEFT_MANAGER_LIFETIME_TIMEOUT` values such as `"true"` and
       `"junk"` raise instead of silently defaulting
     - negative timeout values raise instead of silently defaulting
     - valid float values still parse correctly
     - Postgres DSN plus host or port or user or database is rejected
     - env-only `WEFT_BACKEND_*` translation still yields typed `BROKER_*`
       fields
   - Stop-and-re-evaluate gate:
     - if the shared parse path starts broadening unrelated env semantics, such
       as changing logging-bool compatibility, stop and split that decision into
       a separate slice
   - Verification:
     - `./.venv/bin/python -m pytest tests/system/test_constants.py -q`

3. Make project-config target resolution reuse the already-resolved Weft config
   on both context paths.
   - Outcome: TOML-backed project targets follow the same Weft config model as
     env-only projects, and `.broker.toml` becomes authoritative for
     target-shaping fields.
   - Files to touch:
     - `weft/context.py`
     - `tests/context/test_context.py`
     - `tests/cli/test_cli_init.py`
   - Read first:
     - `_resolve_root_and_target()` in `weft/context.py`
     - `simplebroker.target_for_directory(...)`
     - `simplebroker.resolve_broker_target(...)`
     - current project-config tests in `tests/context/test_context.py`
   - Required implementation details:
     - add one narrow helper in `weft/context.py` that resolves a
       `.broker.toml` file using the already-resolved Weft config
     - that helper should:
       - load and validate the current project config file format locally in
         Weft using the existing on-disk contract (`version`, `backend`,
         `target`, optional `backend_options`)
       - keep sqlite target resolution relative to the project root
       - resolve non-sqlite backends through the backend plugin using the
         already-resolved Weft config, not a fresh raw-env reload
       - construct a `BrokerTarget` carrying `project_root`, `config_path`, and
         backend options
     - do not take a new dependency version or add a broad private
       SimpleBroker project-config import just to fix this slice
     - explicit-root path:
       - if `root/.broker.toml` exists, use the helper directly
       - otherwise keep using `target_for_directory(root, config=config)`
     - auto-discovery path:
       - keep using `resolve_broker_target(start_dir, config=config)` for root
         discovery
       - if the discovered target has a `config_path`, re-resolve it through the
         new helper so the final target uses the Weft-resolved config
       - keep the existing env-only and legacy sqlite discovery behavior for
         non-TOML cases
   - Tests to add or update:
     - explicit-root `.broker.toml` sqlite project still resolves to the
       TOML-defined target
     - explicit-root `.broker.toml` Postgres project ignores raw and
       translated env target-shaping fields
     - env-only Postgres project still honors translated `WEFT_BACKEND_*`
       values
     - auto-discovered `.broker.toml` Postgres project follows the same
       rule as the explicit-root path
   - Stop-and-re-evaluate gate:
     - if implementing the helper requires a broad duplicate of SimpleBroker
       root-discovery behavior, stop and keep discovery on the existing public
       path
   - Verification:
     - `./.venv/bin/python -m pytest tests/context/test_context.py tests/cli/test_cli_init.py -q`

4. Tighten manager-timeout contract documentation and keep runtime precedence
   stable.
   - Outcome: the docs and tests describe the real precedence for manager idle
     timeout without changing the runtime override order.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `tests/system/test_constants.py`
     - read-only confirmation in:
       - `weft/commands/_manager_bootstrap.py`
       - `weft/core/manager.py`
   - Read first:
     - `_build_manager_spec()` in `weft/commands/_manager_bootstrap.py`
     - `Manager.__init__()` in `weft/core/manager.py`
   - Required implementation details:
     - keep `metadata.idle_timeout` as the immediate runtime override
     - keep config fallback behavior only for valid parsed config values
     - document that invalid env input fails before manager bootstrap rather
       than silently turning into a default timeout
   - Stop-and-re-evaluate gate:
     - if this task starts changing manager metadata precedence, stop; that is a
       different contract change
   - Verification:
     - confirm that manager bootstrap tests still rely on the same metadata
       precedence after the env parser change

5. Final verification, doc traceability, and review.
   - Outcome: the slice is proved through targeted tests, spec alignment, and
     an independent review pass.
   - Files to touch:
     - touched specs and code from tasks 1-4
   - Required actions:
     - add `## Related Plans` backlinks in the touched specs when missing
     - keep module docstrings or nearby code comments aligned if the config
       ownership boundary moved materially
     - run the narrowest relevant tests first, then expand only if failures
       suggest broader blast radius
     - request an independent review using the guidance in
       `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
   - Suggested verification commands:
     - `./.venv/bin/python -m pytest tests/system/test_constants.py tests/context/test_context.py tests/cli/test_cli_init.py -q`
     - `./.venv/bin/python -m mypy weft`
     - `./.venv/bin/ruff check weft`
   - Observable success:
     - invalid `WEFT_MANAGER_LIFETIME_TIMEOUT=true` fails loudly
     - env-only Postgres init still works
     - TOML-backed Postgres project target no longer changes when raw or
       translated env target-shaping fields are set
     - touched docs give one explicit broker precedence rule and explicitly
       exclude `.weft/agents.json`, `.weft/config.json`, and TaskSpec metadata
       from that rule

## Out of Scope

- changing raw SimpleBroker `BROKER_*` parsing semantics in the dependency
- broad cleanup of provider settings or delegated-agent precedence
- changing `WEFT_LOGGING_ENABLED` compatibility semantics
- adding a general config schema system for every Weft env var
- changing manager metadata override semantics
- changing task, queue, or broker persistence contracts

## Open Questions

These questions should be resolved during implementation review, not left
implicit:

1. CLI surfaces will likely want to wrap config-load failures into friendlier
   user-facing messages, but the source of truth for invalid
   `WEFT_MANAGER_LIFETIME_TIMEOUT` should still be a direct config-load error
   from `load_config()`. Confirm that all touched command paths can surface
   that clearly.
2. Do any release or operator workflows intentionally depend on env overriding
   TOML-backed Postgres targets today? No tests or docs currently prove that,
   but implementation review should still check for hidden callers before final
   landing.
