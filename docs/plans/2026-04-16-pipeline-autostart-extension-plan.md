# Pipeline Autostart Extension Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Extend autostart manifests so `target.type="pipeline"` launches stored
pipelines through the same first-class pipeline runtime already used by
`weft run --pipeline`. This slice must preserve the ordinary manager spawn
path and must not make `weft/core/manager.py` depend on command-layer code.

## 2. Source Documents

Source specs:

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1.2]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.6]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1]
- [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md) [PL-0.2], [PL-3.3]

Related plans:

- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](./2026-04-16-autostart-hardening-and-contract-alignment-plan.md)

## 3. Context and Key Files

Files to touch:

- `weft/core/manager.py`
- `weft/core/pipelines.py`
- `weft/commands/specs.py`
- `weft/commands/run.py`
- `tests/core/test_manager.py`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/12-Pipeline_Composition_and_UX.md`

Current structure:

- manager autostart supports stored task specs only and warns on pipeline
  targets
- `weft run --pipeline` already compiles a stored pipeline into a first-class
  pipeline task and submits that task through the normal spawn path
- stored spec lookup currently lives under `weft/commands/specs.py`

## 4. Invariants and Constraints

- Keep one durable execution spine: autostarted pipelines must still enter
  through the manager inbox and launch as ordinary child tasks.
- Do not add a pipeline-specific autostart manifest schema.
- Keep `core/manager.py` free of command-layer imports.
- Preserve the autostart bookkeeping fixes from the earlier slice: launch and
  restart accounting only advance after a successful queue write.
- Preserve the live-manager adoption rule: later `weft run` flags do not
  reconfigure an already-running canonical manager.

## 5. Tasks

1. Add or expose a core-usable stored-spec resolution seam so both
   `weft run --pipeline` and manager autostart can resolve stored pipeline
   names and stage task references without command-layer imports.
2. Extend manager autostart payload building so `target.type="pipeline"`
   resolves the stored pipeline, compiles it with the effective context, and
   enqueues the compiled pipeline task with the existing autostart metadata and
   input/default handling.
3. Add broker-backed manager tests that prove autostart launches a stored
   pipeline and that `ensure` mode restarts it after the pipeline run exits.
4. Update the specs so pipeline autostart is part of the current contract
   rather than a later phase.

## 6. Verification

- `./.venv/bin/python -m pytest tests/core/test_manager.py -k 'autostart and pipeline' -q -n 0`
- `./.venv/bin/python -m pytest tests/core/test_manager.py -k autostart -q -n 0`
- `./.venv/bin/ruff check weft/core/manager.py weft/core/pipelines.py weft/commands/specs.py weft/commands/run.py tests/core/test_manager.py`
