# Runtime Liveness Probe Registry Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.2]; docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-7]
Superseded by: none

## 1. Goal

Refactor runtime-specific manager liveness checks behind a very small in-memory
probe registry so core manager lifecycle code can ask for runtime liveness
without importing Docker-specific behavior. Docker container inspection belongs
in `extensions/weft_docker`; core owns the registry, generic fallback policy,
and manager selection semantics.

## 2. Source Documents

- [docs/specifications/01-Core_Components.md](../specifications/01-Core_Components.md) [CC-3.2] defines the `RunnerHandle` contract and `control.authority` boundary.
- [docs/specifications/03-Manager_Architecture.md](../specifications/03-Manager_Architecture.md) [MA-1.4], [MA-3] define manager registry heartbeat, liveness, selection, and bootstrap ownership.
- [docs/specifications/05-Message_Flow_and_State.md](../specifications/05-Message_Flow_and_State.md) [MF-7] defines bootstrap and supervised/containerized manager liveness rules.
- [docs/agent-context/engineering-principles.md](../agent-context/engineering-principles.md) requires extending the existing durable spine and keeping runtime-specific knowledge only where it serves execution, isolation, observability, or clear failure.
- [docs/agent-context/runbooks/runtime-and-context-patterns.md](../agent-context/runbooks/runtime-and-context-patterns.md) explains manager registry state as an append-only runtime snapshot.

Current context:

- A direct core helper for Docker liveness may exist in the active branch. This
  plan treats that as an implementation staging point, not the final design.
  The final state must move Docker-specific subprocess/API inspection into
  `extensions/weft_docker`.

## 3. Context and Key Files

Files to modify:

- `weft/ext.py` or a new small core module such as `weft/runtime_liveness.py`
- `weft/core/manager_runtime.py`
- `weft/core/manager.py`
- `extensions/weft_docker/weft_docker/plugin.py` or a new
  `extensions/weft_docker/weft_docker/liveness.py`
- `extensions/weft_docker/tests/test_docker_plugin.py` or a new focused Docker
  extension test file
- `tests/core/test_manager.py`
- `tests/commands/test_run.py`
- `tests/core/test_runtime_handle_liveness.py` if it exists from the direct
  helper attempt
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `README.md`
- `docs/lessons.md`

Read first:

- `weft/ext.py` for `RunnerHandle`, runner plugin contracts, and import-boundary
  expectations.
- `weft/core/manager_runtime.py` for `_snapshot_registry`,
  `_manager_record_is_stale`, `_select_active_manager`, and the PING/PONG rescue
  path.
- `weft/core/manager.py` for `Manager._manager_record_is_live` and manager-owned
  leadership replay.
- `extensions/weft_docker/weft_docker/plugin.py` and
  `extensions/weft_docker/weft_docker/_sdk.py` for current Docker SDK/binary
  helpers and runner plugin registration patterns.
- `tests/commands/test_run.py` around manager-selection PING/PONG tests.
- `tests/core/test_manager.py` around external-supervisor manager liveness.

Comprehension checks before editing:

- Which callers reduce `weft.state.managers` and must agree on whether a row is
  live: command-side manager selection, startup settlement, and manager-owned
  leadership replay?
- When a runtime probe returns `unknown`, why must startup fall back to heartbeat
  and optional PING/PONG rather than treating the manager as stale?

Current structure:

- `RunnerHandle` is the durable runtime identity shape.
- `manager_runtime._snapshot_registry()` reads the append-only manager registry,
  prunes stale active rows, and may send one keyed PING to rescue a stale-looking
  canonical row.
- `Manager._manager_record_is_live()` independently replays registry state for
  manager-owned dispatch leadership. It must use the same liveness semantics as
  command-side manager selection.
- Docker runner code already lives under `extensions/weft_docker`; core should
  not import Docker SDK modules or run Docker commands directly in the final
  design.

## 4. Invariants and Constraints

- Preserve the existing manager bootstrap and dispatch spine. Do not introduce a
  second manager startup path.
- Preserve `RunnerHandle` JSON shape: `runner`, `kind`, `id`, `control`,
  `observations`, `metadata`.
- Do not change queue names, manager registry payload fields, TID format, or
  state transition rules.
- Keep `weft.state.*` queues runtime-only.
- Keep registry replay generator-based. Do not replace append-only history
  reads with fixed-size peeks.
- Probe lookup must be best-effort. A missing extension, missing Docker binary,
  Docker daemon failure, timeout, or probe exception returns `unknown`, not a
  fatal manager startup error.
- A probe may return definitive `stale` only when the runtime can prove the
  handle is dead, such as Docker reporting the exact container missing or not
  running.
- A probe may return `live` only when it positively proves the named runtime is
  live. Ambiguous output must be `unknown`.
- Do not make core depend on `weft_docker` being installed.
- Do not add a broad plugin framework. This is a small in-memory registry, not a
  new extension lifecycle system.
- Do not make probe registration durable. It is process-local and discovered by
  ordinary Python import/plugin loading.
- Do not mock SimpleBroker manager registry semantics in tests that prove
  manager selection. Mock only the external Docker boundary.

Rollback:

- Removing the registry and Docker probe must restore the existing heartbeat and
  PING/PONG behavior without requiring queue cleanup or payload migration.
- Existing manager registry rows remain compatible because no persisted schema
  changes are introduced.

## 5. Tasks

1. Define the core probe registry contract.

   Outcome: core exposes a tiny API that can register and resolve liveness
   probes without knowing Docker.

   Files:

   - Prefer a new `weft/runtime_liveness.py` if importing from `weft/ext.py`
     would make `ext.py` too broad.
   - Export only if needed by extensions.

   Required shape:

   ```python
   RuntimeLiveness = Literal["live", "stale", "unknown"]
   RuntimeLivenessProbe = Callable[[RunnerHandle], RuntimeLiveness]

   def register_runtime_liveness_probe(
       key: str,
       probe: RuntimeLivenessProbe,
   ) -> None: ...

   def runtime_liveness_from_registered_probe(
       handle: RunnerHandle,
   ) -> RuntimeLiveness: ...
   ```

   Keying rule:

   - Start with `handle.runner`.
   - Also support a small optional alias registration for container runtimes if
     needed, but do not infer Docker in core.
   - Registration overwrite should be explicit and deterministic. Either allow
     last-write-wins with a debug log or raise on conflicting keys; choose the
     simpler rule and test it.

   Error rule:

   - Probe exceptions are caught and return `unknown`.

   Tests:

   - New core unit tests for register, lookup miss, lookup hit, and probe
     exception returning `unknown`.

   Stop and re-evaluate if:

   - The registry starts importing plugins automatically.
   - The API grows beyond registration and lookup.

2. Wire core manager liveness through the registry.

   Outcome: `manager_runtime` and `Manager` agree on registered-probe liveness
   before falling back to heartbeat age.

   Files:

   - `weft/core/manager_runtime.py`
   - `weft/core/manager.py`

   Required behavior:

   - For `host-pid`, keep existing scoped host-process logic.
   - For `external-supervisor` with scoped host processes, keep existing scoped
     host-process logic.
   - For `external-supervisor` without scoped host processes, call
     `runtime_liveness_from_registered_probe(handle)`.
   - `live` means not stale.
   - `stale` means stale and definitive; do not send PING/PONG rescue for a
     definitive runtime-stale result.
   - `unknown` means fall back to existing heartbeat-age logic and optional
     PING/PONG rescue.

   Cleanup requirement:

   - If a direct Docker helper currently exists in `weft/helpers/__init__.py`,
     remove it from core and move equivalent Docker-specific code to the Docker
     extension in Task 3.

   Tests:

   - Update manager-selection tests so registered `unknown` preserves the
     existing stale heartbeat plus PING/PONG behavior.
   - Add a test where registered `stale` prunes the active manager immediately
     and does not write a PING to the manager control queue.
   - Add a manager-owned replay test where registered `stale` makes
     `Manager._manager_record_is_live()` return `False`.

   Stop and re-evaluate if:

   - Command-side and manager-owned liveness start using separate helper logic.
   - A probe result becomes fatal to manager startup.

3. Move Docker-specific liveness into `extensions/weft_docker`.

   Outcome: the Docker extension registers a probe that can interpret
   Docker-backed `RunnerHandle` values and answer `live`, `stale`, or
   `unknown`.

   Files:

   - `extensions/weft_docker/weft_docker/plugin.py`
   - optionally `extensions/weft_docker/weft_docker/liveness.py`
   - `extensions/weft_docker/tests/...`

   Probe behavior:

   - Accept handles that are clearly Docker-backed by `handle.runner == "docker"`
     or by explicit Docker supervisor observations such as
     `observations.container_runtime == "docker"`.
   - Resolve the container identifier from, in order:
     `observations.container_id`, `metadata.container_id`,
     `observations.container_name`, `metadata.container_name`, then a
     Docker-prefixed `handle.id` if present.
   - Use existing Docker extension helpers where practical. Prefer Docker SDK
     helpers if they are already loaded and testable; a small `docker container
     inspect` subprocess wrapper is acceptable only inside the extension.
   - Missing/stopped exact container returns `stale`.
   - Running exact container returns `live`.
   - Docker SDK import failure, Docker daemon unavailable, timeout, malformed
     state, or non-Docker handle returns `unknown`.

   Registration point:

   - Register during Docker plugin import or `get_runner_plugin()` construction,
     whichever matches existing extension loading. The registration must be
     idempotent.
   - Also register a `manager-supervisor` key if that is the runner value used
     by Docker-backed supervised manager handles. Do not require core to know
     that name means Docker.

   Tests:

   - Unit-test the Docker probe with fake Docker client/subprocess results:
     running, missing, stopped, unavailable, and non-Docker handle.
   - Test that loading the Docker plugin registers the probe.
   - Keep external Docker calls mocked; these tests should not require a live
     Docker daemon.

   Stop and re-evaluate if:

   - The Docker extension needs core manager internals to register the probe.
   - The probe needs to mutate the handle or manager registry row.

4. Update specs and operator docs.

   Outcome: docs describe the registry boundary, not Docker-in-core.

   Files:

   - `docs/specifications/01-Core_Components.md` [CC-3.2]
   - `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-3]
   - `docs/specifications/05-Message_Flow_and_State.md` [MF-7]
   - `README.md`
   - `docs/lessons.md`

   Required doc language:

   - Core owns runtime-handle liveness lookup and fallback policy.
   - Extensions may register process-local liveness probes for runtime-specific
     handles.
   - Inconclusive or unavailable extension probes are `unknown` and fall back to
     heartbeat/PING-PONG behavior.
   - Docker-specific container inspection is owned by `weft_docker`, not core.

   Stop and re-evaluate if:

   - Docs imply the registry is durable, mandatory, or an orchestration health
     plane.

5. Verification and review.

   Required local commands:

   ```bash
   . ./.envrc
   ./.venv/bin/python -m pytest tests/commands/test_run.py -k 'manager and stale' -q
   ./.venv/bin/python -m pytest tests/core/test_manager.py -k 'manager_liveness' -q
   ./.venv/bin/python -m pytest extensions/weft_docker/tests/ -q
   ./.venv/bin/ruff check weft extensions/weft_docker tests/commands/test_run.py tests/core/test_manager.py
   ./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
   ```

   Review gate:

   - Run an independent plan review before implementation, preferably with a
     different agent family if available.
   - Run a completed-work review before landing because this touches manager
     election, extension contracts, and documentation.

## 6. Out of Scope

- No new persistent registry queue.
- No new TaskSpec fields.
- No CLI shape changes.
- No generic supervisor management API.
- No Docker image/build/run behavior changes.
- No automatic plugin discovery beyond the existing extension/plugin loading
  path.
- No cleanup of old manager registry rows beyond existing prune behavior.

## 7. Acceptance Criteria

- Core manager code has no Docker-specific subprocess calls, Docker SDK imports,
  or Docker string parsing except generic registry keys supplied by extensions.
- Docker liveness code lives in `extensions/weft_docker`.
- A registered Docker probe can make a missing/stopped container a definitive
  stale-manager result.
- If the Docker extension is absent or the probe returns `unknown`, existing
  heartbeat and PING/PONG behavior still works.
- Command-side manager selection and manager-owned leadership replay use the
  same core registry result semantics.
- Specs, README, and lessons describe the final extension-owned boundary.
