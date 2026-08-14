# SimpleBroker 7.3 Dump Watermark Compatibility Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.2], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-6]
Superseded by: none

Class: 5 — spec-changing and risky. The coordinated dependency floors and
persisted dump/load contract change, including a backend-plugin handshake and
header-only mutation during load.

## Goal

Upgrade Weft to published SimpleBroker 7.3.2 and the coordinated PostgreSQL
backend 3.8.0, then make Weft's dump/load validation, rollback classification,
tests, and documentation honor the SimpleBroker 7.3 bounded-watermark contract.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.2],
  [SB-0.4] governs supported SimpleBroker floors, exact IDs, and dump/load use.
- `docs/specifications/10-CLI_Interface.md` [CLI-6] governs operator-visible
  `weft system dump` and `weft system load` behavior.
- `../simplebroker/CHANGELOG.md` 7.3.0 and 7.3.1 describe the bounded dump,
  watermark restore, skew check, and backend API v7 pairing. Tag `v7.3.2`
  (`284059c1`) contains the isolated-config release even though its changelog
  entry remains under Unreleased.
- `../simplebroker/docs/specs/15-persistence-io.md` [SB-IO-1] through [SB-IO-4]
  define the upstream format and load behavior.
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-2] defines
  `resolve_isolated_config()` and the nominal `ResolvedConfig` handoff.
- `docs/agent-context/runbooks/testing-patterns.md` Rules 3–5 and Pattern 9
  require real broker proof and a failing regression first.

## Context and Key Files

Files to modify:

- `pyproject.toml` and `uv.lock`: coordinated core and PostgreSQL floors.
- `weft/commands/load.py`: Weft's full-file preflight, runtime-queue filter,
  normalized apply stream, and rollback diagnostics.
- `weft/_constants.py` and `weft/bootstrap.py`: the Weft-namespaced skew
  setting and the import-light CLI configuration-error boundary.
- `weft/context.py`, `weft/commands/interactive.py`, `weft/core/manager.py`,
  `weft/core/pipelines.py`, `weft/core/queue_wait.py`,
  `weft/core/spawn_requests.py`, `weft/core/tasks/base.py`, and
  `weft/core/tasks/multiqueue_watcher.py`: recreate or preserve the nominal
  marker at every project, Queue, broker, watcher, spawn, task, and pipeline
  handoff. Transported task state remains an ordinary picklable Weft mapping.
- `tests/commands/test_dump_load.py` and
  `tests/commands/test_dump_load_sqlite_only.py`: real SQLite contract proofs.
- `tests/cli/test_env_file_bootstrap.py`: subprocess proof that invalid
  SimpleBroker configuration cannot escape as an import-time traceback.
- `docs/specifications/04-SimpleBroker_Integration.md` and
  `docs/specifications/10-CLI_Interface.md`: dependency and dump/load contract.
- this plan and `docs/plans/README.md`: execution record and status index.

Read first:

- `weft/commands/dump.py`: delegates export to public `dump_lines()` while
  excluding `weft.state.*`; do not recreate upstream traversal.
- `weft/commands/load.py`: parses once for dry-run and destination conflict
  checks, filters runtime state, then sends canonical lines to `load_lines()`.
- `../simplebroker/simplebroker/_dump.py`: samples one `last_ts` bound, filters
  rows above it, validates `id <= last_ts`, checks clock skew, and advances the
  durable allocation floor after replay.
- `../simplebroker/simplebroker/_constants.py`: owns public
  `resolve_isolated_config()` and the immutable `ResolvedConfig` marker, while
  preserving public `InvalidConfigError` for mapped-value failures.

Comprehension checks:

1. Which checks belong to Weft's preflight because Weft filters or rebuilds the
   stream before upstream apply, and which remain owned by `load_lines()`?
2. Why is a header-only load now a mutation even when no aliases or messages
   follow it?
3. Why must `simplebroker-pg` move to 3.8.0 with core 7.3.2 or newer?

## Invariants and Constraints

- Runtime-only `weft.state.*` queues and aliases remain excluded from export
  and skipped on import, but malformed records are validated before skipping.
- Every applied message ID is positive and no greater than header `last_ts`.
- The original header watermark is preserved even when Weft filters records;
  header-only imports advance the destination allocation floor.
- SQLite apply failures restore the existing file snapshot. Non-file-backed
  diagnostics distinguish pre-mutation skew/capability rejection, known final
  floor failure, prior alias/message writes, and an outcome-ambiguous floor
  attempt; they do not label every apply failure as mutating.
- Preserve exact message IDs, queue names, alias-conflict exit 3, owner-only
  dump files, claimed-row omission reporting, and SimpleBroker dump v1.
- Use only public SimpleBroker root, `ext`, and command surfaces. Do not import
  `_dump`, duplicate its clock-skew math, or add a second export path.
- Add `WEFT_LOAD_MAX_FUTURE_SKEW_SECONDS` as the Weft-namespaced mapping to
  `BROKER_LOAD_MAX_FUTURE_SKEW_SECONDS`; SimpleBroker remains the sole parser
  and validator for the non-negative integer contract.
- Supply a complete broker config from Weft-owned defaults and explicit
  `WEFT_*` mappings so ambient `BROKER_*` values, valid or invalid, do not tune
  or reject Weft. Keep an exhaustive parity test against SimpleBroker's public
  resolved key set.
  Do not mutate process-global environment or import private config schema.
- Use SimpleBroker 7.3.2's public `resolve_isolated_config()` and preserve its
  nominal `ResolvedConfig` at every lower-layer ownership boundary. Ordinary
  dictionaries are not an isolated handoff because `resolve_config()` retains
  its environment-base compatibility behavior.
- Invalid mapped `WEFT_*` or explicit Weft-owned broker overrides, including
  the new skew setting, must not escape `python -m weft` as an import-time
  traceback. Invalid ambient `BROKER_*` is ignored. The import-light
  bootstrap catches public `simplebroker.ext.InvalidConfigError`, emits one
  safe redacted diagnostic, exits 1, and creates no broker target. Do not
  duplicate SimpleBroker's config-value formatting or expose sensitive values.
- Update the [SB-0] implementation mapping to name `weft/commands/dump.py`,
  and map the new configuration/error boundary to `weft/_constants.py` and
  `weft/bootstrap.py`; do not leave ownership implicit in plan prose.
- Do not add `--force` to Weft in this slice. Upstream default skew refusal is
  enforced on apply; a new Weft safety-override surface is a separate public
  API decision, not required for compatibility with default 7.3 behavior.
- Tests use real SQLite brokers for dump/load effects. Mocking is limited to
  injected failures needed to prove rollback diagnostics.

## Spec Baseline

- `410aaeacf77dc550fbb6c0dc65658361475a787e` — governing specs at plan
  authoring time.
- Upstream compatibility baseline: local `../simplebroker` tag `v7.3.2`
  (`284059c1`) and published PyPI versions SimpleBroker 7.3.2 /
  `simplebroker-pg` 3.8.0, checked 2026-08-13.
- Plan type: implementation with spec revision.
- Promotion baseline: `410aaeacf77dc550fbb6c0dc65658361475a787e` plus
  the current worktree diff for `docs/specifications/04-SimpleBroker_Integration.md`
  and `docs/specifications/10-CLI_Interface.md`. The reviewed strategy-B delta
  is promoted atomically with code and tests; verify with
  `git diff 410aaeac --` over the plan's delta table.

## Proposed Spec Delta

Promotion strategy: **B — atomic**. The delta is small and its implementation
mapping already points at the touched command modules, so contract text, tests,
and code land together without reciprocal-link debt.

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1]

Replace the current supported floors with:

> Weft requires SimpleBroker 7.3.2 or newer. Installations using the optional
> PostgreSQL backend require `simplebroker-pg` 3.8.0 or newer. These paired
> floors provide backend API v7 and the bounded dump-watermark contract.

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Extend the dump/load operational notes with:

> SimpleBroker dump v1 header `last_ts` is an inclusive export bound and a
> destination allocation floor. Every message record must satisfy
> `0 < id <= last_ts`. Load advances the destination high-water to at least
> `last_ts` after replay, including for a header-only dump, so later generated
> IDs are greater than the source floor.
> Weft validates the bound before destination writes, preserves the original
> header while filtering runtime-only records, and delegates skew enforcement
> and durable floor advancement to public `load_lines()`.

Add the isolated embedding contract from upstream [SB-API-2]:

> Weft constructs a complete, enumerable SimpleBroker mapping from Weft-owned
> defaults and explicit `WEFT_*` or Weft-owned overrides. It resolves that map
> with public `resolve_isolated_config()` and preserves or recreates the
> immutable `ResolvedConfig` marker at every lower-layer ownership handoff.
> Queue, project discovery, watcher, broker, spawn, interactive, pipeline, and
> dump/load operations therefore do not reread ambient `BROKER_*`, including
> malformed values. Ordinary transport state remains a picklable Weft mapping;
> the marker is recreated only at the SimpleBroker boundary. Schema additions,
> removals, or renames fail closed until Weft's complete mapping is updated.

### `docs/specifications/10-CLI_Interface.md` [CLI-6]

Extend the system dump/load bullets with:

> - `system dump` inherits SimpleBroker's bounded live export: the header
>   `last_ts` is sampled once and no emitted message ID exceeds it. This is not
>   a frozen point-in-time snapshot for aliases, claims, moves, or deletes.
> - `system load` rejects any message ID above header `last_ts` before writes,
>   including records later skipped as runtime-only state. Apply advances the
>   destination high-water to at least the header floor, including for
>   header-only dumps.
>   SimpleBroker's default future-clock-skew warning and refusal apply during
>   import; Weft exposes the limit as
>   `WEFT_LOAD_MAX_FUTURE_SKEW_SECONDS`, mapped to
>   `BROKER_LOAD_MAX_FUTURE_SKEW_SECONDS`, but does not expose the upstream
>   `force` override. Invalid mapped `WEFT_*` or explicit Weft-owned broker
>   configuration is rendered as one safe CLI error with no traceback before
>   broker target creation; malformed ambient `BROKER_*` is ignored.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| None — dependency integrity | Only coordinated SimpleBroker packages change. | Add direct `httpx>=0.28`. | A clean `uv sync` removed an incidental `httpx` install, then `llm 0.32` failed at import because it imports `httpx` without declaring it. The user explicitly authorized making the runtime dependency direct. | N/A — packaging correction, no Weft behavior contract change. |
| [SB-0.4] — embedding config | Add only the new dump-skew mapping. | Complete every SimpleBroker config key from a Weft-owned default or mapping. | User reported that ambient SimpleBroker tuning leaked through unset Weft mappings. The full map makes the intended namespace isolation mechanical and release-auditable. | Promoted into [SB-0.4] context contract in this slice. |

## Tasks

1. Add red compatibility regressions.
   - Add an exhaustive public-key parity test and a behavioral test proving a
     full set of valid ambient `BROKER_*` values cannot change Weft's broker
     config when the corresponding `WEFT_*` values are unset.
   - Add firing tests for each embedding owner: context Queue/broker, automatic
     project discovery, watcher dispatch, dump, load, direct pre-context spawn,
     interactive Queue creation, and manager/task process spawn. Assert the
     same operation succeeds with malformed ambient `BROKER_*`; inspect marker
     type at representative handoffs. Add schema addition/removal/rename and incomplete
     handoff tests so the completeness gate cannot become a no-op.
   - Add real-parser tests proving `id > last_ts` is rejected before writes,
     including a runtime-only queue record that Weft would otherwise skip.
   - Add a header-only load test proving the destination's next generated ID
     exceeds the restored header.
   - Add diagnostic tests that distinguish excessive-skew pre-mutation
     refusal, known final-floor failure, prior alias/message writes, and
     `TimestampError.outcome_ambiguous` without duplicating upstream's
     `advance_last_timestamp` capability check.
   - Add a subprocess CLI test for invalid mapped
     `WEFT_LOAD_MAX_FUTURE_SKEW_SECONDS`: exit 1, one safe diagnostic, no
     traceback, no target file, and no echoed secret/config payload. Prove an
     invalid ambient `BROKER_LOAD_MAX_FUTURE_SKEW_SECONDS` is ignored under
     7.3.2 isolated resolution.
   - Add no-op-prevention tests for a valid mapped value: prove
     `WEFT_LOAD_MAX_FUTURE_SKEW_SECONDS` becomes a typed
     `BROKER_LOAD_MAX_FUTURE_SKEW_SECONDS`, then hold the dump constant and
     show that changing only the Weft value changes excessive-skew refusal to
     warning-and-proceed behavior.
   - Verify the regressions fail against SimpleBroker 7.1/current Weft for the
     expected reasons before implementation.

2. Promote the spec delta and implement the smallest compatibility changes.
   - Update the two specs and their Related Plans backlinks.
   - Update the [SB-0] implementation mapping to include
     `weft/commands/dump.py`, `weft/_constants.py`, `weft/bootstrap.py`,
     `weft/context.py`, `weft/commands/interactive.py`, `weft/core/manager.py`,
     `weft/core/pipelines.py`, `weft/core/queue_wait.py`,
     `weft/core/spawn_requests.py`, `weft/core/tasks/base.py`, and
     `weft/core/tasks/multiqueue_watcher.py` for the bounded export and each
     isolated configuration handoff they own.
   - Validate every parsed message against header `last_ts` before runtime
     filtering; keep the normalized header unchanged.
   - Continue requiring callable `insert_messages` only when message records
     exist. Rely on public `load_lines()` for the API-v7 timestamp-floor
     capability check, which it performs before consuming input or mutation.
   - Track mutation evidence precisely for non-file-backed diagnostics. Use
     public `TimestampError.outcome_ambiguous`; do not infer that a rejected
     skew check mutated the target.
   - Add the Weft-to-broker skew mapping and catch public `InvalidConfigError`
     at the import-light bootstrap around CLI-app import/invocation.
   - Stop and re-plan if compatibility requires private SimpleBroker APIs,
     duplicated skew policy, or a new dump format.

3. Upgrade the coordinated packages and lockfile.
   - Set `simplebroker>=7.3.2` and every `simplebroker-pg` floor to `>=3.8.0`.
   - Regenerate only the relevant lock resolution with the repo environment.
   - Verify installed versions and the backend API handshake.
   - Update every handcrafted v1 fixture: when message rows are intended to be
     valid, set header `last_ts >= max(id)`. Keep separate deliberately invalid
     `id > last_ts` fixtures. Confirm SQLite rollback tests reach apply and
     rollback rather than passing through earlier validation.

4. Reconcile traceability and verify.
   - Update implementation notes and plan evidence/status without recording
     transient worktree claims.
   - Run targeted dump/load tests, CLI system tests, spec gates, full default
     tests, mypy, and ruff.
   - Run an independent completed-work review and disposition every finding.

## Testing Plan

- Red/green: targeted tests in `tests/commands/test_dump_load.py` and
  `tests/commands/test_dump_load_sqlite_only.py` against real SQLite brokers.
- Neighboring behavior: `tests/commands/test_system_public_contract.py` and
  `tests/cli/test_cli_system.py`.
- Exact observable evidence: exported rows never exceed header; invalid input
  leaves aliases and messages unchanged; header-only load advances the next
  real queue write; within-limit future skew warns and proceeds; excessive
  skew refuses before mutation; rollback restores SQLite state.
- Do not mock `dump_lines`, `load_lines`, or broker connections for contract
  success paths.

## Verification and Gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py -q
./.venv/bin/python -m pytest tests/commands/test_system_public_contract.py tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/cli/test_env_file_bootstrap.py -q
./.venv/bin/python -m pytest tests/system/test_constants.py tests/system/test_helpers.py tests/context/test_context.py -q
./.venv/bin/python -m pytest tests/core/test_spawn_requests.py tests/core/test_queue_wait.py tests/tasks/test_multiqueue_watcher.py tests/commands/test_interactive_client.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -c "from importlib.metadata import version; from simplebroker.ext import BACKEND_API_VERSION; from simplebroker_pg import get_backend_plugin; assert version('simplebroker') == '7.3.2'; assert version('simplebroker-pg') == '3.8.0'; assert BACKEND_API_VERSION == 7; assert get_backend_plugin().backend_api_version == BACKEND_API_VERSION"
```

When `SIMPLEBROKER_PG_TEST_DSN` is configured, also run the repository's
PG-marked dump/load and context gates against `simplebroker-pg` 3.8.0; absent
that external service, report PG runtime verification as not run rather than
inferring it from SQLite.

Post-upgrade observation: `weft system dump` followed by a header-only and a
normal `weft system load` succeeds on SQLite; environments using PostgreSQL
must install core 7.3.2 or newer and PG 3.8.x together. A package-only rollback
to SimpleBroker 7.3.1 is forbidden because Weft imports the 7.3.2-only isolated
resolver and marker; Weft code and SimpleBroker core must roll back atomically.
Before rollout, recreate or
validate old dumps made during active writes because rows above their header
are invalid under 7.3. Package/code rollback is coordinated, but persisted
dump semantics are not fully backward-safe: do not load a 7.3 dump with 7.1
when header H exceeds the highest restored row (for example header-only or
claimed-row-omitting dumps), because 7.1 ignores H and can later allocate
below the source floor.

## Independent Review Loop

An independent agent reviews this plan, the Proposed Spec Delta, upstream
`[SB-IO-1]`–`[SB-IO-4]`, and the touched Weft code before implementation. A
second review examines the completed diff for contract drift, rollback errors,
missing firing tests, and unnecessary API expansion. Findings and dispositions
are recorded below.

## Review Log

| Stage | Finding | Disposition |
|-------|---------|-------------|
| Plan review 1 | BLOCKED: migrate `last_ts=0` fixtures with positive rows; distinguish non-mutating and ambiguous failures; document persisted-data rollback edge; add bounded/skew and PG gates; avoid duplicate capability check; map `dump.py`; use monotone floor wording. | Accepted. Plan tasks, tests, invariants, spec text, rollout, and verification updated before implementation. |
| User correction | Include the skew constant and guard invalid configuration from immediate traceback. | Accepted. Add the Weft mapping and import-light public `InvalidConfigError` boundary with subprocess firing tests. |
| User correction | Audit the Weft/SimpleBroker embedding boundary; ambient settings must not leak across namespaces. | Accepted. Complete the Weft-owned defaults, add schema-parity and full ambient-isolation tests, and document the upstream malformed-ambient limitation. |
| SimpleBroker 7.3.2 follow-up | Replace the malformed-ambient limitation with the new public isolated resolver and nominal marker. | Accepted. Upgrade the floor, use `resolve_isolated_config()`, recreate `ResolvedConfig` at Weft ownership boundaries, and add real invalid-ambient CLI/context tests. |
| Plan review 3 | FAIL: the 7.3.2 delta and upstream [SB-API-2] source were missing; ownership and firing evidence were incomplete; invalid ambient wording and rollback floor were stale. | Accepted. The exact embedding delta, all owners, per-boundary tests, ambient-vs-mapped diagnostics, release evidence, and atomic 7.3.2 rollback rule are now explicit. |
| Completed-work review 3 | BLOCKED: the max-message-size helper still used ambient resolution, and incomplete ownership mappings could be silently filled upstream. | Accepted. Use the Weft-owned max-message-size default, require the exact broker key set at every freeze, translate upstream schema rejection, and fire addition/removal/rename/incomplete tests. |
| Plan review 4 | PASS: the exact [SB-API-2] delta, complete ownership map, firing gates, diagnostic split, release evidence, and atomic rollback rule disposition all prior findings. | Accepted with no further action. |
| Completed-work review 4 | PASS: exact 32-key enforcement, helper isolation, picklable manager transport, marker recreation, and firing tests are correct; no private upstream APIs remain. | Accepted with no further action. Full default tests, focused tests, ruff, mypy, spec gates, lock check, diff check, and public API-v7 handshake pass. PostgreSQL runtime tests were not run because no DSN was configured. |
| Plan review 2 | FAIL: mapping ownership still implicit; always-run handshake gate absent; CLI delta used exact-restore wording; valid mapped setting lacked a firing/no-op-prevention test. | Accepted. Exact mapping files, structural API-v7 gate, monotone CLI wording, and mapped-value behavior test are now required. |
| Completed-work review 1 | BLOCKED: input presence falsely implied writes; capability absence was masked; mutation could commit then raise; header-only coverage contained an alias. | Accepted. Mutation attempts are observed at the broker boundary, structural capability absence is preserved and tested through real `load_lines()`, post-commit failure is covered, and the floor test is truly header-only. |
| Completed-work review 2 | No blocker. One stale tracker docstring described successful mutations instead of conservative attempts. | Accepted. Wording corrected; final focused dump/load, embedding, lint, type, and diff gates pass. |

## Out of Scope

- A new Weft `--force` clock-skew override.
- Redis support, which Weft does not currently expose as an optional backend.
- Claimed-row backup or a point-in-time snapshot protocol.
- Refactoring the cohesive load parser or changing dump v1.

## Fresh-Eyes Review

The first review correctly found that the initial draft undercounted fixture
blast radius, overclaimed mutation risk, and treated package rollback as if it
also reversed persisted semantics. Those defects are corrected above. The
remaining design is deliberately narrow: one Weft preflight rule is needed
because filtering can hide invalid records from upstream; upstream retains
ownership of skew math, capability validation, and floor mutation.

## Completion Evidence

- Focused dump/load, isolated-config, ownership-handoff, and spec-gate suite:
  passed on 2026-08-14.
- Full default suite: 4,200 passed and 2 expected backend-selection skips on
  2026-08-14.
- Ruff check and format check: passed on 2026-08-14.
- Mypy: passed for all first-party source packages on 2026-08-14.
- Installed-package handshake: SimpleBroker 7.3.2, `simplebroker-pg` 3.8.0,
  and backend API v7 matched on 2026-08-14.
- PostgreSQL runtime verification was not run during implementation because no
  external test DSN was configured; the release preflight remains responsible
  for the Docker-backed PostgreSQL suite.
