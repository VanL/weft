# SimpleBroker 8.2 Configuration Migration

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-5]; docs/specifications/14-Python_API_Surfaces.md [PY-1]
Superseded by: none

Class: 5. This migration changes the broker configuration boundary and its normative description. Hardening applies because context snapshots cross CLI, manager, worker, and watcher lifetimes.

## Goal

- [x] Upgrade to SimpleBroker 8.2.1 and simplebroker-pg 4.2.1, including the public Config JSON transport API.
- [x] Use the public Config, DEFAULT_CONFIG, and resolve_config API, removing duplicated upstream defaults and obsolete schema/isolation guards.
- [x] Retire tests of removed machinery while proving Weft namespace, precedence, snapshot custody, and real broker behavior.
- [x] Identify further simplifications and distinguish safe removals from policy that Weft must still own.

## Source Documents and Spec Baseline

Plan type: implementation with spec revision. Baseline: HEAD `34ca4bad82b56e917f20f69b0bbb66a755806128` plus the starting worktree captured in `/tmp/weft-sb82-baseline-path` (that file points to a complete file snapshot). The governing integration and CLI specs have no pre-existing edits. Other files contain substantial unrelated work; compare to the captured starting bytes, never revert to HEAD.

Read the canonical agent-context hub, principles, runtime/context and testing runbooks, writing/hardening/review runbooks, and lessons. Governing behavior: [SimpleBroker integration](../specifications/04-SimpleBroker_Integration.md) [SB-0.1], [SB-0.4] and Current Context API; [CLI](../specifications/10-CLI_Interface.md) [CLI-5]. Also preserve [system invariants](../specifications/07-System_Invariants.md). Release evidence: SimpleBroker 8.2.0 CHANGELOG and public configuration code in the sibling repository, verified against published packages.

## Current Structure and Intended Ownership

`weft/_constants.py` owns all environment loading. At the starting baseline it duplicated SimpleBroker field names/defaults, translates WEFT inputs into BROKER keys, resolves an isolated snapshot, and rejects upstream key-set changes. `load_config()` returns an ordinary dictionary used in process transport. `freeze_broker_config()` rebuilds the lower-layer snapshot; context, pipelines and watchers retain it. That baseline used ordinary mappings for transport because its Config API did not provide the required JSON codec.

Reuse these owners, replacing the loader and override catalogs with one combined field table: DEFAULT_CONFIG plus changed/added Weft ConfigField entries (default, description, validator). `load_config()` returns a canonical, read-only Config with unprefixed keys. Environment and explicit override inputs use the WEFT prefix only. Update all consumers to unprefixed keys; do not retain a legacy dual-key dictionary facade. Config is assumed to support JSON serialization in the target SimpleBroker release. Use that public JSON round-trip API at process boundaries; do not introduce pickle or an alternate Weft Config codec. Receiving processes attach the locally imported shared field declarations. The provisional plain-dictionary process transport is replaced by the released JSON API. Replace freeze_broker_config and remove manual mapping inventories, mirrored storage defaults, compatibility guards, duplicate environment and override catalogs, and their policing tests.

Preserve field semantics through existing domain parsers and small typed adapters used directly as ConfigField validators. Do not retain the enum/rule dispatch object. ConfigField descriptions must describe units and constraints. Nullable defaults remain valid. Removed TaskMonitor inputs keep loud migration errors; removed vacuum-lock inputs remain ignored. An invalid earlier source may warn and then be repaired by a valid later override, as native SimpleBroker resolution specifies. Invalid final values raise InvalidConfigError. Unknown properly namespaced custom override fields remain accepted. Preserve the current explicit mode-override derivation of external log enablement; direct flag overrides win. The private serve-active marker becomes canonical MANAGER_SERVE_LOG_ACTIVE and remains excluded from environment loading. Actual process environment constants remain prefixed; runtime lookup keys are unprefixed.

Comprehension checks: which data crosses process boundaries (the public Config JSON representation), and which trusted code supplies validators (the shared declarations imported by the receiving process)? Which layer owns PostgreSQL DSN-versus-parts rejection? Weft keeps that existing policy unless the release demonstrably owns the identical rule.

## Invariants and Constraints

Keep project discovery and .weft paths, explicit context overrides, broker target identity, supervisor environment precedence, debug/logging semantics, and explicit override precedence. Ambient BROKER variables never affect Weft. Config crosses process boundaries through its supported JSON representation; restoration reads no environment and returns a read-only snapshot. Do not serialize validator callables or import executable names supplied by the payload. Keep the durable TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log spine, TID identity, forward-only states, reserved policy, spec/io immutability, and runtime-only queue exclusions. No storage migration or new dependency is introduced by this 8.1-to-8.2 update.

Invalid config remains fatal and CLI errors stay bounded without traceback. Accept upstream percentage units and database-name validation, document the change, and remove empty-name PostgreSQL fixtures. Keep real SQLite and PostgreSQL queues, project selection, watcher and spawned-process tests. Do not replace them with mocked broker behavior. Live peek remains a live observation; its new keyset pagination does not justify removing preflight-before-delete or newest-state selection policies.

Out of scope: whole-Weft config API redesign, lifecycle/persistence changes, CI changes, unrelated dirty work, and publishing/committing the combined checkout. Stop and revise if these become necessary.

## Proposed Spec Delta

Promotion strategy A: apply requirement text before implementation; update existing implementation notes with the code slice. No new spec files or machine classifications.

In [SB-0.1], require SimpleBroker 8.2.1 and simplebroker-pg 4.2.1. Retain the historical v7-to-v8 cold-cutover instructions. Describe Config snapshots and ID-cursor live peeks as provided by the coordinated release.

Replace the three Current Context API bullets requiring complete mirrored defaults, resolve_isolated_config, and ResolvedConfig with this exact text:

> - Weft extends SimpleBroker's `DEFAULT_CONFIG` with `ConfigField` entries for its added or changed settings, each carrying a default, description, and validator. `load_config()` calls the public `resolve_config()` with prefix `WEFT`; broker defaults and validators that Weft does not change belong to SimpleBroker. New upstream fields require no mirrored Weft inventory or schema guard. Ambient `BROKER_*` values never tune Weft, and resolution does not mutate the environment or upstream declarations.
> - In-process configuration is a read-only `Config` with uppercase unprefixed keys. Explicit overrides and environment inputs use `WEFT_*` names; the former `BROKER_*` override aliases and prefixed runtime keys are removed. Invalid earlier source values warn and may be replaced by valid later overrides; invalid final values raise `InvalidConfigError`. Weft retains its own cross-field project-path, external-log-mode, and PostgreSQL target-shape rules.
> - Contexts and SimpleBroker handles share the resolved Config snapshot. Mutable runtime policy copies retain the same unprefixed keys. Process transport uses SimpleBroker's public JSON serialization of Config. Receiving processes restore read-only values and namespace using locally imported field declarations, without rereading the environment. Validator callables and their import paths are not part of the JSON payload. The private manager serve-active flag is not loaded from the environment.

Add to [CLI-5] and the user-facing configuration notes:

> Broker settings follow SimpleBroker 8.2 value rules. `WEFT_VACUUM_THRESHOLD` is a percentage from 0 to 100 for both numeric and string inputs: the default is 10, and 0.1 means 0.1%. SQLite database-name components accept only ASCII letters, digits, dot, dash, and underscore; an explicitly empty database name is invalid even when selecting another backend. Omit an irrelevant SQLite name when configuring PostgreSQL.

## Released JSON Serialization Contract

Upstream 8.2.1/PG 4.2.1 is now available. The public API is `serialize_config(config) -> str` and `deserialize_config(payload, *, defaults=...) -> Config`. The envelope contains `prefix` and `values`; deserialization takes receiver-owned declarations, ignores extra envelope metadata, rejects non-JSON values, and reads neither environment nor TOML. The release also adds pickle support, which this migration does not use, and bounds path-validation error previews to 255 characters.

`weft/_constants.py:resolve_runtime_config` restores JSON with `deserialize_config(payload, defaults=WEFT_CONFIG_DEFAULTS)` and reapplies Weft's PostgreSQL target-versus-parts check. It preserves full resolved snapshots instead of deriving defaults again. `weft/core/launcher.py:launch_task_process` resolves even omitted config in the parent and passes `serialize_config(...)` to `_task_process_entry`; the entry restores it before constructing a task. `weft/core/manager_runtime.py:_build_manager_process_command` uses the same serializer. `weft/manager_process.py:main` validates the JSON snapshot and gives bounded config errors; `run_manager_process` adapts foreground Config input to the shared JSON task entry. No Config or validators enter multiprocessing arguments. Host worker runners do not receive broker config, so their task-spec transport does not change.

Acceptance: nondefault settings and defaults survive a fresh process whose ambient WEFT values differ; the restored Config remains read-only, retains namespace, and uses local validators for later overrides. Manager invalid config produces exit 2 without traceback. Existing real SQLite/PostgreSQL manager, consumer, and watcher tests remain required. Do not duplicate SimpleBroker's generic serialization matrix.

## Execution Slices and Verification

1. Review this plan and proposed delta independently, then promote the spec. Baseline tests establish current config/context behavior. Change unit tests first to demonstrate the new percentage contract and partial snapshot resolution; observe failure under 8.1.1. Dependency upgrade itself must expose removed-import failures before migration.
2. Update pyproject/lock together using `uv lock --upgrade-package simplebroker --upgrade-package simplebroker-pg`, then `uv sync --all-extras`. Replace removed APIs and duplicate loaders with the combined field declarations, migrate runtime keys, integrate the released JSON transport API, and update direct Config indexing and test typing. Delete obsolete schema-drift and exhaustive isolation tests; retain compact boundary tests. Review this slice before expanding.
3. Integrate Config serialization/restoration at manager and task process boundaries and update the dependency floor. Adapt real broker/test provisioning and release-sensitive error/validation expectations. Verify `tests/system/test_constants.py`, `tests/context/test_context.py`, helper/provisioning tests, CLI init/queue, watcher, queue-wait, and process transport coverage. Use `bin/pytest-pg` for live PostgreSQL. Stop if behavior beyond the named release delta changes.
4. Run the default full pytest suite, repo-wide mypy and ruff, affected formatting, plan/spec hygiene, and the canonical backstitch check (discover its command from project tooling). Reconcile traceability and perform independent completed-work review against the captured delta. Report remaining risks, tests retired, code removed, and broader opportunities.

All Python verification loads `. ./.envrc` and uses `./.venv/bin/` tools. Independent reads and static checks may run concurrently; dependency updates, edits, and tests that rely on those edits run sequentially. Keep checks on the published installed distribution, not a PYTHONPATH shadow of the sibling checkout.

## Rollout and Rollback

Install the coordinated package pair with the Weft changes. Restart Weft processes to use the new config representation; no live manager is changed by this work. The existing schema and queue formats stay unchanged. Rollback the scoped Weft changes and both dependency pins together, preserving all unrelated changes. Do not revert or delete user data. Operators using fractional numeric vacuum settings must convert their intended threshold to percent. Observe real enqueue/result, project target selection, and watcher wake/cleanup in tests before release.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Review and Evidence

Self-review: initial facade proposal was superseded by the user clarification. Use one combined ConfigField table and canonical unprefixed Config, keep conversion only at mutable runtime and transport boundaries, and retain Weft cross-field policy. Native independent review required; same-family review is the available integrated path.

Revision: user explicitly clarified extending DEFAULT_CONFIG with WEFT namespace and added/changed documented, validated fields. Config pickle and JSON probes fail directly; dict(Config) succeeds for release defaults. Baseline config/context suite passed 150 tests under 8.1.1.

Independent plan review: PASS (same-family native reviewer). F1 accepted: DIRECTORY_NAME derives DEFAULT_DB_NAME and PROJECT_CONFIG_PATH only when those settings are not explicitly supplied. F2 accepted: TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS remains None until runtime derives configured retention. Both existing behavioral tests are retained. SB owns combined PROJECT_CONFIG_PATH/PROJECT_CONFIG_NAME validation; do not duplicate it. The user subsequently required JSON-serializable Config in the target release; field-table/key changes remain valid, while the final transport slice awaits that upstream contract.

Spec-promotion baseline for the field-table/key/value-rule slice: diff from `34ca4bad82b56e917f20f69b0bbb66a755806128` in docs/specifications/04-SimpleBroker_Integration.md and docs/specifications/10-CLI_Interface.md, applied 2026-09-14. The JSON-specific transport proposal was subsequently promoted with the 8.2.1 slice below.

Implementation evidence so far: published 8.2.0 and PG 4.2.0 installed through uv; baseline new-unit assertion fails with KeyError for VACUUM_THRESHOLD and old fraction value 0.005. The combined declarations return Config(prefix=WEFT) with 64 unprefixed fields and default threshold 10. A real SQLite queue round trip and shared context/broker Config identity passed. Production consumer slice passed scoped Ruff, mypy, canonical-key scan, and manager JSON-value smoke. These are intermediate checks, not final integration evidence.

JSON prerequisite review: PASS by an independent native reviewer. The reviewer confirmed the target capability is distinct from published 8.2.0, validators are receiver-owned, and final transport/dependency/completion gates await the upstream API.

Intermediate verification rerun on 2026-09-14: config, config-property, context, and plan-metadata tests passed (144 tests); spec hygiene passed (2 tests); full configured mypy passed across 421 source files; repository Ruff passed. The consumer test slice ran seven focused modules, corrected three processor-error expectation failures, and reran those three successfully. Full runtime suite, live PostgreSQL, final JSON round-trip acceptance, traceability reconciliation, and final completed-work review remain required in slices 3–4.

Release delta (2026-09-14): user requested the published 8.2.1/4.2.1 pair. The formerly pending JSON prerequisite is satisfied by explicit public functions, not direct json.dumps(Config). Independent review of these concrete transport owners and restoration rules requested before implementing the slice.

Released JSON delta review: PASS by independent native reviewer before transport implementation. Full resolved snapshots are revalidated with receiver declarations. The suggested extra WEFT-prefix rejection is not adopted: the existing Config-input path preserves its namespace, and receiver declarations are selected by local code regardless of the transported prefix. Standard Weft loading still always selects WEFT. JSON transport text promoted into [SB-0.4]/Current Context API with 8.2.1/4.2.1 dependency floors. Manager command-envelope regression failed against provisional dict transport as expected.

Completed-work review finding F3 (accepted): `weft/helpers/__init__.py` loaded ambient configuration during import, before the task/manager JSON decoder ran. A fresh spawn with invalid child WEFT_CACHE_MB reproduced an InvalidConfigError traceback despite a valid parent snapshot. Fix the existing helper config cache to load lazily for standalone callers; allow `reload_config(config=...)` to bind a supplied runtime snapshot. `_task_process_entry` restores and binds the snapshot before loading the task class. This also makes logging/debug helpers respect parent policy. The fresh-spawn acceptance now varies invalid ambient fields and opposite debug/logging flags; normal helper reload and CLI validation tests must still pass.

F3 fix review: PASS before implementation. Related cleanup: remove cmd_init's unreachable empty-database-name guard, now owned by native Config validation. F4 (accepted): Weft seconds parsers accepted NaN/infinity, which Config JSON correctly rejects. Tighten the existing positive/nonnegative parsers to finite values and matching field descriptions; test environment and explicit numeric/string inputs at named fields. This moves the error to configuration resolution instead of later serialization.

## Simplification Audit

Removed duplicated SimpleBroker defaults, the WEFT-to-BROKER mapping and schema inventories, the freeze/isolated-resolution wrapper, the enum/rule override dispatcher, and the duplicate environment-loader/override tables. The combined declarations now automatically inherit upstream additions. Runtime consumers share Config, converting only at intentional mutable policy boundaries. Removed nine config tests tied to schema/parity/freeze or exhaustive ambient policing and one redundant provisioning-isolation test; kept focused namespace and real broker tests. Empty-name init tests now prove upstream rejection instead of the removed local guard. The revised bootstrap no longer depends on import-time helper config loading.

Keep Weft's directory-derived defaults, private serve flag, external-log-mode rule, retirement errors, and PostgreSQL DSN-versus-parts ambiguity check: these express Weft policy and are not identical to upstream behavior. Keep queue/watcher lifecycle, state selection, and cleanup safety tests. No new queue abstraction or codec is needed.

Separate follow-up: `integrations/weft_django/weft_django/conf.py:CORE_CONTEXT_OVERRIDE_ENV_KEYS` still lists BROKER_* variables, causing ambient broker settings to suppress Django BASE_DIR fallback even though core Weft ignores them. This pre-existing Django context issue is outside the migrated core boundary and was not changed. Removing that stale alias list with a Django context test is a concrete next simplification.

Traceability reconciliation: also synchronized the public WeftContext description in `14-Python_API_Surfaces.md` [PY-1] and runtime/context runbook with the canonical immutable Config contract. The constructor and exported-name inventories are unchanged.

Verification checkpoint: the eight-worker full suite ran 4,848 tests: 4,830 passed, 14 skipped, and four failed. One test still patched the removed context.load_config binding; updated its purity tripwire to context.resolve_runtime_config. Three unrelated process-startup/readiness deadlines expired under the parallel load. All four passed together serially in 2.61 seconds, with no production timeout or lifecycle changes. Rerun the same full selection with four workers to assess the final tree under lower contention; do not call the initial full run passing.

Four-worker full-suite result: **4,834 passed, 14 skipped**, 459.55 seconds. Live PG setup then exposed a remaining raw dictionary in `bin/pytest-pg:_initialize_broker_schema`: SimpleBroker rejects `Queue(config=dict(os.environ))`. Replace it with `resolve_config(env=os.environ)` at that broker-only test boundary. This is a test-launcher API compatibility fix, not a CI workflow change; the failed live setup is its red proof. Rerun the wrapper and its existing unit tests.

## Scoped Changed Files

The targeted commit contains only this migration, transposed onto the existing
HEAD where it overlaps unrelated work. Three adaptations to uncommitted tests
(`test_result.py`, `test_status.py`, `test_task_commands.py`) remain with their
owning uncommitted changes; these files need no migration change at HEAD.

```text
README.md
bin/pytest-pg
docs/agent-context/runbooks/runtime-and-context-patterns.md
docs/lessons.md
docs/plans/2026-09-14-simplebroker-8-2-configuration-plan.md
docs/plans/README.md
docs/specifications/04-SimpleBroker_Integration.md
docs/specifications/10-CLI_Interface.md
docs/specifications/14-Python_API_Surfaces.md
pyproject.toml
tests/cli/test_cli_init.py
tests/cli/test_cli_init_sqlite_only.py
tests/cli/test_cli_queue.py
tests/cli/test_env_file_bootstrap.py
tests/commands/test_dump_load.py
tests/commands/test_queue.py
tests/commands/test_run.py
tests/commands/test_serve.py
tests/context/test_context.py
tests/core/test_client.py
tests/core/test_manager.py
tests/core/test_monitor_store.py
tests/core/test_queue_wait.py
tests/core/test_task_monitoring.py
tests/helpers/test_backend.py
tests/helpers/weft_harness.py
tests/system/test_config_transport.py
tests/system/test_constants.py
tests/system/test_manager_process.py
tests/system/test_test_backend.py
tests/tasks/test_multiqueue_watcher.py
tests/tasks/test_task_execution.py
tests/tasks/test_task_monitor.py
uv.lock
weft/_constants.py
weft/bootstrap.py
weft/commands/init.py
weft/commands/interactive.py
weft/commands/run.py
weft/commands/serve.py
weft/commands/system.py
weft/context.py
weft/core/launcher.py
weft/core/manager.py
weft/core/manager_runtime.py
weft/core/monitor/runtime.py
weft/core/monitor/store.py
weft/core/monitor/task_monitor.py
weft/core/pipelines.py
weft/core/queue_wait.py
weft/core/serve_log.py
weft/core/spawn_requests.py
weft/core/tasks/base.py
weft/core/tasks/multiqueue_watcher.py
weft/helpers/__init__.py
weft/manager_process.py
```

## Final Verification and Handoff

The following broad checks ran on the combined working checkout before targeted
commit isolation. Exact candidate checks are recorded separately below.

- Published SimpleBroker 8.2.1 and simplebroker-pg 4.2.1 installed; pyproject floors and lockfile agree. The sibling checkout is not on PYTHONPATH.
- Full default test selection with four workers: **4,834 passed, 14 skipped**, 459.55 seconds (`/tmp/weft-sb821-full-four-workers.log`). The initial eight-worker result and its four failures are recorded above; the corrected test and three deadline failures passed together serially before the successful full rerun.
- Live PostgreSQL 18 through `bin/pytest-pg --fast`, four workers, covering constants, JSON transport, context, manager, monitor store/runtime, queue waits, run/serve/queue/dump-load, native observation connections, CLI init/queue, watchers, TaskMonitor, task execution, and observability: **1,406 passed, 12 skipped**, 496.02 seconds (`/tmp/weft-sb821-postgres-final.log`). Wrapper unit tests: **27 passed**. Temporary test containers are cleaned by the wrapper.
- Full configured mypy: **422 source files, no issues**. Repository Ruff and scoped formatting/whitespace checks passed. Plan metadata and spec hygiene: **6 passed**.
- Black-box CLI invalid config probes returned exit 1 without traceback; malformed/invalid manager Config envelopes returned exit 2 without traceback. Real spawn proved invalid child environment isolation, local unpicklable sender-validator exclusion, receiver-owned validation, read-only Config, and helper logging/debug snapshot custody.
- Independent completed-work review: **PASS** after resolving the import-time ambient-load blocker and finite-number validation gap.
- Backstitch command: `../backstitch/.venv/bin/backstitch check --repo-root /Users/van/Developer/weft --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --format json --output /tmp/weft-sb821-trace-final.json`. The starting snapshot has 28 errors, 1,120 warnings, 601 informational findings; the final tree has the same 28 errors, 1,119 warnings, 600 informational findings. Comparing severity/code/path/message/symbol yields **no new diagnostics**. This is baseline parity, not a clean absolute traceability gate.

## Targeted Commit Verification

The owner requested a targeted commit on 2026-09-14. A separate candidate tree
contains the migration applied to HEAD `34ca4bad`, with its own virtualenv
installed from the candidate lockfile (`uv sync --all-extras --locked`).
Unrelated public API, typing-audit, dependency, and tooling edits are excluded.

- Exact candidate, 23 configuration/context/process/runtime/CLI/spec test modules,
  four workers: **1,469 passed, 5 skipped, 1 failed**, 161.70 seconds. The sole
  failure is `test_every_plan_has_normalized_metadata`: the existing
  `2026-09-08-persistent-result-output-ids-plan.md` links to the deleted
  `2026-09-10-weft-result-outbox-contract-plan.md`. Running that test against an
  export of unchanged HEAD reproduces the same failure. It is outside this
  commit; no migration test failed. Evidence: `candidate-pytest.log` and
  `head-metadata-pytest.log` in the temporary directory recorded by
  `/tmp/weft-sb821-commit-path`.
- Exact candidate production, bin, and first-party integration/extension mypy:
  **189 source files, no issues**. Tests retain HEAD's existing typing surface;
  the separate typing audit is not included.
- Exact candidate repository Ruff passed; all 46 changed Python/script files
  passed formatting. Staged whitespace validation passed.
- Independent production/dependency/docs review and resolved-test review:
  **PASS**, with no dependency on omitted worktree APIs. Corrected the CLI
  future-skew documentation to name the canonical unprefixed key.

The repository owner requested a targeted commit after implementation and verification. The migration is isolated from the pre-existing dirty worktree using its captured starting bytes. Overlapping test typing and public API changes remain outside this commit. No push is requested. Restart existing Weft processes when applying the coordinated code/dependency update.
