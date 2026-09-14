# Django Context Resolution Owned by Weft

Status: draft
Source specs: docs/specifications/13C-Using_Weft_With_Django.md [DJ-2], [DJ-3], [DJ-8.1], [DJ-8.4], [DJ-9.1], [DJ-13.2]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]
Superseded by: none

Class: 5. This changes the public context-resolution contract and the Django/core ownership boundary. Hardening applies because resolution controls submission destinations and crosses the deferred-submission boundary.

Plan type: implementation with spec revision. This document is the planning deliverable; it does not authorize or record completed runtime implementation.

## Goal

Have Django request its resolved context through Weft's public client. Django supplies its explicit `CONTEXT` setting and a `BASE_DIR` discovery fallback; Weft loads configuration, discovers the project, chooses the broker, and constructs the `WeftContext`. Remove the bridge's environment-key catalog and preserve pure TaskSpec export and prepared transaction submissions.

The important distinction is between a **project root** and a **broker target**. A PostgreSQL setting chooses a broker, not a new directory for Weft artifacts. A fallback directory is a discovery anchor, not an explicit override of a TaskSpec's declared context.

## Spec Baseline

- `e35b0a274766867f23739e5f3e69593477d30104`: baseline for [Django integration](../specifications/13C-Using_Weft_With_Django.md), [SimpleBroker integration](../specifications/04-SimpleBroker_Integration.md), and [Python API surfaces](../specifications/14-Python_API_Surfaces.md).
- The baseline requires SimpleBroker 8.2.2 and simplebroker-pg 4.2.1. The completed [8.2 configuration migration](./2026-09-14-simplebroker-8-2-configuration-plan.md) supplies immutable, unprefixed Config values and JSON transport. Its separate Django follow-up is the starting problem here; the migration itself is not reopened.
- Promotion baseline: to be recorded by the implementer after the spec-promotion slice, using a commit SHA or a saved diff against the baseline above. Until promotion, the existing specs remain normative.

Read the [architecture](../specifications/00-Overview_and_Architecture.md), [system invariants](../specifications/07-System_Invariants.md), and [testing strategy](../specifications/08-Testing_Strategy.md) [TS-0], [TS-3] before implementation. Follow [AGENTS.md](../../AGENTS.md), the [decision hierarchy](../agent-context/decision-hierarchy.md) [DOM-15], [engineering principles](../agent-context/engineering-principles.md), and these runbooks: [runtime/context patterns](../agent-context/runbooks/runtime-and-context-patterns.md), [testing patterns](../agent-context/runbooks/testing-patterns.md), [writing plans](../agent-context/runbooks/writing-plans.md), [hardening](../agent-context/runbooks/hardening-plans.md), [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md), and [acceptance probes](../agent-context/runbooks/adversarial-acceptance-probes.md). Consult [lessons](../lessons.md), especially configuration restoration and context custody.

The public-client contract expansion also touches `weft/context.py`, `weft/client/_client.py`, and [PY-1]. Reconcile overlapping edits when implementing; this plan requires only the already-public `WeftClient`. It does not depend on adding `build_context` or `WeftContext` to `weft.client.__all__` and does not absorb unrelated exports.

## Current Structure and Evidence

| Owner / file | Current responsibility and coupling |
|---|---|
| `integrations/weft_django/weft_django/conf.py` | `resolve_context_override()` returns an explicit setting, `None` if any cataloged environment key is nonempty, or `BASE_DIR`. The catalog repeats 11 `WEFT_*` and 11 `BROKER_*` names. |
| `integrations/weft_django/weft_django/client.py` | `get_core_client()` calls `WeftClient.from_context(resolve_context_override())`. `build_registered_task_taskspec()` also calls that helper to embed a context path, including during pure export. All native/decorated submissions and observations share the core-client acquisition path. |
| `weft/_constants.py` | `WEFT_CONFIG_FIELDS`, `load_config()`, and `resolve_runtime_config()` own declarations, validation, and snapshots. `WEFT_CONTEXT_ENV` exists, but `CONTEXT` is not yet a loadable Config field. |
| `weft/context.py` | `build_context()` loads once, resolves a root/target, and materializes metadata and the broker. `_resolve_root_and_target()` uses an explicit path or discovery from CWD. `resolve_context_broker_target()` delegates to SimpleBroker `target_for_directory()`. |
| `weft/client/_client.py` | `from_context()` owns construction and `_context_explicit`; `__init__()` also constructs when no context is supplied. `prepare_spec()` passes that policy into parameterized reference materialization. A supplied context is retained, not rebuilt. |
| `weft/commands/submission.py` and `weft/client/_prepared.py` | Preparation snapshots work. Submission reuses the captured context when the runtime root matches; an explicitly different TaskSpec root is resolved through core with the captured Config. Returned task handles retain the actual runtime context. |
| Django `apps.py`, `registry.py`, `decorators.py` | App readiness discovers declarations. Export normalizes a template without loading Weft configuration or initializing a broker. Neither path may acquire a runtime context. |

Verified reproduction: with `CONTEXT=None`, `BASE_DIR=/tmp/django-project`, and CWD elsewhere, the old helper selects `BASE_DIR`. Setting `BROKER_BACKEND=postgres` changes its return to `None`, while core `load_config()["BACKEND"]` remains `sqlite`. The bridge consequently changes the project directory because of an input core ignores.

The catalog also treats `WEFT_BACKEND=sqlite` and `WEFT_PROJECT_SCOPE=1` as reasons to abandon `BASE_DIR`. SimpleBroker's current explicit-directory API does not use `DEFAULT_DB_LOCATION` or `PROJECT_SCOPE` to select a different Weft root. Config exposes values and namespace, not environment-source provenance. Comparing values with defaults cannot reliably identify whether a setting was explicitly supplied, and no provenance mechanism is needed for the design below.

Comprehension gates before editing:

1. Explain why a PostgreSQL DSN and the Weft metadata root are independent, and why the bridge cannot identify core overrides by maintaining environment names.
2. Trace `as_taskspec_for_call()` and `enqueue_on_commit()` separately. Identify which may resolve a context, when resolution occurs, and why passing an already-built context blindly into `WeftClient()` can change parameterized-reference precedence.

## Intended Design

### Core entry points and resolution

Extend the existing constructors, not the number of constructors:

```python
build_context(spec_context=None, *, fallback_root=None, config=None,
              create_dirs=True, create_database=True, autostart=None)
WeftClient.from_context(spec_context=None, *, fallback_root=None, autostart=None)
```

`fallback_root` uses the existing path types for each function. It is a discovery start directory, used only when no explicit root is supplied. With no fallback, the discovery anchor remains CWD. Do not add a Django-aware resolver, a context registry, a global cache, or a second root-discovery implementation. `connect()` can keep its existing signature; it already delegates to `from_context()`.

Add `CONTEXT` to `WEFT_CONFIG_FIELDS`, default `None`, external input `WEFT_CONTEXT`, with a description and local validator. Accept a string or `None`; an empty string means absent. Preserve nonempty path text, including spaces. Expand `~` and resolve relative paths only during context resolution, using the process CWD at that time. Config normalization must do no filesystem work. Mapping inputs reject non-string, non-None values. Reuse the existing optional-string normalization machinery and the SimpleBroker Config JSON path. Do not serialize validators or invent source-provenance metadata.

Root precedence is:

1. A non-None explicit `spec_context` argument.
2. Nonempty `CONTEXT` from the resolved Config.
3. Search upward from `fallback_root` when supplied, otherwise CWD, using the configured Weft broker-config path and name. A discovered project's root wins over the discovery anchor.
4. If discovery finds nothing, the anchor itself is the root.

Resolve the broker from that root with the existing SimpleBroker project API. Keep its existing precedence: project broker config, then applicable configured backend selection, then directory-local SQLite fallback. An absolute `PROJECT_CONFIG_PATH` selects a broker config without making its parent the artifact root; retain the chosen anchor as the root in that case. Broker filenames remain non-markers for discovery.

`build_context(config=...)` uses that supplied snapshot, including its `CONTEXT` value, without reading ambient environment. A snapshot lacking `CONTEXT` has no context override; this also handles older JSON snapshots. Creation flags and error behavior retain their current meanings. Even disabling both creation flags does not make context resolution pure.

`WeftClient.__init__()` and `from_context()` must agree about explicitness: a caller-supplied context/path or a nonempty resolved Config `CONTEXT` is explicit; a fallback/discovered root is not. Derive this from the arguments and the already-built context's Config, never a second config load or a Django mutation of `_context_explicit`. Preserve `from_weft_context()` as an explicitly supplied-context path. Test the effect on parameterized TaskSpec materialization, not just the private boolean.

### Django adapter

Replace environment-based selection in `conf.py` with settings-only accessors for explicit `CONTEXT` and optional `BASE_DIR`. Preserve the existing setting's `Path`/string conversion and absent-value behavior; do not add support for a `WeftContext` object in Django settings. No accessor reads environment, discovers paths, or initializes Weft. Remove `CORE_CONTEXT_OVERRIDE_ENV_KEYS`, `_has_core_context_override()`, `resolve_context_override()`, and the unused `os` import once all callers/tests move.

`get_core_client()` requests the context through:

```python
return WeftClient.from_context(
    get_explicit_context(),
    fallback_root=get_context_fallback_root(),
)
```

Weft owns the `build_context()` call and returns a client carrying its resolved context. Django neither calls `WeftContext(...)` nor imports private core resolution helpers. Submission, observations, HTTP/streaming adapters, and management commands keep using this one acquisition path. A retained client keeps its snapshot; a later acquisition can see later settings. Add no cache or invalidation API.

The pure registered-task builder copies only an explicit Django `CONTEXT` declaration into `spec.weft_context`. With no explicit declaration it leaves the field unset (normalization may emit `None`); it does not embed `BASE_DIR`, environment, or a discovered path. Actual submission resolves the runtime context through the client. Exports composed elsewhere inherit their destination from the receiving Weft context unless they contain an explicit declaration. This is an intentional compatibility clarification and must be documented.

All four deferred helper families continue acquiring their core client and preparing work before registering `transaction.on_commit()`. The callback submits the existing `PreparedSubmission`; it must not call settings/configuration resolution again. Changing settings, environment, or CWD between prepare and commit must not redirect work that uses the captured runtime root. Resolve relative and home-relative explicit TaskSpec context declarations once during core runtime preparation, as described below. Keep the existing core behavior for selecting the broker at an explicitly different runtime root; do not introduce a second context snapshot/transport mechanism.

### Bind declared paths during core preparation

Review reproduced a hidden coupling: a relative Django `CONTEXT` is copied verbatim into the generated TaskSpec, so the current submit path can resolve it against a different CWD after commit. Fix that in `weft/commands/submission.py`, where prepared work is owned, not in Django.

Use one small shared operation to copy/revalidate a nonempty `spec.weft_context` as `str(Path(value).expanduser().resolve())` at preparation time. Leave an absent declaration unset. Preserve input immutability, template/resolved mode, TID, `io`, bundle provenance, and all prepared-request flags. Reuse the existing snapshot/TaskSpec validation helpers; do not mutate frozen fields or introduce a new prepared-request field/type. The three context-aware owners invoke it as follows:

- `prepare()`: after `prepare_definition()` creates the pure definition snapshot, replace only its TaskSpec with the bound copy and retain the rest of the request.
- `prepare_spec()`: after parameterization and submit overrides, before a run-input adapter receives the runtime root and before the final preparation snapshot. The adapter and stored TaskSpec must use the same absolute root.
- `prepare_pipeline()`: after compilation and overrides, before the final preparation snapshot. The compiler already supplies an absolute top-level context; preserve it. Nested stage declarations retain their existing lifecycle.

Do not put this operation in `prepare_definition()`, `prepare_taskspec()`, or `_snapshot_taskspec()`, because pure normalization/export shares those paths. Do not open or resolve an alternate broker during this path-copying step. At submit time the existing shared owner uses the captured absolute declaration, builds an alternate runtime context with captured Config only when needed, and preserves the actual context on the returned task handle. This binds path interpretation, not project-file contents.

### Resolution matrix

Use distinct absolute directories for explicit root E, environment root W, Django fallback D, and unrelated CWD C. Assert both the artifact root and actual broker destination.

| Inputs | Expected root and broker selection |
|---|---|
| Django `CONTEXT=E`, `WEFT_CONTEXT=W`, fallback D | E; broker resolution at E still uses valid configured backend policy. Explicit root does not bypass config validation. |
| No Django context, `WEFT_CONTEXT=W` | W; direct root selection, no upward discovery. |
| No root override, no project config above D | D, regardless of C; default SQLite under D. |
| Project broker config above D, a different project above C | Nearest project above D; never C's project. |
| No `BASE_DIR` and no root override | Existing CWD discovery and fallback. |
| Any legacy `BROKER_*` catalog entry, or an unknown `BROKER_*` name | No change to D's root, Config, or broker target. |
| `WEFT_DEBUG`, `WEFT_BACKEND=sqlite`, explicitly default-valued broker settings | No root change; applicable Config values still load. |
| `WEFT_DEFAULT_DB_LOCATION`, either `WEFT_PROJECT_SCOPE` boolean value | No new root-selection behavior. Retain their current behavior in SimpleBroker's explicit-directory API, including that they do not relocate this context. |
| `WEFT_DEFAULT_DB_NAME=alternate.db` | D remains root; directory-local SQLite uses the configured filename when no broker config overrides it. |
| PostgreSQL target or split connection settings, no project broker config | D remains root; configured PostgreSQL broker selected by SimpleBroker. |
| Broker config at selected root plus conflicting env backend selection | Existing project broker-config precedence retained; no bridge override logic. |
| Absolute broker-config path outside D | D remains artifact root; external file supplies broker target. |
| Supplied Config with `CONTEXT=W`, ambient `WEFT_CONTEXT=E` or malformed ambient config | W and supplied values; no ambient reload. |
| Supplied Config without `CONTEXT`, fallback D, ambient `WEFT_CONTEXT=E` | D/discovery from D; ambient E ignored. |

## Invariants, Failure Boundaries, and Scope

- Keep `spec.weft_context` handling in the shared TaskSpec/submission owner, including explicit native TaskSpec roots. No Django-only submission or broker path. TIDs, forward-only states, reservations, immutable resolved `spec`/`io`, queue names, and runtime-only dump exclusions do not change.
- Preserve Config custody and JSON process transport. Queues and broker connections are recreated in spawned processes. Do not pass a live broker or a new pickled validator between processes.
- Preserve import/app-ready/export purity. Do not substitute `build_context(create_dirs=False, create_database=False)` on those paths. Settings-only path copying is permitted; project resolution is not.
- Resolution errors propagate through the existing core/public error paths. Invalid config or inaccessible project metadata must not fall back silently to another broker. Context construction can create metadata before a later failure; this plan promises no transactional filesystem rollback. No spawn request or commit callback should be registered when preparation fails.
- Preserve the existing broker-selection boundary for explicit alternate TaskSpec roots: core binds the declared path during preparation, then may build that runtime context with captured Config at submission. This plan does not freeze project files or change that lifetime. The pre-commit snapshot guarantee must not be overstated as a snapshot of all project files.
- Do not fix unrelated CLI context inconsistencies, alter SimpleBroker's target precedence, make `DEFAULT_DB_LOCATION` or `PROJECT_SCOPE` newly operative, or add arbitrary backend config validation. Existing CLI readers of `WEFT_CONTEXT` remain unless a focused regression requires a coordinated change.
- No new dependency, CLI flag, Django setting, schema migration, runtime table, or process boundary. Dependency minimum changes required to publish the new core keyword are release compatibility work, not a new dependency.

## Proposed Spec Delta

Promotion strategy: **A, in-file text before new implementation claims**, for all three files below. Apply the exact text and plan backlinks in the first implementation slice, before code changes. Retain existing mappings for existing behavior; add new function/section mappings together with their reciprocal code references in the owning implementation slice. No file reclassification is required.

### `13C-Using_Weft_With_Django.md` [DJ-13.2]: replace subsection body

> Django supplies an optional explicit `WEFT_DJANGO["CONTEXT"]` and an optional `settings.BASE_DIR` fallback to the public `WeftClient.from_context(..., fallback_root=...)` entry point. Weft owns configuration loading, validation, project discovery, broker selection, and `WeftContext` construction. The bridge must not maintain a list of core environment keys, inspect those variables to choose a root, or construct `WeftContext` itself.
>
> Root selection is explicit Django context, then resolved `WEFT_CONTEXT`, then Weft project discovery starting at `BASE_DIR`, then `BASE_DIR` itself when discovery finds nothing. Without `BASE_DIR`, discovery starts at the process CWD. Explicit roots are used directly rather than as discovery starts. Broker settings select the broker using core/SimpleBroker precedence; they do not suppress `BASE_DIR` or turn CWD into the artifact root. `BROKER_*` variables do not affect Weft configuration or context selection.
>
> A fallback or discovered root is not an explicit context override for parameterized spec-reference preparation. Explicit Django context and resolved `WEFT_CONTEXT` are explicit. This distinction belongs to core, not the bridge.
>
> Context resolution occurs when a core client is acquired for a runtime operation. Importing settings, discovering task declarations in `AppConfig.ready()`, and exporting a TaskSpec must not resolve a context. A retained client uses its resolved context and Config snapshot; acquiring another client can observe changed settings. The integration does not cache contexts globally.

### `13C-Using_Weft_With_Django.md` [DJ-8.1]: append to export rules

> Pure decorated-task export copies an explicitly configured Django `CONTEXT` path into `spec.weft_context` without resolving it. Otherwise the template leaves `spec.weft_context` unset; it does not capture `BASE_DIR`, environment configuration, or project discovery. Such a template inherits its runtime destination when submitted through Weft. The same declaration rule applies to the generated template used by `enqueue()`.

### `13C-Using_Weft_With_Django.md` [DJ-8.4]: append to snapshot rule

> Every deferred helper acquires its core client and prepares its work before registering the commit callback. The callback submits the captured `PreparedSubmission` without reacquiring Django settings or loading ambient Weft configuration. Later changes to `BASE_DIR`, Django `CONTEXT`, environment, or CWD must not redirect a submission using the captured runtime root. Core runtime preparation expands home-relative and relative explicit TaskSpec context paths once, preserving their absolute interpretation through commit. An explicitly different TaskSpec runtime root continues to have its broker resolved by core with the captured Config; this rule does not snapshot broker project files.

### `04-SimpleBroker_Integration.md` [SB-0.4]: update root-selection prose

Replace the existing bullet about queue/status helpers honoring `WEFT_CONTEXT` with:

> `build_context()` owns root selection: an explicit `spec_context` argument takes precedence over the resolved Config's `CONTEXT` (`WEFT_CONTEXT` input). In the absence of either, project discovery starts at `fallback_root` when supplied, otherwise CWD. A discovered project owns the root; if none is found, the discovery start directory is the root. This keyword changes the discovery anchor, not the broker-target precedence. Queue/status helpers retain their supported explicit environment override behavior.

In “Project Context and Directory Scoping,” replace “Current discovery rules” and “Current broker target precedence” with:

> Root selection follows [SB-0.4]. Explicit roots are used directly. Automatic discovery searches upward from the selected fallback/CWD anchor for the configured broker-config path and name; SQLite filenames are not discovery markers. Weft-owned metadata is materialized at the resulting root when requested.
>
> At the selected root, delegate broker selection to `simplebroker.target_for_directory()`: project broker config first, then applicable configured backend selection, then directory-local SQLite fallback. Backend configuration does not independently choose a Weft artifact root. `DEFAULT_DB_LOCATION` and `PROJECT_SCOPE` retain the behavior of this explicit-directory API and do not choose or relocate the Weft root.

Replace the absolute `WEFT_PROJECT_CONFIG_PATH` boundary note with:

> An absolute `WEFT_PROJECT_CONFIG_PATH` selects the broker configuration and target only. Its parent does not become the Weft artifact root. Without an explicit root argument or resolved `CONTEXT`, the selected discovery anchor (`fallback_root` or CWD) remains the root for this case.

In “Current Context API,” append:

> `CONTEXT` is a Weft Config declaration with default `None` and external name `WEFT_CONTEXT`. It accepts a string or `None`; an empty string means absent. Nonempty path text is preserved during config loading; home expansion and relative-path resolution happen when building the context. Supplied Config/mapping inputs do not read ambient environment, including for `CONTEXT`. A supplied snapshot without `CONTEXT` has no root override. JSON transport restores this field using the receiver's local declarations, without serializing validators.
>
> `build_context(..., fallback_root=...)` accepts an optional discovery anchor. Existing creation flags retain their effects and do not promise side-effect-free resolution. Configuration and metadata errors remain errors, not reasons to select a different broker.

### `14-Python_API_Surfaces.md` [PY-1]: append context construction contract

> `WeftClient.from_context(spec_context=None, *, fallback_root=None, autostart=None)` requests a resolved context from the core context owner. `fallback_root` follows [SB-0.4] and is not an explicit override of a parameterized TaskSpec reference's declared context. An explicit path argument, caller-supplied `WeftContext`, or resolved Config `CONTEXT` is explicit. Direct `WeftClient()` construction and the factory agree about this policy. Clients preserve supplied contexts and use their resolved Config snapshots; the Django adapter does not set core's context-precedence bookkeeping.

### `14-Python_API_Surfaces.md` [PY-3]: qualify pure normalization and runtime binding

Replace the sentence beginning “`weft.client.normalize_taskspec_payload(taskspec, **overrides)`” through “reserved-metadata handling” with:

> `weft.client.normalize_taskspec_payload(taskspec, **overrides)` runs the shared definition-normalization contract without a client or context and returns the validated, normalized TaskSpec definition as a fresh JSON-compatible `dict`: the definition snapshot used by `prepare(...)` before runtime-context binding, submission-time transport encoding, or reserved-metadata handling. Overrides are applied, re-validated, and JSON round-tripped. Pure normalization preserves a relative or home-relative `spec.weft_context` declaration as text.

In the following sentence replace “raises what `prepare(...)` raises” with “raises the errors from `prepare(...)`'s shared definition-normalization stage”. Append:

> Context-aware `prepare()`, `prepare_spec()`, and `prepare_pipeline()` bind a nonempty declared `spec.weft_context` to an absolute path using the preparation-time CWD and home expansion. Binding occurs after parameterization and overrides and before any run-input adapter observes the runtime root. An absent declaration stays unset and uses the captured client's root at submission. Binding copies and validates the TaskSpec without mutating its input, TID, `io`, bundle provenance, or request flags. It opens no alternate broker; broker selection for an explicit alternate root remains at submission using captured Config. Pure `normalize_taskspec_payload()` does not perform runtime-context binding.

### Backlinks and implementation notes

During promotion add this plan to each touched spec's `Plans`/`Related Plans` section. During implementation synchronize nearby mapping notes and module/function docstrings: [SB-0.4] owns core context and Config, [PY-1] owns the public factory, [PY-3] owns pure definitions and runtime path binding, and [DJ-13.2]/[DJ-8.1]/[DJ-8.4] own bridge acquisition/export/deferred behavior. Do not claim code is implemented merely because proposed wording was promoted.

## Implementation Slices

### 1. Spec-promotion slice

Files: the three specs above and this plan. Read the cited sections and baseline before applying the exact delta. Add backlinks, record the promotion baseline, and run plan/spec hygiene and traceability comparison. Review the resulting text independently. Stop if a previously accepted source contract conflicts with the chosen anchor/explicitness rules; resolve the spec first rather than improvising in code.

### 2. Core fallback anchor and Config field

Files: `weft/_constants.py`, `weft/context.py`, `weft/client/_client.py`, `weft/commands/submission.py`, `tests/commands/test_submission.py`, `tests/context/test_context.py`, `tests/context/test_context_sqlite_only.py`, `tests/core/test_client.py`, `tests/system/test_constants.py`, `tests/system/test_config_transport.py`, root `README.md`; matching spec implementation notes. Read the installed `simplebroker/_constants.py`, `simplebroker/_project_config.py`, and `simplebroker/project.py` helpers, `tests/helpers/test_backend.py`, `weft/commands/submission.py`, and the public-client preparation tests first.

Write failing cases for fallback versus unrelated CWD, explicit `WEFT_CONTEXT`, and supplied-snapshot custody. Implement the one Config declaration and existing root resolver extension. Document `WEFT_CONTEXT` and fallback-root semantics in the root README configuration section. Forward the keyword from the client and align explicitness in both construction paths. Add no second config load. Prove parameterized-reference behavior with public preparation and inspected normalized task content. Add the core declared-path binding described above, with failing relative-path and home-expansion deferred probes; keep pure normalization unchanged and verify reference run-input adapters observe the bound root. Verify the core-focused commands below and get a slice review before moving on.

### 3. Thin Django settings adapter and pure declaration path

Files: `integrations/weft_django/weft_django/conf.py`, `integrations/weft_django/weft_django/client.py`, `integrations/weft_django/tests/test_weft_django.py`, `integrations/weft_django/README.md`; matching Django spec notes. Read `apps.py`, `decorators.py`, `_prepared.py`, and the existing deferred/export tests first.

Prove the legacy-key regression red through the real client acquisition path. Replace the catalog with settings-only accessors and request the core factory's fallback behavior. Separate pure explicit declaration copying from runtime acquisition. Replace helper-return/private-catalog assertions with the contract tests below. Retain export tripwires and snapshot/rollback tests. Update README examples to show omitted `CONTEXT` using BASE_DIR discovery, explicit pinning when intended, backend/root separation, and portable export behavior. Run the complete Django suite and get a slice review.

### 4. Cross-boundary proof, release compatibility, and reconciliation

Files: the affected tests/docs above; `integrations/weft_django/pyproject.toml`, root `pyproject.toml`, and `uv.lock` only as required for coordinated release compatibility. Follow the repository's existing release-version owner; do not invent a version in this plan. The released bridge must require at least the core release that introduces `fallback_root`, and the core Django extras must resolve a compatible bridge. Do not publish or change CI in this task.

Run the final gates and review the complete scoped delta, including the package metadata. Close traceability and the deviation/review logs, record tests replaced and evidence, and commit only the intended change. Mark this plan completed only after implementation and required verification, not after authoring it.

## Test Design and Commands

Use the narrowest real fixture. Root/target/config tests need real temporary directories and broker config files; SQLite queue custody uses real queues. Any test that starts a manager/worker must use `WeftTestHarness` and register each context/process with its owner before assertions. Reuse the existing Django test harness and backend fixtures. Do not start a manager for every matrix row. Patch tripwires only to prove forbidden export/import IO; never mock core resolution, queues, or submission for the positive custody proof.

Required contract coverage:

1. **Root matrix:** cover every row above through `build_context`/public factory and representative Django calls, including empty/unset `CONTEXT`, `Path` settings, relative paths and `~`, custom metadata/config names, and absolute broker-config paths. Use distinct CWD/fallback projects so an incorrect selection cannot pass accidentally. Explicit-default env values must not need provenance.
2. **Legacy keys:** parameterize the 11 retired suffixes (`BACKEND`, `BACKEND_TARGET`, `BACKEND_HOST`, `BACKEND_PORT`, `BACKEND_USER`, `BACKEND_PASSWORD`, `BACKEND_DATABASE`, `BACKEND_SCHEMA`, `DEFAULT_DB_LOCATION`, `DEFAULT_DB_NAME`, `PROJECT_SCOPE`) under `BROKER_`, plus an unknown `BROKER_` key. Test fixtures may list these historical regressions; production code must not. Assert actual root, broker target, and one representative real queue round trip; not just a settings accessor result.
3. **Config declaration/transport:** defaults, empty and nonempty strings, invalid mapping types, metadata/validator registration, normal environment load, authoritative mapping/Config input, JSON round trip, and older snapshots without the new field. A spawned receiver under hostile ambient `WEFT_CONTEXT` must retain the supplied root/config using the existing transport harness. Do not duplicate upstream Config serializer/validator implementation tests.
4. **Client policy:** plain constructor, factory, `connect()`, explicit path, explicit context object, environment root, and fallback discovery. For parameterized spec references assert normalized context-dependent fields and declared `spec.weft_context`, including a declared root different from fallback. A non-default broker config and resulting task handle must survive preparation/submission.
5. **Pure export/bootstrap:** with explicit Django context the export contains that declaration; without it the normalized context is `None` despite BASE_DIR/env/project markers. Keep real filesystem/broker before-after assertions and forbidden-call tripwires for export. Test import/app-ready in a subprocess with malformed ambient Weft configuration and an unwritable fallback so eager initialization would fail. Task discovery still succeeds.
6. **Deferred custody:** parameterize decorated/native TaskSpec/reference/pipeline `*_on_commit` helpers. Acquire/prepare under context A, change Django settings/environment/CWD to B before commit, and prove the request/result remains at A and absent at B. Reuse the real execution/result path for one representative case and real queued evidence for remaining families. Rollback publishes no request; invalid config/reference/payload raises before callback registration. Cover a relative explicit Django context that resolves to A, home-relative declarations with HOME changed after preparation, and a native/reference declaration selecting a distinct root. Assert the prepared absolute declaration and run-input payload agree, while pure export retains raw text. For pipelines verify the compiler's existing absolute top-level destination survives; do not add a new relative pipeline-root feature. Preserve bundle provenance, TID/IO and prepared flags. Alternate-root broker project files remain outside the snapshot guarantee.
7. **Backend/entry-point proof:** one real default SQLite Django enqueue/result run from unrelated CWD and one real PostgreSQL configured-target run using the repo backend fixture. Test an observation/management command against the submitted TID through the shared client. Use queue contents/results and metadata root, not DSN strings alone. Config errors must fail explicitly without silently creating an alternate default broker. Apply the acceptance-probe floors to invalid path/config/TaskSpec inputs through the public Python or existing CLI adapter as appropriate; Python callers receive exceptions, CLI probes assert the existing error exit class and no traceback.

Replacement coverage permits deleting the old catalog-clearing fixture and helper-return tests, and replacing duplicate assertions of bridge-side precedence. Keep tests for Weft-owned selection, non-default config custody, pure export, and transaction behavior. Do not remove tests solely because an implementation helper disappeared; name their replacement behavior in the completion record.

Load the environment first: `. ./.envrc`. On a fresh checkout install the repository extras with `uv sync --all-extras`. Use repository-managed tools:

```bash
# Slice 1 and plan authoring
./.venv/bin/python -m pytest -q -n 0 tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py

# Slice 2
./.venv/bin/python -m pytest -q -n 0 tests/context tests/core/test_client.py tests/commands/test_submission.py tests/system/test_constants.py tests/system/test_config_transport.py

# Slice 3 (explicit: this suite is outside the default root test paths)
./.venv/bin/python -m pytest -q -n 0 integrations/weft_django/tests

# Final implementation gates, including slow tests
./.venv/bin/python -m pytest -m ""
./.venv/bin/python -m pytest -q -n 0 integrations/weft_django/tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/_constants.py weft/context.py weft/client/_client.py weft/commands/submission.py tests/commands/test_submission.py integrations/weft_django tests/context tests/core/test_client.py tests/system/test_constants.py tests/system/test_config_transport.py
```

For PostgreSQL use `./.venv/bin/python bin/pytest-pg --all tests/context tests/core/test_client.py tests/commands/test_submission.py integrations/weft_django/tests`, which owns a temporary Postgres container. Alternatively, with an existing test instance, set `BROKER_TEST_BACKEND=postgres` and `WEFT_PG_TEST_DSN` and run those targets directly using the repo virtualenv; keep credentials out of the recorded command. Read `tests/helpers/test_backend.py` and `tests/conftest.py` for marker selection and per-root schema isolation. Keep the Django suite serial (`-n 0`) when using direct pytest. A skipped PostgreSQL selection proof is not a passing backend gate. No live LLM or sandbox service is needed for this change.

For traceability use the repository's installed backstitch, for example:

```bash
../backstitch/.venv/bin/backstitch check --repo-root /Users/van/Developer/weft --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --format json --output /tmp/weft-django-context-trace.json
```

Capture the baseline with the identical command and compare diagnostic identity, not only counts. Fix all findings introduced by this change. Report pre-existing debt separately rather than claiming an absolute clean gate. Check every new Markdown link target and the plan-index count in both the working tree and the commit candidate. For this plan-only deliverable run documentation gates; runtime gates belong to implementation.

## Rollout and Rollback

The core keyword and bridge consumption must ship compatibly. Release core first, or coordinate the package pair, and raise the bridge's core minimum before releasing it. Do not add a catch-`TypeError` compatibility fallback, which would restore duplicate resolution. No upstream SimpleBroker release is needed for this plan.

Document intentional differences: stray `BROKER_*` and default-valued backend settings no longer redirect artifact creation into CWD; Django default discovery begins at BASE_DIR; `WEFT_CONTEXT` becomes available through the core context builder; pure exports without explicit context are portable declarations. Existing clients keep their captured snapshot. Restart long-lived Django processes to adopt new code/settings through the normal deployment process.

Before rollout, record the selected root and redacted broker target for a Django operation and its equivalent core operation. Confirm one task/result and one deferred commit/rollback pair at the intended broker. If a deployment relied on the old accidental CWD destination, explicitly configure `CONTEXT` to that existing root before upgrading; do not move or delete queues/artifacts automatically. Monitor for split destinations rather than assuming a successful enqueue proves the destination is correct.

Rollback reverts the coordinated core/bridge changes and pins compatible package versions. No persisted schema changes occur. Tasks already queued stay at their original broker and must drain or be inspected there; rollback must not duplicate submissions or delete directories created during validation. Config JSON restoration with a missing `CONTEXT` is covered; rolling code back does not relocate an existing runtime context.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## Review and Execution Log

Independent review must read this plan, its exact proposed spec delta, baseline specs, and the named owners. Answer PASS/BLOCKED: could a zero-context engineer implement it confidently, and would it meaningfully degrade correctness or safety? Prefer removal of unnecessary work. Findings must identify a location and severity; separate unrelated observations. The author dispositions every finding and requests a focused second pass for accepted fixes. Implementation also requires review after each meaningful code slice and before completion.

| Pass | Findings and disposition | Result |
|---|---|---|
| Author fresh-eyes, 2026-09-14 | Corrected the upstream Config source path, added root README maintenance, and specified the real PostgreSQL runner. Confirmed constructor/factory explicitness parity, missing-field snapshots, and the alternate TaskSpec-root limit on deferred custody. | PASS; independent review still required. |
| Upstream/core audit, 2026-09-14 | Separate agent verified installed SB 8.2.2 / PG 4.2.1, target/root precedence, inert DB_LOCATION/SCOPE, absent Config provenance, and creation side effects. Findings incorporated in intended design and test matrix. | Complete. |
| Reviewer availability, 2026-09-14 | Claude CLI was present, but an actual review call failed: version 2.1.207 requires 2.1.251 or newer for the requested model. Used an independent Codex reviewer; same-family limitation disclosed. | Different-family attempt unavailable; independent review retained. |
| Independent plan and spec-delta review, round 1 | F1 (P2): raw relative Django CONTEXT can be resolved again after CWD changes at commit. Accepted. Added shared core runtime preparation binding, exact [PY-3] equality/timing clarification, and relative/home-path, adapter, purity, and pipeline-preservation tests. No Django rebinding and no new transport type. | BLOCKED on F1; focused verification requested. |
| Independent review, round 2, 2026-09-14 | F1 verified resolved in R1. Reviewer checked core binding ownership/timing, exact [PY-3] clarification, regression evidence requirements, and preservation of the alternate-broker boundary. No new defects found. | PASS, plan only. |

## Revision Log

| Revision | Reason and scope | Review requirement |
|---|---|---|
| R1, 2026-09-14 | Accepted F1 adds declared-path binding to the three core runtime preparation owners and [PY-3]; this makes the promised transaction custody true for relative paths. Classification remains 5, with the same submission boundary and purity constraints. | Focused independent verification of F1 and defects introduced by its fix. |

Planning verification and implementation evidence are recorded separately. No runtime implementation is claimed by this draft.


### Planning verification, 2026-09-14

- Plan metadata and spec hygiene passed: 8 tests against the isolated `e35b0a27` plus plan/index candidate; 6 tests with the repository's separate test-audit changes. All 17 local Markdown links in the plan resolve. Plan counts and row status match each tested corpus.
- Backstitch comparison against the identical isolated baseline produced exactly the same diagnostics: 28 errors, 1,101 warnings, and 593 informational findings. No diagnostics were introduced. This is baseline parity, not a claim that pre-existing traceability debt is cleared.
- Fresh-eyes review and independent R1 verification are complete. Runtime code, normative specs, and dependency versions have not been changed by this planning deliverable; their checks belong to the implementation slices above.
