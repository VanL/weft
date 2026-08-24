# SimpleBroker 7.4.1 Compatibility Upgrade Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.3], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-6]
Superseded by: none

Class: 5 — spec-changing and risky. The dependency and coordinated backend
floors are normative, while configuration snapshot and watcher lifecycle
changes cross context, task, process, and persistence boundaries. Hardening is
mandatory.

## Goal

Upgrade Weft to the published SimpleBroker 7.4.1 and `simplebroker-pg` 3.9.1
pair, preserve Weft's isolated embedding and durable queue semantics, and make
only compatibility changes proven necessary by the released contracts and the
Weft verification suite.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.3],
  [SB-0.4] owns Weft's dependency floor, queue delegation, context isolation,
  and watcher integration.
- `docs/specifications/10-CLI_Interface.md` [CLI-6] owns dump/load behavior;
  its existing contract must remain unchanged.
- `../simplebroker/CHANGELOG.md` 7.4.0 and 7.4.1 enumerate configuration
  snapshot timing, queue typing, watcher failure/lifecycle, retry, CLI, and
  coordinated extension changes.
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-1] through
  [SB-API-6] define the public root/ext surfaces, `ResolvedConfig`, queue result
  shapes, and watcher lifecycle.
- `../simplebroker/docs/specs/15-persistence-io.md` [SB-IO-1] through [SB-IO-4]
  remains the dump/load contract baseline.
- `docs/plans/2026-08-13-simplebroker-7-3-dump-watermark-plan.md` is completed
  historical context for the existing isolated-config and watermark work; it
  is not a new behavior source.

## Context and Key Files

Files to modify if the released compatibility probes require them:

- `pyproject.toml`, `uv.lock`: coordinated core and PostgreSQL floors.
- `weft/_constants.py`, `weft/context.py`, `weft/commands/init.py`: complete
  strict Weft mapping and every config-consuming lower-layer handoff.
- `weft/commands/queue.py`, `weft/core/tasks/consumer.py`,
  `weft/core/tasks/multiqueue_watcher.py`: queue result typing and watcher
  lifecycle compatibility without changing runtime record shapes.
- `weft/core/tasks/base.py`, `weft/core/tasks/pipeline.py`,
  `weft/core/manager.py`, `weft/core/monitor/task_monitor.py`: move every
  Weft-policy read off the inherited BaseWatcher config slot and onto the
  separate plain `_weft_config`.
- `tests/system/test_constants.py`, `tests/context/test_context.py`: canonical
  key parity, ambient isolation, and snapshot timing.
- `tests/system/test_optional_extras.py`, `tests/cli/test_cli_init.py`: exact
  dependency floors and init-command ambient isolation.
- `tests/commands/`, `tests/tasks/test_multiqueue_watcher.py`: real queue,
  dump/load, and watcher failure/lifecycle proofs.
- `docs/specifications/04-SimpleBroker_Integration.md`, `README.md`,
  `CHANGELOG.md`, this plan, and `docs/plans/README.md`: normative floor and
  release traceability.

Read first:

- `weft/_constants.py` (`_resolve_weft_broker_config`,
  `freeze_broker_config`): Weft deliberately supplies exactly all 32 canonical
  broker keys and rejects schema drift.
- `weft/core/tasks/multiqueue_watcher.py`: Weft owns membership and priority;
  SimpleBroker owns dispatch, retry, run ownership, and cleanup.
- `weft/core/tasks/base.py` and its descendants: Weft runtime policy must use a
  distinct plain `_weft_config`; inherited `_config` belongs to BaseWatcher and
  remains its immutable `ResolvedConfig` snapshot.
- `weft/commands/dump.py` and `weft/commands/load.py`: public upstream
  delegation, runtime-state filtering, and rollback diagnostics.
- `../simplebroker/simplebroker/sbqueue.py`, `watcher.py`, and `_constants.py`
  at tag `v7.4.1`; use public behavior only, never import these modules.

Comprehension checks:

1. Why must a plain picklable Weft config cross spawn while `ResolvedConfig`
   is recreated only at the SimpleBroker ownership seam?
2. Which watcher concerns remain Weft-owned after upstream makes error-handler
   exceptions terminal and serializes startup/stop cleanup ownership?
3. Which queue typing changes remove casts without changing runtime values?

## Invariants and Constraints

- Preserve the existing durable spine, queue names, TID identity, forward-only
  task states, reservation policy, and TaskSpec immutability.
- Runtime-only `weft.state.*` queues remain excluded from dumps and skipped on
  load; dump v1 watermark semantics do not change.
- Ambient `BROKER_*` values, valid or invalid, do not affect Weft. Weft keeps
  its complete 32-key mapping strict and does not opt into opaque unknown keys.
- Long-lived process payloads remain picklable plain mappings in
  `_weft_config`. Inherited watcher `_config` remains the lower layer's owned
  `ResolvedConfig`; other config-consuming lower-layer handoffs receive the
  same nominal form.
- Queue read/peek/move runtime shapes remain the existing strings, timestamp
  tuples, iterators, and moved-message dictionaries selected by literal flags.
  Use upstream overloads; do not add Weft wrappers or runtime conversions.
- SimpleBroker owns watcher run/stop cleanup, dispatch failure propagation,
  retry, and context startup. Weft owns multi-queue topology and must not clone
  the upstream lifecycle state machine.
- Core 7.4.1 and `simplebroker-pg` 3.9.1 roll out and roll back together. The
  backend API remains v7, but package floors are coordinated.
- Use only public package-root, `simplebroker.ext`, and
  `simplebroker.commands` surfaces. Stop and re-plan if compatibility appears
  to require private imports, a second queue path, or a persisted-format change.
- Preserve unrelated worktree changes and resolve overlapping edits by
  integrating, never replacing, their intent.
- Real SQLite queues/watchers prove behavior. Mocks are limited to injected
  terminal callback or cleanup failures that cannot be triggered deterministically.

## Spec Baseline

- `f5e90e41` — `docs/specifications/04-SimpleBroker_Integration.md` and
  `docs/specifications/10-CLI_Interface.md` at plan authoring time.
- Upstream release baseline: `../simplebroker` tag `v7.4.1` at `36bc6d4d`;
  published versions verified from PyPI JSON on 2026-08-24 as SimpleBroker
  7.4.1 and `simplebroker-pg` 3.9.1.
- Plan type: implementation with spec revision.
- Promotion strategy: B — atomically update the dependency/snapshot wording
  with the implementation and tests because no interim mixed contract is useful.

## Proposed Spec Delta

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1]

Replace the dependency-floor paragraph with:

> Weft requires SimpleBroker 7.4.1 or newer. Installations using the optional
> PostgreSQL backend require `simplebroker-pg` 3.9.1 or newer. These coordinated
> floors provide backend API v7, bounded dump watermarks, immutable
> invocation/handle configuration snapshots, typed queue result overloads, and
> the synchronized watcher lifecycle contract used by Weft.

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Replace the version-specific isolated-resolution bullet with:

> Weft resolves its exact complete mapping with SimpleBroker 7.4.1's public
> `resolve_isolated_config()` and preserves or recreates the immutable
> `ResolvedConfig` marker at every config-consuming lower-layer handoff. Weft
> does not opt into opaque extra keys. A SimpleBroker handle or invocation that
> accepts config retains that snapshot for its lower-layer lifetime and does
> not reread ambient `BROKER_*`. Weft watcher subclasses keep runtime policy in
> a distinct complete picklable ordinary mapping while leaving BaseWatcher's
> inherited config slot as its owned `ResolvedConfig`; process transport uses
> only the ordinary mapping and recreates the marker at the child handoff.

Add to the watcher consequences:

> SimpleBroker owns serialized watcher startup/stop cleanup and treats an
> ordinary exception raised by an error handler as terminal after cleanup.
> Weft does not swallow or replace that terminal callback failure and does not
> duplicate the upstream watcher lifecycle.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [SB-0.4] | Capture red evidence before compatibility edits | Three pre-existing worktree edits remove queue-result casts enabled by 7.4.1 overloads | The edits belong to this compatibility task but predate this plan and review. The planned clean-`HEAD`/7.4.1 probe unexpectedly passed. The firing Rule 5 substitute is the already captured cast-free source against 7.3.2 (seven mypy errors), followed by the same source passing against 7.4.1. | No behavioral change. |

## Tasks

1. Establish released-version and red compatibility evidence.
   - Record PyPI and tag versions and compare 7.3.2 to 7.4.1 public contracts.
   - Inventory the three existing dirty compatibility paths as task-owned and
     preserve them. Record that clean `HEAD` unexpectedly passes against 7.4.1;
     use the captured seven cast-free/7.3.2 mypy errors as the firing Rule 5
     substitute, then prove those same paths pass against 7.4.1.
   - Add or tighten firing tests for canonical-key parity; ambient isolation at
     discovery, broker/Queue, init, watcher, load, and dump broker-open seams;
     retained watcher snapshots; and manager/task spawn picklability.

2. Promote the spec delta and upgrade the coordinated packages.
   - Update [SB-0.1]/[SB-0.4], backlinks, README, changelog, dependency floors,
     and lockfile together.
   - Set every SimpleBroker floor to 7.4.1 and every PG floor to 3.9.1.
   - Verify public backend API v7 and exact plugin declaration without a live
     service. Record a promotion baseline against `f5e90e41` plus the spec diff.

3. Apply the smallest compatibility fixes.
   - Prefer new public overload narrowing over casts; do not alter runtime data.
   - Preserve the strict 32-key isolated map. Move Weft/task policy reads to a
     distinct `_weft_config`, leave BaseWatcher `_config` as `ResolvedConfig`,
     and transport only the plain Weft mapping across process spawn.
   - Audit every `self._config` use in MultiQueueWatcher descendants. After the
     split, descendant Weft policy must use `_weft_config`; inherited
     SimpleBroker lifecycle code alone owns `_config`.
   - Pass the frozen Weft marker to `simplebroker.commands.cmd_init()`; valid or
     malformed ambient `BROKER_*` must not affect `weft init`.
   - Let upstream watcher lifecycle/error rules propagate through the existing
     MultiQueueWatcher seam. Prove sync re-raise with the original handler as
     cause and no later dispatch, plus background `threading.excepthook`
     visibility and no later dispatch.
   - Stop and re-plan on private API need, persistence changes, or a second
     watcher/queue implementation.

4. Verify, review, and reconcile traceability.
   - Run focused config/context/queue/watcher/dump/load tests, full default
     tests, ruff, mypy, lock/diff/spec/architecture gates, and version handshake.
   - Run `tests/system/test_optional_extras.py` explicitly and update its exact
     7.4.1/3.9.1 floor assertions.
   - Run PG gates when `WEFT_PG_TEST_DSN` is configured; otherwise report the
     live PG runtime gap explicitly.
   - Complete independent work review, disposition findings, and update the
     review/deviation records before completion.
   - Before any commit, inspect and stage an explicit task-owned file list. Do
     not use broad staging, because the task began with overlapping dirty files.

## Testing Plan

- Real SQLite: context Queue/broker, queue read/peek/move shapes, watcher
  dispatch and terminal error propagation in sync and background modes,
  `cmd_init` under valid and malformed ambient broker values, and dump/load
  round trip and watermark.
- Ownership: assert the inherited watcher `_config` is a `ResolvedConfig`, the
  separate `_weft_config` remains plain and complete, and ambient mutation after
  construction cannot change the retained lower-layer snapshot.
- Process: one manager child spawn with malformed ambient `BROKER_*` after
  config capture, proving the transported mapping remains picklable and the
  child reconstructs the marker.
- Structural: public canonical key set equals Weft's exact map; installed core
  and PG versions pair at backend API v7.
- Type: full project mypy uses the 7.4.1 overloads and has no obsolete casts or
  new ignores.
- Do not mock successful queue, watcher, context, or dump/load paths.

## Verification and Gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/system/test_optional_extras.py -q
./.venv/bin/python -m pytest tests/system/test_constants.py tests/context/test_context.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_init.py -q
./.venv/bin/python -m pytest tests/commands/test_queue.py tests/tasks/test_multiqueue_watcher.py -q
./.venv/bin/python -m pytest tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/architecture/test_import_boundaries.py -q
uv lock --check
git diff --check
./.venv/bin/python -c "from importlib.metadata import version; from simplebroker.ext import BACKEND_API_VERSION; from simplebroker_pg import get_backend_plugin; assert version('simplebroker') == '7.4.1'; assert version('simplebroker-pg') == '3.9.1'; assert BACKEND_API_VERSION == 7; assert get_backend_plugin().backend_api_version == 7"
```

## Rollout and Rollback

- Roll out Weft, SimpleBroker 7.4.1, and `simplebroker-pg` 3.9.1 as one package
  set. Observe context creation, manager child startup, watcher terminal errors,
  and dump/load round trips.
- Roll back Weft code, core, and PG plugin together. Do not leave PG 3.9.1 with
  a core below 7.4.1 or new Weft snapshot assumptions with the 7.3.2 stack.
- No new persistent format is intended. If verification finds one, stop and
  reclassify the rollout before landing.

## Independent Review Loop

An independent reviewer must inspect this plan against the upstream 7.4.1
changelog/specs and the Weft integration spec before implementation. A second
review inspects the completed diff for public-surface drift, swallowed watcher
failures, configuration isolation loss, package skew, and unnecessary changes.
Every finding is recorded and dispositioned below.

## Review Log

| Stage | Finding | Disposition |
|-------|---------|-------------|
| Author fresh-eyes | The first classification undercounted the normative dependency-floor edit as Class 4. | Accepted. Escalated to Class 5, added the exact spec delta and atomic promotion strategy, and retained mandatory hardening. |
| Independent plan review | Weft overwrites BaseWatcher's retained snapshot; `cmd_init` omits isolated config; invocation wording and tests were too broad; floor tests and dirty-tree sequencing were omitted. | Accepted. Required `_weft_config` separation, an isolated init handoff, seam-specific firing tests, explicit optional-extra gates, a Rule 5 baseline record, and explicit file-list staging. |
| Rule 5 execution | The reviewer expected clean `HEAD` to report redundant casts against 7.4.1, but focused mypy passed. | Recorded the non-firing probe. The discriminating evidence is cast-free source failing with seven result-shape errors under 7.3.2 and passing unchanged under 7.4.1. |
| Independent work review | PASS. No blocking findings in config ownership, init isolation, watcher terminal propagation, queue typing, TaskMonitor snapshot policy, dependency pairing, docs, or scope. | Accepted. Primary verification completed the full suite and all static gates after the review snapshot. |

## Verification Record

- Installed handshake: SimpleBroker 7.4.1, `simplebroker-pg` 3.9.1, backend API v7.
- Full default suite: 4,215 passed, 2 PostgreSQL-gated skips.
- Full mypy: 187 source files passed.
- Full ruff, plan/spec/import-boundary tests, lock check, and diff check passed.
- Live PostgreSQL verification was not run because `WEFT_PG_TEST_DSN` is unset;
  exact package/plugin API checks and the default SQLite suite passed.

## Out of Scope

- Adopting Redis support.
- Exposing new SimpleBroker CLI flags or changing Weft CLI output.
- Opting into opaque SimpleBroker config extension keys.
- Reworking dump v1, queue naming, task lifecycle, or watcher topology policy.
- Refactoring unrelated queue/task code beyond compatibility evidence.

## Fresh-Eyes Review

The plan initially risked treating 7.4.1 as a version-only bump. The upstream
contract changes make snapshot timing, process picklability, queue overloads,
and terminal watcher failures the real compatibility surfaces. The revised
tasks attach a firing proof to each, keep persistence unchanged, and provide a
clear stop gate if private APIs or a second lifecycle path appear.
