# Django Override Normalization Seam Plan

Status: draft
Source specs: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3], [PY-4]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-1.1], [DJ-2.1], [DJ-2.2], [DJ-6.3], [DJ-8.1], [DJ-8.4]; docs/specifications/02-TaskSpec.md [TS-1.4A]; docs/specifications/07-System_Invariants.md OBS.9, CTX.5; docs/specifications/08-Testing_Strategy.md [TS-3.1]
Superseded by: none

Class: 5 — adds normative text to spec 14 ([PY-1] gains one `__all__`
name; [PY-3] gains the contract for it) and clarifies the proposed 13C
contract ([DJ-8.1]). Risky trigger: a public contract changes on two
surfaces — the `weft.client` Python API ([PY-1]) and the `weft-django`
`as_taskspec_for_call` result and override semantics — so the hardening
checklist applies and review precedes promotion and implementation. Plan
type: implementation with spec revision. Promotion strategy: **A** for
spec 14 (text lands in the spec-promotion slice without link claims; the
core code slice adds the `Spec:` backlink and the `Implementation:` note
together); **D** for 13C (clarification of a proposed contract; no code
cites `[DJ-*]` codes — grep over `integrations/`, `weft/`, `tests/` at
178e3a34 finds none) and for the integration README (user documentation,
which lands with the Django code slice, not at promotion). Origin:
complexity/duplication review finding 08 ("Django override policy");
owner decision Van, 2026-09-08 (recorded in §1). Revision 1 (2026-09-08)
changed the seam shape and re-entered review; revision 2 (2026-09-08)
applied the round-2 dispositions without changing the seam, the delta's
substance, or the register classes — see `## Revision Log`; it requests
a scoped round-3 verification, not a full re-review.

## 1. Goal

`weft_django` carries a private copy of the core submit-override
application (`integrations/weft_django/weft_django/client.py:223-288`,
`_apply_taskspec_payload_overrides`) whose semantics have drifted from the
core path it copies (`weft/commands/submission.py:84-163`,
`apply_submit_overrides`, plus `_validate_submit_overrides` :166-170).
Its only caller with non-`None` overrides is
`RegisteredWeftTask.as_taskspec_for_call` (decorators.py:103-117). The
two `enqueue` paths call the builder with `overrides=None` (client.py:395,
:426) — reaching only the JSON-copy branch of the private function — and
hand overrides to `WeftClient.submit`/`prepare`, so they already use core
policy. Verified at 178e3a34 (probe re-run 2026-09-08):

| Input via `_overrides` | `as_taskspec_for_call` today | `enqueue` / `WeftClient.prepare` today |
|---|---|---|
| `timeout=None` | `spec.timeout` written as `None` (declared `30.0` cleared) | ignored; `30.0` stands (submission.py:127) |
| `unknown_flag=1` | silently dropped | `TypeError("Unknown submit override(s): unknown_flag")` (:166-170) |
| `stream_output=None` | `spec.stream_output=None` → payload fails `TaskSpec` validation (`bool_type`) | ignored |
| `wait=True` | silently dropped | stripped by `_submit_kwargs` (:157-160) and honored as a Django wait |
| `memory_mb=0` | written unvalidated | pydantic `ValidationError` (`LimitsSection.memory_mb` `ge=1`, model.py:304-306) |
| `name="_weft.x"` | written unvalidated | `ValueError` (reserved endpoint namespace, submission.py:149-157 → endpoints.py:71; OBS.9) |
| result | unvalidated compact dict `{name, spec, metadata}` | validated, normalized `TaskSpec` |

13C [DJ-8.1] promises a "validated TaskSpec payload"; the export is not
validated. The copy exists because `weft.client` exposes no way to obtain
a normalized definition without a broker-bound client: `WeftClient`
requires a `WeftContext` (`weft/client/_client.py:42`, `context or
build_context(...)`), and `build_context` writes — it creates
`.weft/config.json` when missing (`weft/context.py:268` → `:469-478`),
creates and tightens directories (`:290-300`, CTX.5), and initializes the
broker database (`:303`). The current export performs none of that; it
constructs no Weft context, reads no configuration, opens no broker, and
writes nothing (it still runs the configured request-ID provider, as
`enqueue(...)` does).

Owner decision (Van, 2026-09-08): core submit-override semantics win on
both Django surfaces — explicit `None` overrides are ignored, unknown
names raise, and `as_taskspec_for_call` returns a validated payload
produced by the same normalization `WeftClient.prepare` uses. Delete the
private copy. Provide the missing public seam through `weft.client`. No
backward compatibility: the behavior changes on `as_taskspec_for_call`
are class A and go in `CHANGELOG.md`.

Seam decision (revision 1): add one context-free public function,

```python
weft.client.normalize_taskspec_payload(taskspec, **overrides) -> dict[str, Any]
```

exported in `weft.client.__all__` ([PY-1] change). It runs exactly the
sequence `WeftClient.prepare` runs today — `_validate_submit_overrides` →
`apply_submit_overrides(normalize_taskspec(...))` → `prepare_taskspec`
(validate, JSON-snapshot, re-validate) — and returns the JSON-mode dump of
the snapshotted `TaskSpec`. To avoid a second copy of that sequence, the
body of `submission.prepare` (:417-428) moves into a context-free leaf
`submission.prepare_definition(taskspec, overrides, *, payload=None)` —
overrides travel as one explicit `Mapping`, so a caller-supplied
`payload=` key on the public seam is an unknown override and raises
`TypeError` like any other non-vocabulary name (round-2 probe
`08-rev2/probe_r2.py`: with a `**overrides` leaf the keyword parameter
captured `payload={"x": 1}` silently and `payload=object()` surfaced as
the snapshot `ValueError`; with the mapping parameter it raises
`TypeError("Unknown submit override(s): payload")`). `prepare(context,
...)` becomes a one-line delegate passing its own `**overrides` dict
through (its `context` parameter is already unused; verified :417-428).
Django then calls `normalize_taskspec_payload(base_payload, **overrides)`
and never touches `get_core_client()` on the export path.

Probe (2026-09-08, `scratchpad/plans/08-rev/probe_pure.py`): with every
`WEFT*` environment variable removed and the working directory a fresh
`0o500` temp directory, the sequence above runs to completion with no
`WeftContext`, creates no file in the working directory, and imports no
context builder; `None` is ignored for every `SUBMIT_OVERRIDE_NAMES`
entry on a template that declares every overridable field; the dump has
`tid: None`, no `_weft_bundle_root` key, and re-normalizes to itself.
TaskSpec validation reads no configuration: the only injected default is
the `DEFAULT_MEMORY_MB` constant (`weft/core/taskspec/model.py:282-283`),
and `spec.working_dir`/`spec.weft_context` are accepted as strings
without filesystem checks (probe `probe_paths.py`). Host-runner limit
validation needs no config value. The one filesystem touch reachable on
any input is read-only: a `TaskSpec` that already carries bundle
provenance re-resolves that root through `TaskSpec.set_bundle_root` →
`Path(...).expanduser().resolve()` (`weft/core/taskspec/model.py:1394-1404`)
when validation re-runs. Round-2 probe (`08-rev2/probe_r2.py`, trapping
`Path.resolve`): two calls on a bundle-rooted `TaskSpec`, zero on a plain
mapping. Django templates are mappings and never carry a bundle root, so
the Django path makes no filesystem call; the public contract therefore
claims "no context, no configuration read, no broker, no writes" — not
"zero filesystem reads" — and bundle provenance is excluded from the
export mapping (Open Owner Question 4).

Rationale against the alternatives:

- `PreparedSubmission.taskspec_payload()` (revision 0) requires a
  `WeftClient`, hence `build_context`, hence the writes above — a class-D
  loss of the export's filesystem and broker independence (review round 1,
  Codex #1). Rejected.
- `WeftClient.normalize(...)` has the same defect: every `WeftClient`
  owns a context.
- `WeftClient(build_context(..., create_dirs=False, create_database=False))`
  still writes `config.json` via `_load_project_config` (:268, :469-478)
  and still resolves a root by upward discovery from `Path.cwd()`
  (:234-236) when Django supplies no `CONTEXT`. Rejected.
- Importing `weft.commands.submission` from `weft_django` is forbidden by
  13C [DJ-2.2] :288-290 and by [PY-1] (command leaves are private).

## 2. Source Documents

Governing (current contract):

- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] :7-28. The
  inventory sentence at :25-28 reads: "`weft.client.__all__` retains its
  existing inventory and adds exactly `CommandError`, `CommandUsageError`,
  `CommandTimeoutError`, `CommandExecutionError`, `SubmissionError`,
  `SubmissionValidationError`, and `SubmissionManagerError`." :15-17:
  "Each `__all__` is its authoritative public-name inventory. Names not
  exported there, including `weft.core.*`, helpers, constants, command
  leaves, and `execute_run`, are private." (`apply_submit_overrides` and
  the new `prepare_definition` are command leaves → private; Django must
  not import them.)
- 14 [PY-3] :210-232 — client submission pipeline and error typing. Spec
  14 has no section enumerating members of client handles; this plan does
  not add one (the seam is a module-level name, which [PY-1] enumerates
  exactly).
- 14 [PY-4] :234-244 — layering: `client -> commands -> core`; `weft/client/`
  imports no `weft.core` module today (verified by grep) and must not
  start to.
- `docs/specifications/07-System_Invariants.md` OBS.9 :320-321 (endpoint
  names under `_weft.` are reserved; the export now enforces it via core)
  and CTX.5 :896-901 (context build tightens directory modes — the reason
  the export must not build a context).
- `docs/specifications/02-TaskSpec.md` [TS-1.4A] :391-434 — submission
  shaping hooks "must not branch Weft onto a different durable execution
  path"; the export is a local shaping read, not a path.
- `docs/specifications/08-Testing_Strategy.md` [TS-3.1] :186-235 —
  suppression registry ownership: "update the human row, cardinality, and
  source pointer together; then regenerate only the delimited derived
  index with `./.venv/bin/python bin/ruff_suppression_index.py --write`".
- `integrations/weft_django/README.md` "Submission Handle" :58-91 —
  deferred helpers "validate and snapshot before registering Django's
  `transaction.on_commit()` callback. Missing spec references, invalid
  overrides, and unserializable payloads fail before the app transaction
  commits." The README does not document `as_taskspec_for_call`.

Proposed (design evidence, not the governing contract):

- `docs/specifications/13C-Using_Weft_With_Django.md`. **Status
  mechanism:** 13C is staged by the *prose* mechanism only —
  `docs/specifications/README.md:36-37` ("a proposed first-party framework
  integration contract. It is not current core Weft behavior") and the
  13C header :3-9 ("does not change current core Weft behavior on its
  own"). It has no machine classification: its filename is not `*A-*`,
  and no `planned_spec_globs`/`exploratory_spec_globs` configuration
  exists in the repository (the terms appear only as prose in
  `docs/agent-context/decision-hierarchy.md:191` and
  `docs/agent-context/runbooks/writing-plans.md:412`). Consequently the
  [DJ-8.1] edit below is an in-place clarification of a proposed document
  under the prose mechanism (strategy D); it neither promotes 13C to
  current contract nor requires a classification change, and shipped code
  must not cite `[DJ-*]` codes as its governing spec. Sections used:
  [DJ-1.1] :154-164 (core owns "`TaskSpec` shape and validation");
  [DJ-2.1] :232-238 (what preparation does); [DJ-2.2] :255-290 (client
  API the Django package may depend on; :288-290 forbids importing
  `weft.commands.submission`); [DJ-6.3] :536-552 (`as_taskspec_for_call`
  is the manual-composition surface); [DJ-8.1] :624-648; [DJ-8.4]
  :720-775 (override vocabulary incl. `wait` :731; deferred `wait` rule
  :749-759); [DJ-19] :1312-1321 (monorepo rule heading :1316, operative
  text :1318-1319: "version coordination is a release management concern
  rather than a runtime mismatch concern"; the fail-clearly rule after
  the "Once the package is split" heading :1321 applies only after a
  future repository split).

Style and guidance: `CLAUDE.md` §1.1, §4.2-4.8, §4.11;
`docs/agent-context/engineering-principles.md` §3 (validate at the
boundary, then stay strict); `docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/writing-plans.md` :427-437 (strategy A).

History (evidence only): `f898244a` (2026-04-20) created both the core
and the Django functions; `e8c5b19e` (2026-08-06, "Clarify Django override
invariants") changed the private copy's errors to `TypeError` and added
the monkeypatched `json.loads` test; RUFF-SUP-209 approved 2026-08-05;
`85196bc2` ("Release 0.9.99", 2026-08-31) is the release-commit pattern:
root version, sub-package versions, root extras floors, and `uv.lock`
move together.

## 3. Context and Key Files

Current structure (read before editing):

- `weft/commands/submission.py` — the one normalization path.
  `normalize_taskspec` :73-81 (mapping → template `TaskSpec` when no
  `tid`); `apply_submit_overrides` :84-163 (every `None` is skipped:
  `if metadata:` :112, `is not None` :114/:116/:123/:125/:127/:129/:137/:149,
  `if env:` :118; `name` runs `validate_endpoint_claim_name` for
  `_weft.`-prefixed or persistent names :149-157; re-validates via
  `validate_taskspec_payload` :159-163); `_validate_submit_overrides`
  :166-170 (`TypeError` for names outside `SUBMIT_OVERRIDE_NAMES`,
  `weft/_constants.py:2051-2066`); `_snapshot_payload` :173-181
  (`ValueError` "Submission payload must be JSON-serializable");
  `_snapshot_taskspec` :184-191 (JSON round-trip then re-validate);
  `prepare_taskspec` :323-339 ("without queue writes"); `prepare`
  :417-428 (`context` parameter unused); `submit` :431-442 (delegates to
  `prepare`). `prepare` does **not** translate errors (unknown name → raw
  `TypeError`; invalid value → pydantic `ValidationError`, a
  `ValueError`); `prepare_spec` :459-462 translates `TypeError` →
  `SubmissionValidationError` and :511-516 translates `ValidationError` →
  `SubmissionValidationError` and other `TypeError`/`ValueError` →
  `CommandUsageError`; `prepare_pipeline` :602-611 and `submit_command`
  :693 raise raw `TypeError` for unknown names.
- `weft/commands/types.py:93-104` — `PreparedSubmissionRequest(name,
  taskspec: Any, payload, seed_start_envelope, allow_internal_runtime)`;
  `taskspec` is intentionally `Any` so the types module does not import
  core models. After `prepare_taskspec` it holds a validated `TaskSpec`.
- `weft/client/_client.py` — `WeftClient.__init__` :32-46 (:42 builds a
  context when none is given); `from_context` :48-56; `submit` :63-70
  (→ `prepare().submit()`); `prepare` :72-85; module-level `connect`
  :169. The new function lives here as a second module-level function.
  `weft/client/__init__.py:28-31` imports, `:39-63` `__all__`, pinned
  exactly by `tests/architecture/test_import_boundaries.py:949-1000`
  (parametrize data) / `:1002` (`test_official_python_surface_inventories_are_exact`).
  `weft/client/_prepared.py:14-33` — `PreparedSubmission(client,
  _request)`; unchanged by this plan (note: its `client` field is a
  public dataclass attribute; the plan makes no member-inventory claim
  about it).
- `weft/core/taskspec/transport.py` — `validate_taskspec_payload` :18-45
  (`bundle_root=` sets provenance on the model); `encode_taskspec_transport_payload`
  :67-73 adds the top-level `_weft_bundle_root` key
  (`TASKSPEC_BUNDLE_ROOT_FIELD`, `weft/_constants.py:2069`) at queue/process
  boundaries. `weft/core/pipelines.py:471` stores transport-encoded stage
  TaskSpecs inside compiled pipeline metadata, so a pipeline TaskSpec's
  *nested* metadata may legitimately contain that key; `weft/core/spawn_requests.py:136`
  (`_prepare_spawn_metadata`) handles reserved metadata at submit time.
  The seam returns the pre-transport snapshot; see §4.
- `weft/core/taskspec/model.py` — `SpecSection.timeout: float | None = None`
  :958 (no positivity constraint: `timeout=-1` is accepted);
  `LimitsSection.memory_mb` `ge=MIN_MEMORY_LIMIT` (=1, `_constants.py:1846`)
  :304-306; `cpu_percent` `ge=1, le=100` :307-309; TaskSpec `name` and
  `spec.runner.name` are `min_length=1` (probe: `name=""` rejected).
- `weft/context.py` — `build_context` :223 (discovers upward from
  `Path.cwd()` when `spec_context` is `None`, :234-236); config write
  :268 → `_load_project_config` :469-478; directory creation and mode
  tightening :290-300; database initialization :303.
- `integrations/weft_django/weft_django/client.py` — imports from
  `weft.client` :12-21; `get_core_client` :149-150
  (`WeftClient.from_context(resolve_context_override())`); `_submit_kwargs`
  :157-160 (strips `wait`); `_validate_decorated_task_overrides` :215-220
  (runner ∈ {None, "", "host"} else `ValueError`);
  `_apply_taskspec_payload_overrides` :223-288 (delete); `_build_limits`
  :291-301; `build_registered_task_taskspec` :304-379 (builds the compact
  payload from decorator fields + `WEFT_DJANGO["DEFAULT_TASK"]`, stamps
  `spec.weft_context` from `resolve_context_override()` :368-370 — a
  settings read, no filesystem access (`conf.py:94-105`) — then calls the
  private copy :375-379); `submit_registered_task` :382-408;
  `submit_registered_task_on_commit` :411-441 (`prepare` :430-434 before
  `transaction.on_commit` :440).
- `integrations/weft_django/weft_django/decorators.py` — `_json_validate_call`
  :31-37 (args/kwargs JSON check, `ValueError`); `build_envelope` :85-101;
  `as_taskspec_for_call` :103-117 (lazy import of the client module —
  keep that pattern: it breaks the `client -> registry/decorators`
  import cycle); `enqueue` :119-132; `enqueue_on_commit` :134-147.
- Tests. `integrations/weft_django/tests/test_weft_django.py`:
  `TEST_ROOT` :23 (test-module-local; fixture modules cannot import it);
  `test_payload_override_copy_rejects_invalid_json_round_trip_type`
  :149-158 (monkeypatches `json.loads` to return a list — impossible
  input; delete with the function);
  `test_payload_overrides_reject_invalid_private_spec_type` :161-169
  (`{"spec": []}` — the builder never produces it; delete);
  `test_deferred_native_payload_is_snapshotted_at_registration` :396-406
  (never reaches the private copy) and
  `test_deferred_decorated_payload_is_snapshotted_at_registration`
  :410-420 (reaches only its `overrides=None` JSON-copy branch via
  `enqueue_on_commit`; keep both — they pin the snapshot rule, which core
  owns after this change); `test_as_taskspec_for_call_applies_public_submit_overrides`
  :506-520 (keep; becomes one cell of the matrix). Fixture tasks:
  `integrations/weft_django/tests/fixture_project/testapp/weft_tasks.py:8-10`
  (`echo_task`, `timeout=30.0`, nothing else declared); fixture settings
  `fixture_project/fixture_project/settings.py`: `BASE_DIR` :6-7,
  `CONTEXT` from `WEFT_DJANGO_FIXTURE_WEFT_CONTEXT` :30, `DEFAULT_TASK`
  `timeout: None` :36 and `stream_output: False` :39. The Django suite is
  not under `testpaths` (`pyproject.toml:128`); CI runs it as
  `pytest -q -n 0 integrations/weft_django/tests`
  (`.github/workflows/release-gate.yml:47`). `tests/core/test_client.py`:
  `CLIENT_API_PARITY_EXPECTATIONS` :142-219 (class-member presence guard
  :239-246; not touched — the seam is module-level and pinned by the
  exact `__all__` test); `_function_taskspec` :64;
  `test_prepare_snapshots_payload_before_submission` :454-470;
  `test_client_prepare_spec_translates_unknown_override` :525-544.
  `tests/cli/test_cli_run.py:1304` builds its bundle directory inline —
  there is no reusable bundle fixture; task 3 builds its own.
- `docs/ruff-suppression-registry.md` — human row `RUFF-SUP-209` :87.
  Its two named proofs: `test_as_taskspec_for_call_applies_public_submit_overrides`
  (the only one that exercises the override branches; kept as a matrix
  cell) and `test_deferred_native_payload_is_snapshotted_at_registration`,
  which runs the native helper path and never reaches the private
  function at all (the *decorated* deferred test :410-420, not cited by
  the row, reaches only its `overrides=None` copy branch). Retiring the
  row loses no proof. Global inventory line :227 (`C901=130`); generated
  index row :302 inside the markers beginning :229.
- Sibling embedders: `/Users/van/Developer/engram/engram/runtime/weft.py`
  imports `WeftClient` (:13) and calls `WeftClient.from_weft_context`
  (:83) only; engram contains no reference to `weft_django`,
  `as_taskspec_for_call`, `PreparedSubmission`, `.prepare(`,
  `apply_submit_overrides`, or `normalize_taskspec` (grep, `.venv`
  excluded). The change is additive for engram. Blast radius: this
  repository plus the published `weft-django` package.
- Release coupling. Weft `0.9.99` is tagged (`v0.9.99`; `CHANGELOG.md:43`
  `## [0.9.99] - 2026-08-31`; root `pyproject.toml:7`). `weft-django`
  `0.9.33` is tagged with the root (`integrations/weft_django/pyproject.toml:7`)
  and declares `weft>=0.9.95` (:17); root extras pin
  `weft-django>=0.9.33` (`pyproject.toml:49`, `:52`); `uv.lock:1730-1731`
  records `weft-django 0.9.33`. The seam ships in weft `0.9.100`; the
  Django package that requires it is `weft-django 0.9.34`. Because `uv`
  resolves the editable workspace `weft` at its declared version, a
  `weft>=0.9.100` floor cannot lock until the root version is `0.9.100`
  — so the floor, both version bumps, the extras floors, and the lock
  refresh land together in the `Release 0.9.100` commit (pattern
  `85196bc2`), which the owner cuts (task 8).

Comprehension questions (answer before editing): (1) Which function is
the only place override values are applied to a TaskSpec on the submit
path, and what does it do with `timeout=None`? (2) Why can the export
not go through `WeftClient`, and which three writes does `build_context`
perform? (3) Why do `enqueue` and `enqueue_on_commit` not need the new
seam?

## 4. Invariants and Constraints

- **One normalization path.** After this plan, override application for
  every Django surface runs through `submission.apply_submit_overrides`
  — `enqueue*` via `WeftClient.submit`/`prepare`, the export via
  `weft.client.normalize_taskspec_payload`, both of which call
  `submission.prepare_definition`. No Django-side override merging
  survives, and `prepare` keeps no body of its own. Stop if any task
  wants a Django-side "fix-up" of the normalized payload, or a second
  copy of the validate → apply → snapshot sequence anywhere.
- **The export builds nothing.** `normalize_taskspec_payload` constructs
  no `WeftContext`, reads no configuration (`load_config` is never
  called), resolves no project root, opens no broker, and writes nothing
  — no file, directory, configuration file, or queue. It is not claimed
  to perform zero filesystem reads: a `TaskSpec` input that already
  carries bundle provenance re-resolves that root read-only
  (model.py:1394-1404; §1 probe). Task 3 pins the contract under a
  read-only working directory, a cleared `WEFT*` environment, and traps
  on `build_context`, `load_config`, and broker construction. On the
  Django path the only reads outside the function are the settings read
  in `build_registered_task_taskspec` (`resolve_context_override()`
  :368-370, `conf.py:94-105`) and whatever the configured
  `REQUEST_ID_PROVIDER` does inside `build_envelope` (decorators.py:85-90,
  `conf.py:146-148`) — both pre-existing and outside this plan's claim.
- **Pre-transport snapshot.** The returned mapping is the JSON-mode dump
  of the `TaskSpec` as `prepare` holds it: after overrides and
  re-validation, after the JSON round-trip snapshot, before submission.
  Submission-time transport encoding (the top-level `_weft_bundle_root`
  marker, transport.py:67-73) and spawn-time reserved-metadata handling
  (spawn_requests.py:136) are not applied. The guarantee about
  `_weft_bundle_root` is **top-level only**: caller-supplied nested
  metadata (including compiled-pipeline stage payloads) passes through
  unchanged. Bundle provenance is not carried by the mapping. A fresh
  `dict` on every call (pydantic `model_dump(mode="json")` builds new
  containers).
- **`__all__` changes by exactly one name.** `weft.client.__all__` gains
  `normalize_taskspec_payload`; the architecture inventory test's
  expected set gains the same name; nothing else is exported.
  `weft.commands.__all__` is unchanged (`prepare_definition` is a private
  leaf).
- **Layering [PY-4].** `weft/client/_client.py` calls
  `submission.prepare_definition` and `.model_dump(mode="json")` on the
  `Any`-typed request field; it must not import `weft.core`.
  `weft_django` must not import `weft.commands` (13C [DJ-2.2] :288-290;
  README :5-6); it imports the new name from `weft.client` only.
- **Return-shape change is a superset.** Today's export is the compact
  builder dict (`name`, `spec` with 11 keys, `metadata`); the normalized
  dump adds `tid: None`, `version`, `description`, `state`, `io`, and the
  defaulted `spec` fields. Verified: the dump validates as a template and
  re-normalizes to itself. [DJ-6.3] composition consumers (pipeline stage
  loaders validate with `template=True`) accept it.
- **Decorated-task host-only rule stays Django-owned.** `runner` is a
  core override name, so the Django check
  `_validate_decorated_task_overrides` must run before normalization on
  the export path or `runner="docker"` would validate in core.
- **Error-path classification.** Fatal (raise locally, nothing submitted):
  unknown override name (`TypeError`), invalid override value (pydantic
  `ValidationError`, a `ValueError`), reserved `_weft.` name
  (`ValueError`, OBS.9), non-JSON args/kwargs (`ValueError` from
  `_json_validate_call`), non-host runner (`ValueError`). There is no
  best-effort path in this change.
- **Unchanged.** `enqueue`, `enqueue_on_commit`, native helpers, the
  `_submit_kwargs` `wait` stripping on submit paths, `PreparedSubmission`,
  TaskSpec schema, queue names, TID rules, `spec`/`io` immutability (only
  template TaskSpecs are touched), manager lifecycle, `weft.state.*`
  queues, context construction.
- **Rollback.** Pure code and doc change; no persisted format, queue
  name, or event shape changes. Reverting the commit restores the prior
  behavior completely. No one-way door. Rollout order: core seam (task 4)
  before Django rewiring (task 6); release coupling (task 8) after both,
  core first (weft `0.9.100` tagged before `weft-django 0.9.34`).
- Review gates: no new execution path; no new abstraction (no override
  registry, no merge helper, no `WeftClient.normalize`/`export` verb, no
  `PreparedSubmission` accessor); no drive-by refactor of
  `build_registered_task_taskspec` beyond removing the `overrides`
  parameter and the copy call; no mocks around normalization or
  `TaskSpec` validation; external review before promotion and before
  code.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `178e3a34` — docs/specifications/14-Python_API_Surfaces.md,
  13C-Using_Weft_With_Django.md, 02-TaskSpec.md, 07-System_Invariants.md,
  08-Testing_Strategy.md, README.md at plan authoring time (2026-09-08).
  Plan type: implementation with spec revision. Promotion baseline
  identifier: recorded after task 2.
  identifier: `c7628b6d` — the tree the promoted spec text landed on
  (spec-promotion slice, 2026-09-09). The only drift in the delta's spec
  files between `178e3a34` and `c7628b6d` is in spec 14 [PY-2]
  (client dump/load/tidy resolved-context wording, complexity-review
  corrections plan); [PY-1], [PY-3], [PY-4], the 13C sections, 02, 07 and
  08 are unchanged, so every line reference in this plan's delta still
  resolves.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/14-Python_API_Surfaces.md | A | [PY-1] replace the inventory sentence :25-28 (exact text below); [PY-3] insert after :231-232 (exact text below); `## Related Plans` :246 backlink. The `Implementation:` note and the code `Spec:` backlink land together in task 4, not in promotion. |
| docs/specifications/13C-Using_Weft_With_Django.md | D (prose-status proposed doc; in-place clarification) | [DJ-8.1] :630-631 replace and :640-645 replace; `## Backlinks` :1326 add plan link. [DJ-2.2] :268 is **not** edited (`PreparedSubmission` members are unchanged). |
| integrations/weft_django/README.md | D (user documentation; lands in task 6 with the Django code, not at promotion) | new subsection after :91, before `## Native Helpers` :93 (exact text below) |

### 14 [PY-1] — replace :25-28 ("`weft.client.__all__` retains its existing inventory and adds exactly … and `SubmissionManagerError`.")

> `weft.client.__all__` retains its existing inventory and adds exactly
> `CommandError`, `CommandUsageError`, `CommandTimeoutError`,
> `CommandExecutionError`, `SubmissionError`, `SubmissionValidationError`,
> `SubmissionManagerError`, and `normalize_taskspec_payload`.

### 14 [PY-3] — insert as a new paragraph after "…so the initial work payload has one owner." (:232)

> Submit-override semantics are identical on every surface that accepts
> `**overrides`: an override whose value is `None` is ignored and the
> template's value stands; a name outside the shared public override
> vocabulary raises `TypeError` from `prepare(...)`, `prepare_pipeline(...)`,
> and `submit_command(...)`, and `SubmissionValidationError` from
> `prepare_spec(...)`; a value the TaskSpec schema rejects raises the
> schema's validation error (a `ValueError`) from `prepare(...)` and
> `SubmissionValidationError` from `prepare_spec(...)`; a `name` in the
> reserved `_weft.` namespace raises `ValueError`. Each `submit(...)`,
> `submit_spec(...)`, and `submit_pipeline(...)` raises exactly what its
> `prepare*` counterpart raises. `weft.client.normalize_taskspec_payload(taskspec,
> **overrides)` runs that same contract without a client or context and
> returns the validated, normalized TaskSpec definition as a fresh
> JSON-compatible `dict`: the pre-transport snapshot `prepare(...)` would
> hold for the same inputs — overrides applied, re-validated, JSON
> round-tripped — before any submission-time transport encoding or
> reserved-metadata handling. It accepts a `TaskSpec` or a JSON-compatible
> mapping (a mapping without `tid` is validated as a template), raises what
> `prepare(...)` raises, constructs no Weft context, reads no
> configuration, resolves no project root, opens no broker, and writes
> nothing. Every keyword argument is an override name: `payload` is not
> part of the override vocabulary and raises `TypeError` here. The mapping
> carries no top-level bundle-root marker and no bundle provenance; a
> `TaskSpec` input that already carries a bundle root is accepted, its
> root is re-resolved read-only by TaskSpec validation, and the result
> drops it. Embedders that need a TaskSpec definition for composition
> rather than submission call it; there is no second normalization path.

(Rule-vs-code check performed 2026-09-08: `None` handling — submission.py
:112-150; `payload` keyword — reaches `_validate_submit_overrides` only
with the mapping-parameter leaf (round-2 probe); bundle-root re-resolution
— model.py:1394-1404; `TypeError` sites — :166-170 via :426, :611, :693;
`prepare_spec` translation — :459-462 and :511-516; `ValidationError`
propagates untranslated from `prepare` :427; reserved name — :149-157;
`submit` → `prepare` — `_client.py:63-70`; `submit_spec` :87 →
`prepare_spec` :104; `submit_pipeline` :124 → `prepare_pipeline` :133;
`submit_command` :148. Tasks 3 and 3b pin each with a firing test.)

### 13C [DJ-8.1] — replace :630-631 "- `as_taskspec_for_call(*args, _overrides=None, **kwargs) -> dict` representing a validated TaskSpec payload"

> - `as_taskspec_for_call(*args, _overrides=None, **kwargs) -> dict`
>   returning the validated, normalized TaskSpec payload for the decorated
>   task's generated TaskSpec with the call envelope embedded — the value of
>   `weft.client.normalize_taskspec_payload(...)` for the generated template
>   and `_overrides`

### 13C [DJ-8.1] — replace :640-645 (the "`as_taskspec_for_call(...)` rule:" block through "or runner configuration")

> `as_taskspec_for_call(...)` rule:
>
> - positional and keyword arguments before `_overrides` become the task
>   call payload, embedded in the generated TaskSpec's `spec.args`
> - `_overrides` carries TaskSpec-level submit overrides and uses the core
>   submit-override contract unchanged: the accepted names are exactly the
>   shared public override vocabulary (`name`, `description`, `tags`,
>   `env`, `working_dir`, `stream_output`, `timeout`, `memory_mb`,
>   `cpu_percent`, `runner`, `runner_options`, `metadata`); an override
>   whose value is `None` is ignored and the declared or default value
>   stands; any other name — including the submission-only `wait` — raises
>   `TypeError` locally; a `runner` other than `host` raises `ValueError`
>   (the v1 host-only rule); a value the TaskSpec schema rejects raises its
>   validation error
> - for every override in the shared public override vocabulary, the
>   export and `enqueue(...)` apply it identically; the surfaces differ in
>   where the call envelope travels (embedded in `spec.args` for the
>   export, as the work payload for `enqueue`) and in `wait`, which
>   `enqueue(...)` honors and the export rejects because it submits nothing
> - the export constructs no Weft context, reads no Weft configuration,
>   resolves no project root, opens no broker, and writes nothing; the
>   configured request-ID provider runs inside the call envelope exactly
>   as it does for `enqueue(...)`
> - the integration applies no overrides itself; it calls
>   `weft.client.normalize_taskspec_payload(...)`

(The sentence "This split avoids collisions between user task parameters
and TaskSpec override names." at :647-648 stays.)

### integrations/weft_django/README.md — insert after :91, before "## Native Helpers" (:93); lands in task 6

> ## Composition Export
>
> `task.as_taskspec_for_call(*args, _overrides=None, **kwargs)` returns the
> validated, normalized TaskSpec payload Weft would submit for that call,
> with the call envelope embedded in `spec.args`, for manual composition
> into ordinary Weft task or pipeline specs. It does not submit anything,
> builds no Weft context, reads no Weft configuration, opens no broker,
> and writes nothing (the configured `REQUEST_ID_PROVIDER` still runs, as
> it does for every call).
> `_overrides` accepts exactly Weft's public submit overrides (`name`,
> `description`, `tags`, `env`, `working_dir`, `stream_output`, `timeout`,
> `memory_mb`, `cpu_percent`, `runner`, `runner_options`, `metadata`) with
> core semantics: `None` values are ignored, unknown names (including
> `wait`) raise `TypeError`, and invalid values raise the TaskSpec
> validation error. The export is `weft.client.normalize_taskspec_payload(...)`
> applied to the generated template; the package applies no overrides of
> its own.

### Spec-changing slice order

1. Plan (this document) → 2. independent review of plan and delta (§8) →
3. spec-promotion slice (task 2; strategies A/D as tabled; backlinks;
promotion identifier recorded) → 4. code slices (tasks 3-7) against the
promoted text → 5. deviation handling if reality disagrees → 6.
traceability reconciliation (task 9) → 7. release coupling (task 8,
owner-cut).

## 5. Tasks

1. **Independent review before promotion** (§8). Re-enter on any
   revision that changes the delta, the seam shape, or the register.
   Revision 1 re-entered here; revision 2 is a non-material update and
   asks for a scoped round-3 verification of the round-2 dispositions
   (see `## Revision Log`).

2. **Spec-promotion slice.** Files: spec 14 and 13C only (the README
   edit lands in task 6). Apply the exact texts above. Add to 14
   `## Related Plans` :246 and 13C `## Backlinks` :1326 a line for this
   plan
   (`- [Django override normalization seam plan](../plans/2026-09-08-django-override-normalization-seam-plan.md)`).
   Do **not** add an `Implementation:` note or any "implemented by" claim
   to spec 14 in this slice (strategy A). Verify:
   `./.venv/bin/python -m pytest tests/specs -q`. Record the promotion
   baseline identifier in §Spec Baseline. Done when the spec text matches
   the delta verbatim and `tests/specs` is green.

3. **Red: core seam tests** (`tests/core/test_client.py`,
   `tests/architecture/test_import_boundaries.py`). Every test in this
   task must fail at the promoted baseline for the stated reason; run
   them and confirm before task 4.
   - `tests/architecture/test_import_boundaries.py:949-1000`: add
     `"normalize_taskspec_payload"` to the `weft.client` expected set.
     **Red**: `set(module.__all__) == expected` fails (the exact-inventory
     test is the exactness guard for the seam; no presence-only parity
     entry is added).
   - Module-level helper in the test file: `_declared_taskspec()` — a
     template mapping that declares **every** overridable field with a
     non-default value (`name="declared"`, `metadata` with `description`,
     `tags`, and one custom key, `spec.timeout=30.0`, `working_dir` set,
     `stream_output=True`, `env={"DECLARED": "1"}`,
     `limits={"memory_mb": 256, "cpu_percent": 50}`,
     `runner={"name": "host", "options": {"declared": True}}`; function
     target `os:getcwd`). Default-valued templates cannot prove
     `None`-retention.
   - `test_normalize_taskspec_payload_ignores_none_for_every_override`,
     parametrized over `sorted(SUBMIT_OVERRIDE_NAMES)` (import from
     `weft._constants`): `normalize_taskspec_payload(_declared_taskspec(), **{name: None}) == normalize_taskspec_payload(_declared_taskspec())`
     **and** the declared value is still present at its path (table the
     path per name in the test). **Red**: `ImportError` on
     `from weft.client import normalize_taskspec_payload`.
   - `test_normalize_taskspec_payload_applies_every_override`: one cell per
     name with a non-`None` value (`name="renamed"`, `description="d"`,
     `tags=("a","b")`, `env={"K":"v"}`, `working_dir=str(tmp_path)`,
     `stream_output=False`, `timeout=5.0`, `memory_mb=512`,
     `cpu_percent=25`, `runner="host"`, `runner_options={"x": 1}`,
     `metadata={"k": "v"}`); assert the exact resulting value at the
     tabled path and that merges keep the declared entries
     (`env` has `DECLARED` and `K`; `runner.options` has `declared` and
     `x`). **Red**: `ImportError`.
   - `test_normalize_taskspec_payload_is_pure` (`monkeypatch`, `tmp_path`):
     delete every `WEFT*` environment variable (`monkeypatch.delenv`),
     `monkeypatch.chdir(tmp_path)`, `tmp_path.chmod(0o500)` (restore
     `0o700` in a `finally`); trap the three things the contract
     forbids by monkeypatching the **use-site bindings** — `weft.client._client.build_context`
     (`_client.py:16` imports the name, so patching `weft.context.build_context`
     would not intercept it), `weft.context.load_config` (`context.py:70-77`
     imports it by name), and `weft.context.open_broker` (`build_context`
     opens the broker through `open_broker` via `_ensure_database`, `context.py:304` → `:453-466`, not
     through `simplebroker.Queue.__init__`) — with functions that raise
     `AssertionError("forbidden on the export path")` (do **not** trap `Path.resolve`: bundle-rooted `TaskSpec`
     inputs legitimately re-resolve their root, model.py:1394-1404, and
     the plain-mapping input here makes no such call — §1 probe); call the
     function on `_declared_taskspec()` with `timeout=None,
     name="renamed"`, then assert `list(tmp_path.iterdir()) == []`, that
     no `.weft` directory exists in `tmp_path` or any parent up to the
     filesystem root that did not exist before the call (snapshot the
     parent chain first), and that the result has `name == "renamed"` and
     `spec.timeout == 30.0`. Skip on Windows (`chmod` semantics). **Red**:
     `ImportError`.
   - `test_normalize_taskspec_payload_is_a_fresh_copy`: call twice;
     `a == b and a is not b`; mutate `a["spec"]["timeout"]`; third call
     unchanged; `json.dumps(a)` succeeds; `a["tid"] is None`. **Red**:
     `ImportError`.
   - `test_normalize_taskspec_payload_omits_top_level_bundle_root`
     (`tmp_path`): build a `TaskSpec` with provenance via
     `validate_taskspec_payload(_declared_taskspec(), bundle_root=tmp_path, template=True)`
     (import from `weft.core.taskspec.transport` in the test only; the
     `weft_django`-style function target needs no bundle file), assert
     `taskspec.get_bundle_root() is not None`, then
     `TASKSPEC_BUNDLE_ROOT_FIELD not in normalize_taskspec_payload(taskspec)`
     (constant from `weft._constants`, test only). Add a second cell whose
     template carries `{"metadata": {"nested": {TASKSPEC_BUNDLE_ROOT_FIELD: "x"}}}`
     and assert the nested key **survives** (the guarantee is top-level
     only). **Red**: `ImportError`.
   - `test_normalize_taskspec_payload_raises_like_prepare`:
     `unknown_override=True` → `pytest.raises(TypeError, match="Unknown submit override")`;
     `memory_mb=0` → `pytest.raises(ValueError)` (pydantic
     `ValidationError`); `name="_weft.x"` →
     `pytest.raises(ValueError, match="reserved")`; `wait=True` →
     `TypeError`; `payload={"x": 1}` →
     `pytest.raises(TypeError, match="Unknown submit override\\(s\\): payload")`
     (the mapping-parameter leaf makes `payload` an ordinary unknown name;
     a `**overrides` leaf would capture it silently — §1). **Red**:
     `ImportError`.
   - `test_normalize_taskspec_payload_matches_submitted_definition`:
     `WeftTestHarness` + `harness.ensure_foreground_manager()` (pattern
     :454-470); `exported = normalize_taskspec_payload(_function_taskspec(harness.root), name="renamed", timeout=7.5, metadata={"k": "v"})`;
     `task = client.submit(exported)`; wait for the result; assert the
     completed snapshot's `name == "renamed"` and `metadata["k"] == "v"`
     (`client.tasks.snapshot(task.tid)`), and that
     `client.submit(_function_taskspec(harness.root), name="renamed", timeout=7.5, metadata={"k": "v"})`
     produces a snapshot with the same `name`/`metadata["k"]` — the
     "what `submit()` would write" proof through a real broker and
     manager. **Red**: `ImportError`.
   Do not mock `submission.*`, `TaskSpec` validation, or the broker. Done
   when every test above fails for its stated reason and the rest of the
   two files is green.

3b. **Green: characterization tests for the [PY-3] error-type sentence**
   (`tests/core/test_client.py`). These pin existing behavior and are
   expected **green at the promoted baseline**; they are listed
   separately from task 3 so the red gate is honest.
   - `test_client_prepare_and_submit_raise_raw_type_error_for_unknown_override`:
     `client.prepare(spec, unknown_override=True)` and
     `client.submit(spec, unknown_override=True)` → `TypeError` (exact
     type, not `SubmissionValidationError`).
   - `test_client_prepare_pipeline_and_submit_command_raise_raw_type_error_for_unknown_override`:
     reuse the existing pipeline-reference fixture pattern in the file;
     `client.prepare_pipeline(ref, unknown_override=True)`,
     `client.submit_pipeline(ref, unknown_override=True)`
     (`_client.py:124-132`), and
     `client.submit_command(["true"], unknown_override=True)`
     (`_client.py:148`) → `TypeError`.
   - `test_client_prepare_spec_translates_invalid_override_value`: stored
     spec (pattern :525-544) with `memory_mb=0` →
     `SubmissionValidationError` (translation at :511-516); the existing
     :525-544 already pins the unknown-name translation. Also
     `client.submit_spec(ref, memory_mb=0)` → `SubmissionValidationError`.
   Done when all are green before and after task 4.

4. **Core seam — the linking slice** (`weft/commands/submission.py`,
   `weft/client/_client.py`, `weft/client/__init__.py`; spec 14 note).
   - `submission.py`: add directly above `prepare` (:417):

     ```python
     def prepare_definition(
         taskspec: TaskSpec | Mapping[str, Any],
         overrides: Mapping[str, Any],
         *,
         payload: Any = None,
     ) -> PreparedSubmissionRequest:
         """Validate overrides, normalize, and snapshot a submission without a context.

         Overrides travel as one mapping so every public-seam keyword,
         including ``payload``, is checked against the override vocabulary.
         Constructs no ``WeftContext``, reads no configuration, opens no
         broker, and writes nothing; ``prepare`` delegates here.

         Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
         """

         _validate_submit_overrides(dict(overrides))
         updated = apply_submit_overrides(normalize_taskspec(taskspec), **overrides)
         return prepare_taskspec(updated, payload=payload)
     ```

     and replace the body of `prepare` (:426-428) with
     `return prepare_definition(taskspec, overrides, payload=payload)`
     (`prepare` keeps its `**overrides: Any` signature and passes the dict
     through). The `context` parameter stays for signature stability
     (`submit` :431-442 and `WeftClient.prepare` pass it). Check
     `_validate_submit_overrides` :166-170 accepts a `dict` built from the
     mapping unchanged (it iterates keys; verified).
   - `weft/client/_client.py`: add after `connect` (:169-182):

     ```python
     def normalize_taskspec_payload(taskspec: Any, **overrides: Any) -> dict[str, Any]:
         """Return the validated, normalized TaskSpec payload for a submission.

         Applies the public submit overrides exactly as `WeftClient.prepare`
         does and returns the pre-transport snapshot as a fresh JSON-compatible
         mapping. Every keyword is an override name (`payload` is rejected).
         Needs no client or context: builds no context, reads no
         configuration, opens no broker, writes nothing.

         Spec: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]
         """
         request = submission.prepare_definition(taskspec, overrides)
         payload: dict[str, Any] = request.taskspec.model_dump(mode="json")
         return payload
     ```

   - `weft/client/__init__.py`: import it at :28
     (`from ._client import WeftClient, connect, normalize_taskspec_payload`)
     and add `"normalize_taskspec_payload"` to `__all__` (:39-63, keep the
     list sorted as it is today: after `"connect"`).
   - Update the `_client.py` module docstring spec references to include
     14 [PY-1], [PY-3]. In the same change append to 14 [PY-3] (after the
     promoted paragraph): "Implementation:
     `weft/client/_client.py::normalize_taskspec_payload` over
     `weft/commands/submission.py::prepare_definition`." Both functions
     carry the `Spec:` backlink ([PY-3] on `prepare_definition`; [PY-1],
     [PY-3] on the client function) because the note maps both. Also add
     `14-Python_API_Surfaces.md [PY-3]` to the `submission.py` module
     docstring's spec references. The Django caller is **not** named here
     (it does not exist yet; task 6 does not add it either — spec 14 maps
     core implementation only).
   Reuse: everything — no new validation, snapshot, or merge code.
   Forbidden: importing `weft.core` into `weft/client/`; re-validating or
   re-snapshotting in the client function; adding a
   `WeftClient.normalize`/`export` verb or a `PreparedSubmission`
   accessor; exporting `prepare_definition` from `weft.commands`; any
   second copy of the validate → apply → snapshot sequence. Stop if the
   client function wants anything from `submission.*` beyond
   `prepare_definition`. Verify:
   `./.venv/bin/python -m pytest tests/core/test_client.py tests/architecture tests/commands -q`;
   task-3 tests green, task-3b tests still green. Done when green and
   mypy is clean on `weft`.

5. **Red: Django cross-surface matrix**
   (`integrations/weft_django/tests/test_weft_django.py`; fixture
   `testapp/weft_tasks.py`). First add a fixture task with every
   decorator field declared so `None`-retention is observable:

   ```python
   from django.conf import settings


   @weft_task(
       name="testapp.declared_task",
       description="declared",
       timeout=30.0,
       memory_mb=256,
       cpu_percent=50,
       stream_output=True,
       runner_options={"declared": True},
       working_dir=str(settings.BASE_DIR),
       env={"DECLARED": "1"},
       metadata={"declared_key": "declared", "tags": ["declared"]},
   )
   def declared_task(value: str) -> str:
       return value
   ```

   (`settings.BASE_DIR` is `fixture_project/fixture_project/settings.py:6-7`;
   the fixture module cannot import the test module's `TEST_ROOT` :23.
   `weft_task` has no `tags=` parameter (decorators.py:150-162), so the
   non-default `tags` the `None` matrix needs must arrive through
   `metadata` — the builder copies `metadata` into the template's
   `metadata` section, where `tags` is the list `apply_submit_overrides`
   :114-115 reads. No test pins the registry size — verified.) Then add,
   marked `@pytest.mark.shared` where they touch the fixture context:
   - `test_as_taskspec_for_call_explicit_none_keeps_declared_values`,
     parametrized over `sorted(SUBMIT_OVERRIDE_NAMES)`: for
     `declared_task.as_taskspec_for_call("v", _overrides={name: None})`
     assert equality with `declared_task.as_taskspec_for_call("v")` and
     that the declared value is present at its tabled path. **Red today**
     for `timeout`, `working_dir`, `stream_output` — each by inequality
     (`stream_output=None` does not raise today; it returns a payload
     with `spec.stream_output == None` that fails `TaskSpec` validation —
     round-2 probe `08-rev2/probe_django_today.py`; see §1).
   - `test_as_taskspec_for_call_rejects_unknown_and_wait`: `unknown_flag=1`
     → `pytest.raises(TypeError, match="Unknown submit override")`;
     `wait=True` → same; `payload={"x": 1}` → same (`payload` is a
     `prepare` keyword, not an override; the mapping-parameter leaf keeps
     it out of the export vocabulary). **Red today** (silently dropped).
   - `test_as_taskspec_for_call_rejects_invalid_values`: `memory_mb=0` →
     `pytest.raises(ValueError)`; `runner="docker"` →
     `pytest.raises(ValueError, match="runner='host'")`; `name=""` →
     `pytest.raises(ValueError)`; `name="_weft.x"` →
     `pytest.raises(ValueError, match="reserved")`. **Red today** for the
     first, third, and fourth.
   - `test_as_taskspec_for_call_applies_every_override_like_prepare`: one
     parametrized cell per name with a non-`None` value (`name="renamed"`,
     `description="d"`, `tags=("a","b")`, `env={"K":"v"}`,
     `working_dir=str(TEST_ROOT)`, `stream_output=False`, `timeout=5.0`,
     `memory_mb=512`, `cpu_percent=25`, `runner="host"`,
     `runner_options={"x": 1}`, `metadata={"k": "v"}`) against
     `declared_task`; assert the exact field in the exported payload
     (table the expected paths in the test: `["name"]`,
     `["metadata"]["description"]`, `["metadata"]["tags"] == ["a","b"]`,
     `["spec"]["env"]` contains both `DECLARED` and `K`,
     `["spec"]["working_dir"]`, `["spec"]["stream_output"]`,
     `["spec"]["timeout"]`, `["spec"]["limits"]["memory_mb"]`,
     `["spec"]["limits"]["cpu_percent"]`, `["spec"]["runner"]["name"]`,
     `["spec"]["runner"]["options"]` contains both `declared` and `x`,
     `["metadata"]["k"]`), and that
     `validate_taskspec_payload(exported, template=True)` succeeds and
     `exported["spec"]["args"] == [{"payload": envelope}]` with
     `envelope["call"]["args"] == ["v"]`. **Red today**: `exported["tid"]`
     is absent (compact dict) — assert `exported["tid"] is None` in every
     cell so the shape change is pinned. Keep the existing :506-520 test
     as-is (it is the description/tags/memory/cpu cell on `echo_task`).
   - `test_as_taskspec_for_call_is_pure` (`monkeypatch`, `tmp_path`).
     The suite's default settings make a regression invisible: `CONTEXT`
     is `TEST_ROOT` (`_fixture_weft_settings` :96; env :36) and the
     module bootstraps `TEST_ROOT/.weft` at import (:80-85), so a path
     that re-grew `get_core_client()`/`build_context()` would reuse that
     writable context and leave every snapshot unchanged. Therefore:
     run under `override_settings(WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None), BASE_DIR=tmp_path)`;
     `monkeypatch.delenv` every key in
     `weft_django.conf.CORE_CONTEXT_OVERRIDE_ENV_KEYS` (`conf.py:18`)
     and `WEFT_CONTEXT` (`raising=False`) so `resolve_context_override()`
     falls through to `BASE_DIR` (`conf.py:94-105`); assert the actual
     resolution target first — `resolve_context_override() == str(tmp_path)`
     — so the test proves where a regression *would* build;
     `monkeypatch.chdir(tmp_path)`, `tmp_path.chmod(0o500)` (restore in
     `finally`); monkeypatch the same three use-site bindings as the core
     test (`weft.client._client.build_context`, `weft.context.load_config`,
     `weft.context.open_broker`) plus `weft_django.client.get_core_client`
     to raise `AssertionError` (these are the permitted tripwires in §6); export
     `echo_task.as_taskspec_for_call("v", _overrides={"timeout": None})`;
     assert `list(tmp_path.iterdir()) == []`, `not (tmp_path / ".weft").exists()`,
     and that the `TEST_ROOT/.weft` directory listing and the broker
     database at `_bootstrap_context.database_path` (default
     `.weft/broker.db`, not `weft.db`; see `tests/system/test_constants.py:514`)
     have unchanged size/mtime (snapshot before). **Green today
     and after** — it is the diminution guard for register row 10 and
     must stay in the suite.
   - `test_as_taskspec_for_call_export_runs_like_enqueue`: one end-to-end
     cell (`{"name": "renamed", "timeout": 5.0, "metadata": {"k": "v"}}`):
     submit the exported dict through the native helper
     `submit_taskspec(exported)` and run `echo_task.enqueue("v", _overrides=...)`;
     both results `status == "completed"`, equal `value`, and equal
     `snapshot.metadata["k"]`/`snapshot.name`. Real manager via the
     fixture context (the file already runs broker-backed tests).
     **Green today and after** (the compact dict already validates as a
     template and these three overrides are applied by the copy); it is
     the submittability guard for register row 8, not a red gate.
   Do not mock `WeftClient`, `normalize_taskspec_payload`, or TaskSpec
   validation. Done when the cells marked red fail for the stated reasons
   and the existing suite is otherwise green.

6. **Django rewiring** (`integrations/weft_django/weft_django/client.py`,
   `decorators.py`, `integrations/weft_django/README.md`). Wrapper logic
   over the task-4 core seam.
   - Delete `_apply_taskspec_payload_overrides` :223-288 and the `json`
     import at :5 if it becomes unused.
   - Add `normalize_taskspec_payload` to the `from weft.client import (...)`
     block :12-21.
   - `build_registered_task_taskspec(task, *, envelope, embed_envelope)`:
     drop the `overrides` parameter; return `spec_payload` directly
     (replace :375-379 with `return spec_payload`). The JSON-copy
     isolation it provided is owned by core: `prepare` → `_snapshot_taskspec`
     :184-191 and `_snapshot_payload` :173-181 run on every submit and
     deferred path before `transaction.on_commit` (:430-440). Update the
     two callers :392-397 and :423-428 (remove `overrides=None`).
   - Add, next to `submit_registered_task_on_commit`:

     ```python
     def export_registered_task_taskspec(
         task: Any,
         *,
         args: tuple[Any, ...],
         kwargs: dict[str, Any],
         overrides: Mapping[str, Any] | None = None,
     ) -> dict[str, Any]:
         """Validated TaskSpec payload for one decorated-task call.

         Applies the core submit-override contract through
         `weft.client.normalize_taskspec_payload`; builds no Weft context,
         reads no Weft configuration, opens no broker, writes nothing
         (README "Composition Export").
         """
         _validate_decorated_task_overrides(overrides)
         envelope = task.build_envelope(*args, **kwargs)
         base_payload = build_registered_task_taskspec(
             task, envelope=envelope, embed_envelope=True
         )
         return normalize_taskspec_payload(base_payload, **dict(overrides or {}))
     ```

     Pass overrides through **unchanged** (not `_submit_kwargs`): `wait`
     and `payload` must reach core and raise. `_validate_decorated_task_overrides` runs
     first so `runner="docker"` fails with the Django message. No
     `Spec:` line: spec 14 maps core code only, and shipped code does not
     cite `[DJ-*]`.
   - `decorators.py` `as_taskspec_for_call` :103-117: delegate to
     `export_registered_task_taskspec(self, args=args, kwargs=kwargs, overrides=_overrides)`
     with the same lazy import pattern; return type `dict[str, Any]`.
   - `README.md`: insert the "Composition Export" section (exact text in
     the delta) after :91.
   - Delete the two tests at :149-158 and :161-169.
   Reuse: `_validate_decorated_task_overrides`,
   `build_registered_task_taskspec`, the core seam. Forbidden: any
   Django-side merge of override values; `get_core_client()` or any
   `WeftClient` on the export path; a Django `ValueError` for `wait`
   (one rule: every `_overrides` key is a core override name); catching
   and re-typing core errors; keeping a JSON copy in the builder "just in
   case". Stop if the export needs to post-process the normalized payload
   — that is a second normalizer. Verify:
   `./.venv/bin/python -m pytest integrations/weft_django/tests -q -n 0`;
   `./.venv/bin/mypy … integrations/weft_django/weft_django …` (full
   command in §7); `./.venv/bin/ruff check .` (expect one *unused
   suppression*/`C901` inventory mismatch to be resolved in task 7, not
   here — if `ruff check` fails only on the registry, proceed). Done when
   task-5 tests are green, `test_as_taskspec_for_call_is_pure` is still
   green, and no test references the deleted function.

7. **Registry retirement and release note.**
   - `docs/ruff-suppression-registry.md`: delete the `RUFF-SUP-209` row
     (:87); change `C901=130` → `C901=129` on the global inventory line
     (:227); run `./.venv/bin/python bin/ruff_suppression_index.py --write`
     (removes the :302 index row; touch nothing else outside the
     markers) then `--check`. Verify
     `./.venv/bin/python -m pytest tests/specs/test_ruff_policy.py tests/specs/test_ruff_suppression_index.py -q`.
   - `CHANGELOG.md` under `## Unreleased`: add `### Added` entry
     "`weft.client.normalize_taskspec_payload(taskspec, **overrides)`
     returns the validated, normalized TaskSpec payload a submission would
     use, as a fresh JSON-compatible dict, without a client or context —
     it reads no configuration, opens no broker, and writes nothing — so
     embedders can export the exact definition without a second
     normalizer." and a `### Changed` entry "`weft-django`:
     `RegisteredWeftTask.as_taskspec_for_call(..., _overrides=...)` now
     applies the core submit-override contract through
     `weft.client.normalize_taskspec_payload(...)`: explicit `None`
     overrides are ignored instead of clearing declared values, unknown
     names (including `wait`) raise `TypeError`, invalid values raise the
     TaskSpec validation error, reserved `_weft.` names raise
     `ValueError`, and the returned dict is the full validated TaskSpec
     payload (`tid: None`, `io`, `state`, defaulted `spec` fields) rather
     than the compact builder dict. The export still builds no Weft
     context, reads no Weft configuration, opens no broker, and writes
     nothing. The private override copy was removed; `weft-django 0.9.34`
     requires `weft>=0.9.100`."
   Done when both gates above pass.

8. **Release coupling (owner-cut, `Release 0.9.100` commit; pattern
   `85196bc2`).** Core first: root `pyproject.toml:7` `version = "0.9.100"`
   **and** `weft/_constants.py:47` `__version__: Final[str] = "0.9.100"`
   (the public/CLI version; `tests/system/test_constants.py:238`
   `test_version` fails on any mismatch with `pyproject.toml`);
   `integrations/weft_django/pyproject.toml:7` `version = "0.9.34"` and
   `:17` `"weft>=0.9.100"`; root extras `pyproject.toml:49`
   `weft-django>=0.9.34` and `:52` `weft-django[channels]>=0.9.34` (and
   the `all` extra's `weft-django` line, as `85196bc2` did); `uv lock`
   then `uv lock --check`; `CHANGELOG.md` `## Unreleased` →
   `## [0.9.100] - YYYY-MM-DD` with the release date (the dated form every
   heading uses, `CHANGELOG.md:43`). Publish weft `0.9.100` before
   `weft-django 0.9.34`. These edits must not land before task 6 (the
   floor would claim a seam the tree does not have) and cannot land
   separately from the root bump (`uv` resolves the editable workspace
   `weft` at its declared version). Verify, **after** these edits (the §7
   final gates run before task 8 do not cover them):
   `./.venv/bin/python -m pytest tests/system/test_constants.py -q`;
   `uv lock --check` green; `grep -n 'weft>=' integrations/weft_django/pyproject.toml`
   shows `0.9.100`; `grep -n '__version__' weft/_constants.py` shows
   `0.9.100`; then re-run the release-gate suite as CI does
   (`.github/workflows/release-gate.yml`: the §7 command list including
   `pytest -q -n 0 integrations/weft_django/tests`). Done when the
   release commit exists (`git log`) and those gates are green on it.

9. **Traceability reconciliation.** Grep gates (scoped to `weft/`,
   `integrations/`, `tests/`, `docs/specifications/`,
   `docs/ruff-suppression-registry.md`): zero hits for
   `_apply_taskspec_payload_overrides` and `RUFF-SUP-209`; zero hits for
   `get_core_client` inside `export_registered_task_taskspec`; the two
   task-4 code backlinks — `_client.py::normalize_taskspec_payload`
   (`Spec: … [PY-1], [PY-3]`) and `submission.py::prepare_definition`
   (`Spec: … [PY-3]`) — and the [PY-3] `Implementation:` note reference
   each other (grep `14-Python_API_Surfaces.md \[PY-3\]` in both files;
   grep both function paths in spec 14); `weft/client/` imports no
   `weft.core` module. Confirm 13C still reads "does not change current
   core Weft behavior on its own" (:6). Close the deviation log. Rerun §7
   final gates from the current state and record results here.

## 6. Testing Plan

Harnesses: `WeftTestHarness` with `ensure_foreground_manager()` for the
core end-to-end test; the Django fixture project (`django.setup()` at
import, real broker at `WEFT_DJANGO_FIXTURE_WEFT_CONTEXT`) for the
matrix. Real everything: `normalize_taskspec_payload`,
`submission.prepare_definition`, `apply_submit_overrides`, `TaskSpec`
validation, spawn queue, manager, consumer. Nothing in this change is
external, slow, or nondeterministic enough to mock; a monkeypatch of
`json`, `submission.*`, or `weft.client.*` is a stop-and-re-evaluate
signal. The only permitted `monkeypatch` uses are `delenv`/`chdir` in the
purity tests and, in those same tests, the tripwires that turn a
regression into a failure: the use-site bindings
`weft.client._client.build_context`, `weft.context.load_config`, and
`weft.context.open_broker` (plus `weft_django.client.get_core_client` in
the Django test) replaced with functions that raise. Patching the
defining modules (`weft.context.build_context`,
`weft._constants.load_config`) would not intercept the by-name imports at
`_client.py:16` and `context.py:70-77`, and the broker is opened through
`open_broker` (`build_context` → `_ensure_database`, `context.py:304` → `:453-466`), not `Queue.__init__`. `Path.resolve` is deliberately not
trapped (bundle-rooted `TaskSpec` inputs re-resolve their root read-only;
§1 probe).

Red-green: task 3 names the failing tests and the reason each fails at
the promoted baseline; task 3b is explicitly green characterization;
task 5 marks each cell red or green. Enumerable contract elements with a
firing test: every `SUBMIT_OVERRIDE_NAMES` entry (`None` cell and value
cell, both surfaces, on fully-declared templates); unknown name; `wait`;
`payload` (both surfaces); invalid value (`memory_mb=0`); reserved
`_weft.` name; empty `name`;
non-host runner; the `weft.client.__all__` inventory (exact set); the
absent top-level transport key and the surviving nested key; fresh-copy
semantics; the no-context/no-config/no-broker/no-write contract under a
read-only cwd with no `WEFT*` environment and tripwires (core), and with
no configured `CONTEXT`, a read-only `BASE_DIR`, cleared core context
override variables, and a verified resolution target (Django); the
[PY-3] error-type sentence per surface (`prepare`,
`submit` → raw `TypeError`/`ValueError`; `prepare_spec`, `submit_spec` →
`SubmissionValidationError` for unknown name (existing :525-544) and
invalid value (new); `prepare_pipeline`, `submit_pipeline`,
`submit_command` → `TypeError`); the end-to-end
"exported definition is what `submit` writes" proof.

Tempting to skip, in scope: the purity tests (they are the only guard
against the class-D regression review round 1 caught) and the one
end-to-end "export runs like enqueue" cell (the only proof the exported
payload is *submittable*, not merely valid). Out of scope: pipeline
composition of an exported payload into a stored pipeline spec —
[DJ-6.3] only requires the payload be an ordinary validated TaskSpec,
which the `template=True` validation cell proves.

Observable success beyond tests: after landing, `python -c` against the
fixture project shows `echo_task.as_taskspec_for_call("x", _overrides={"timeout": None})["spec"]["timeout"] == 30.0`
and `_overrides={"nope": 1}` raises, from a read-only working directory
with no `.weft` created; `ruff_suppression_index.py --check` reports 129
`C901` directives.

## 7. Verification and Gates

Per task (named in each task). Final gates, run from the current state
before claiming completion:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_client.py tests/architecture tests/commands tests/specs -q
./.venv/bin/python -m pytest integrations/weft_django/tests -q -n 0
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft tests integrations/weft_django extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
```

There is no in-repo backstitch runner at 178e3a34 (the name appears in
agent-context prose, `docs/lessons.md:110-112`, and `bin/coalesce-check`,
which checks `docs/coalescing.md` SHA cues, not spec mappings);
`tests/specs` and `tests/architecture` plus the task-9 grep gates are the
traceability gate. Rollback: revert the commit; nothing persisted
changes. Rollout: one code landing in this repo (tasks 2-7), then the
owner's release commit (task 8) tags weft `0.9.100` and `weft-django
0.9.34` together, core published first.

## 8. Independent Review Loop

External review is run by the top-level agent using a different agent
family (Codex CLI on this machine) plus a Claude reviewer, with the
Planning Review Prompt from
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, before
promotion (task 1) and again on the completed work. Round 1 is recorded
below; revision 1 re-enters round 2. Reviewers read: this plan including
`## Proposed Spec Delta`, the register, and `## Review Record`; spec 14
[PY-1] :7-28, [PY-3] :210-232, [PY-4] :234-244; 07 OBS.9 :320-321, CTX.5
:896-901; 13C header :1-22, [DJ-2.1] :196-238, [DJ-2.2] :255-290,
[DJ-6.3] :536-552, [DJ-8.1] :624-648, [DJ-8.4] :720-775, [DJ-19]
:1312-1321; `docs/specifications/README.md:30-40`;
`weft/commands/submission.py:73-191, :323-339, :417-442, :446-575, :602-611, :685-697`;
`weft/client/_client.py`, `_prepared.py`, `__init__.py`;
`weft/commands/types.py:93-104`; `weft/context.py:223-320, :469-478`;
`weft/core/taskspec/transport.py:18-73`; `weft/core/taskspec/model.py:298-312, :940-975, :1394-1404`;
`weft/_constants.py:47`; `tests/system/test_constants.py:238-256`;
`integrations/weft_django/weft_django/conf.py:18-26, :86-105, :146-148`;
`integrations/weft_django/tests/test_weft_django.py:30-40, :80-85, :95-115`;
`integrations/weft_django/weft_django/client.py:1-30, :149-160, :215-441`;
`decorators.py:31-37, :85-147`; `weft_django/conf.py:94-105`; the five
Django tests named in §3; `tests/core/test_client.py:64, :142-246, :454-470, :525-544`;
`tests/architecture/test_import_boundaries.py:945-1010`;
`docs/ruff-suppression-registry.md:87, :227, :302`;
`integrations/weft_django/pyproject.toml`, root `pyproject.toml:7, :45-55`,
`uv.lock:1730-1745`, `CHANGELOG.md:1-45`; `git show 85196bc2 --stat`.
Stance: (1) is there any input for which `normalize_taskspec_payload`
differs from the TaskSpec `prepare(...)` holds for the same inputs; (2)
does any path from `as_taskspec_for_call` reach `build_context`,
`load_config`, or a broker after task 6; (3) is the [PY-3] sentence
exactly true per surface; (4) is moving `prepare`'s body into
`prepare_definition` a move or a second path; (5) is the release
coupling in task 8 complete and correctly ordered; (6) performative
ceremony to remove; (7, round 3) is every round-2 disposition below
applied faithfully and is the narrowed no-context/no-config/no-broker/
no-write wording exactly true on both surfaces. Feedback is handed back
as findings with dispositions recorded in `## Review Record`
(append-only).

## 9. Out of Scope

Translating `WeftClient.prepare`/`prepare_pipeline`/`submit_command`
raw `TypeError`/`ValidationError` into typed `SubmissionValidationError`
(would change `enqueue`; a separate [PY-3] decision). Adding
`as_taskspec_for_call` to the module-level `weft_django` API. A Django
pipeline DSL ([DJ-6.3] non-goal). Documenting `Task` or
`PreparedSubmission` members in spec 14 (including whether
`PreparedSubmission.client` is public). Any change to `_submit_kwargs` on
the submit paths. A pipeline-reference form of the export. Retiring other
registry rows. Editing the 13C prose status. Cutting the release itself
(task 8 is owner-executed).

## 10. Fresh-Eyes Review

Author pass 2026-09-08 (revision 0), ordered by severity:

1. (High) The first draft's [PY-3] sentence said unknown names raise
   `TypeError` "from `prepare(...)`" without checking the other
   non-translating surfaces; verified `prepare_pipeline` :611 and
   `submit_command` :693 also raise raw `TypeError` and `prepare_spec`
   alone translates — sentence rewritten per surface and a firing test
   added.
2. (High) The draft passed `_overrides` through `_submit_kwargs`, which
   would have silently dropped `wait` on export — the exact class of
   behavior the owner rejected. Task 6 now passes overrides unchanged
   and the matrix pins `wait` → `TypeError`; the inconsistency with
   `enqueue_on_commit`'s `ValueError` for `wait=True` is raised as an
   owner question rather than decided silently.
3. (Medium) The draft omitted that routing the export through
   `WeftClient` calls `build_context`. Recorded as C — **overturned in
   review round 1 (Codex #1): it is a class-D loss; the seam was redesigned
   to be context-free.**
4. (Medium) The return-shape change (compact dict → full normalized dump)
   was unstated; probed that the dump validates as a template and
   re-normalizes identically; recorded as C with the [DJ-6.3] consumer
   argument.
5. (Medium) RUFF-SUP-209's second "real proof" test reaches only the
   copy branch; noted so the retirement is not read as losing coverage.
   [Corrected in revision 2 (Codex R2-7): the row's second proof is the
   *native* deferred test, which never reaches the private function; the
   conclusion — no proof is lost — stands. See §3.]
6. (Low) `runner=""` and `name=""` change from silent no-op to
   validation error on export; verified `enqueue` already rejects both
   (`min_length=1`).
7. (Low) "compare export vs prepared definitions" could be implemented
   circularly once export *is* the core path; the matrix asserts explicit
   expected field values and one end-to-end submit instead.

Author pass 2026-09-08 (revision 1), after applying round-1 dispositions:

1. (High) Checked that `prepare_definition` is a move, not a copy:
   `prepare`'s three statements (:426-428) become the new function's
   body verbatim and `prepare` keeps only the delegate line; `submit`
   :431-442 is unaffected. Grepped for other callers of the three-step
   sequence — `prepare_spec` :511 applies overrides after reference
   resolution and is a different pipeline by design (out of scope).
2. (Medium) The revision-0 end-to-end Django cell was labelled red but is
   green today (the compact dict already submits); relabelled as a guard,
   and the red cells in task 5 were re-derived from the §1 table.
3. (Medium) The purity test must not merely check `tmp_path`: with no
   `CONTEXT`, discovery would create `.weft/` beside the discovered root,
   not the cwd. The test snapshots the parent chain and `settings.BASE_DIR`.
4. (Low) `uv lock` feasibility drove task 8's coupling of the floor to
   the root bump; the alternative (bump root version in the code commit)
   is offered as an owner question rather than assumed.

## Observable-Difference Register

Classes (owner rule, Van 2026-09-08): **A** — owner-decided removal or
behavior change, recorded in `CHANGELOG.md`; **N** — no observable
difference; **C** — observable change with no capability loss (additive,
superset, or bug fix); **D** — diminution: observable capability lost;
must be fixed in-plan or escalated before promotion. Verified against
the shipping default configuration (fixture settings:
`DEFAULT_TASK.timeout=None`, `stream_output=False`; `echo_task` declares
`timeout=30.0`). `enqueue`/`enqueue_on_commit`/native helpers: no row
changes (N) — they never passed overrides to the deleted function.

| # | Surface / input | Before (178e3a34) | After | Class | Note |
|---|---|---|---|---|---|
| 1 | `as_taskspec_for_call` `timeout=None`, `working_dir=None` | declared value cleared to `None` | ignored; declared value stands | A | owner decision: core semantics win |
| 2 | `as_taskspec_for_call` unknown override name | silently dropped | `TypeError` | A | owner decision |
| 3 | `as_taskspec_for_call` `wait=<any>` | silently dropped | `TypeError("Unknown submit override(s): wait")` | A | see Open Owner Questions 1 |
| 4 | `as_taskspec_for_call` `stream_output=None` | returns an invalid payload (`bool_type`) | ignored; valid payload | C | bug fix |
| 5 | `as_taskspec_for_call` invalid value (e.g. `memory_mb=0`, `cpu_percent=101`) | returned unvalidated | `ValueError` (pydantic `ValidationError`) | A | core semantics win; `enqueue` already rejects (`timeout=-1` is **accepted** by the schema on both surfaces — not a row) |
| 6 | `as_taskspec_for_call` `runner=""` / `name=""` | silently skipped | `ValueError` (validation) | A | `enqueue` already rejects both |
| 7 | `as_taskspec_for_call` `runner="docker"` | `ValueError` (Django) | `ValueError` (Django, same message) | N | check kept before normalization |
| 8 | `as_taskspec_for_call` return shape | compact `{name, spec(11 keys), metadata}` | full normalized dump (`tid: None`, `version`, `description`, `state`, `io`, defaulted `spec` fields); validates as template; re-normalizes identically | C | superset; [DJ-8.1] "validated TaskSpec payload" now true |
| 9 | `as_taskspec_for_call` `name` starting with `_weft.` | written unvalidated | `ValueError` from `validate_endpoint_claim_name` (submission.py:149-157; OBS.9) | A | core semantics win; `enqueue` already rejects; decorated tasks are never persistent so the persistent-name branch is unreachable here |
| 10 | `as_taskspec_for_call` side effects | settings read + configured request-ID provider only; no Weft context, no configuration read, no broker, no writes | identical (`normalize_taskspec_payload` builds no context, reads no config, opens no broker, writes nothing; probe under `0o500` cwd with no `WEFT*` env created nothing; no cwd discovery; zero `Path.resolve` calls on the mapping input) | N | revision 0 had this as C via `build_context`; overturned as D and fixed by the seam redesign; pinned by two purity tests with tripwires (revision 2 hardened the Django one so a regression cannot hide behind the suite's writable `TEST_ROOT` context) |
| 11 | `as_taskspec_for_call` merges for `env`, `metadata`, `runner_options`, `description`, `tags`, `memory_mb`, `cpu_percent` | override-wins merge | identical | N | verified line-by-line against submission.py:112-147 and by probe |
| 12 | `weft.client.__all__` | 23 names | adds `normalize_taskspec_payload` (every keyword an override name; `payload=` → `TypeError`) | C | additive; [PY-1] sentence edited; `PreparedSubmission` unchanged (its `client` attribute stays as it is; no inventory claim made) |
| 13 | `submission.prepare(context, ...)` | own body | delegates to `prepare_definition`; same inputs, same outputs, `context` still unused | N | move, not copy |
| 14 | `build_registered_task_taskspec` JSON copy of the base payload | copied before return | returned as built; core snapshots on every path before queue/`on_commit` | N | snapshot-rule tests :396-420 remain the pin |
| 15 | Ruff `C901` inventory | 130 | 129 | N | tooling ledger |
| 16 | `weft-django` manifest and reported version | `0.9.33`, `weft>=0.9.95`; weft `__version__` `0.9.99` | `0.9.34`, `weft>=0.9.100`; root extras `>=0.9.34`; lock refreshed; `weft._constants.__version__` `0.9.100` with `pyproject.toml` | C | release coupling, task 8; monorepo rule 13C :1318-1319 |
| 17 | `normalize_taskspec_payload(taskspec)` on a bundle-rooted `TaskSpec` | n/a (new surface) | accepted; root re-resolved read-only by validation (model.py:1394-1404); provenance absent from the result | C | not reachable from Django (mappings only); scope of the public contract is Open Owner Question 4 |

No D rows after revision 1; revision 2 added row 17 and re-verified rows 10, 12, and 16 without a class change.

## Open Owner Questions

1. `wait` on `as_taskspec_for_call`. Recommended default (in plan):
   treat it like any non-core name — raw `TypeError("Unknown submit
   override(s): wait")` from core, no Django special case. Alternative:
   mirror [DJ-8.4]'s deferred rule (:749-759) with a local
   `ValueError("as_taskspec_for_call does not submit; wait is not supported")`
   before normalization. Either is one line in task 6; the default keeps
   exactly one override rule on the export.
2. Public name. Recommended default: `weft.client.normalize_taskspec_payload`
   (describes the result — a payload — and the operation; avoids
   colliding with the private `submission.normalize_taskspec`, which
   returns a `TaskSpec`). Alternative: `prepare_taskspec_payload`. The
   name appears in [PY-1], [PY-3], 13C, README, CHANGELOG, and one test
   file; changing it after promotion is a spec edit.
3. Release coupling. Recommended default (task 8): the floor
   `weft>=0.9.100`, `weft-django 0.9.34`, root extras, and `uv.lock` move
   in the owner's `Release 0.9.100` commit, matching `85196bc2`; the code
   landing leaves `integrations/weft_django/pyproject.toml` untouched.
   Alternative: bump the root version to `0.9.100` inside the code
   landing so the floor can lock immediately — deviates from the release
   pattern and pre-commits the next version number. Either way the
   `weft/_constants.py:47` `__version__` moves with `pyproject.toml`
   (task 8; `test_constants.py:238`).
4. Bundle-rooted inputs and the public export contract. A `TaskSpec`
   that already carries bundle provenance re-resolves its root read-only
   during validation (model.py:1394-1404), so
   `normalize_taskspec_payload` cannot promise literal zero filesystem
   reads for such inputs. Recommended default (in plan): the public
   contract is stated as "no context, no configuration read, no broker,
   no writes"; bundle-rooted `TaskSpec` inputs are accepted, their
   provenance is stripped from the result, and the read-only
   re-resolution is documented ([PY-3] text, register row 17). No Django
   input can carry a bundle root. Alternative: reject bundle-rooted
   inputs on the public seam with `ValueError` so the seam is
   filesystem-silent for every accepted input — one extra check in
   `normalize_taskspec_payload` and one more [PY-3] sentence, and it would
   make the seam stricter than `prepare(...)`.

Resolved with evidence in revision 1 (no owner input needed): export
side effect (seam is context-free); bundle-root provenance in the
*result* (pre-transport snapshot, top-level guarantee only; Django
templates never carry a bundle root); version floor (`v0.9.99` tagged →
`weft>=0.9.100`); home of the inventory ([PY-1] enumerates `__all__`
names exactly — the seam is one). Resolved in revision 2: `payload=`
on the seam (mapping-parameter leaf → `TypeError`, Claude R2-1).

## Revision Log

- Revision 3 (2026-09-08): round-3 verification fixes only — purity
  tripwires rebound to use-site symbols, broker database path taken from
  the bootstrap context, §1 wording aligned with the narrowed contract.
  Non-material; no seam, delta, task-order, or register-class change.

| Date | Change | Reason |
|------|--------|--------|
| 2026-09-08 | Revision 2 (non-material). No change to the seam's name, layering, return value, the register's classes, or the delta's substance. Applied: (a) `prepare_definition` takes `overrides: Mapping[str, Any]` as an explicit parameter instead of `**overrides`, so `payload=` on `normalize_taskspec_payload` is an unknown override (`TypeError`); cells added in tasks 3 and 5 (Claude R2-1). (b) Contract wording narrowed on [PY-3], [DJ-8.1], the README, §4, task-4/6 docstrings, the CHANGELOG entries, and register row 10 from "performs no I/O / no filesystem" to "constructs no Weft context, reads no configuration, opens no broker, writes nothing"; `TaskSpec.set_bundle_root`'s read-only `Path.resolve()` (model.py:1394-1404) documented and excluded from the Django path; purity tests trap `build_context`/`load_config`/broker construction, not `Path.resolve` (Codex R2-1, R2-6; Claude R2-3). (c) Task 8 updates `weft._constants.__version__` with `pyproject.toml`, uses the dated CHANGELOG heading, and re-runs `tests/system/test_constants.py` plus the release gates after the edits (Codex R2-2). (d) Django purity test rebuilt: no configured `CONTEXT`, `BASE_DIR` = read-only temp dir, core context override env cleared, resolution target asserted, `TEST_ROOT/.weft` snapshot (Codex R2-3). (e) `declared_task` fixture gains non-default `tags` via `metadata` (Codex R2-4). (f) `Spec:` [PY-3] backlink on `prepare_definition`; task 9 verifies both backlinks (Codex R2-5). (g) Evidence corrections: RUFF-SUP-209's second proof is the native deferred test; [DJ-19] anchors :1316/:1318-1319 (Codex R2-7); `stream_output=None` cell is red by inequality, not by a raise (Claude R2-2). Open Owner Question 4 added (bundle-rooted inputs). This revision **requests a scoped round-3 verification** of the dispositions (stance item 7 in §8), not a full re-review. | Review round 2: Codex FAIL (R2-1..R2-7), Claude PASS (R2-1..R2-3); every claim reproduced (`08-rev2/probe_r2.py`, `probe_django_today.py`); dispositions below. |
| 2026-09-08 | Revision 1. Seam changed from `PreparedSubmission.taskspec_payload()` (client-bound) to the context-free `weft.client.normalize_taskspec_payload(taskspec, **overrides)` backed by `submission.prepare_definition`; `weft.client.__all__` now changes ([PY-1] sentence edited); Django export no longer calls `get_core_client()`; [DJ-2.2] edit withdrawn; README moves to task 6; task 3 split into red (3) and green characterization (3b); `timeout=-1` replaced by `memory_mb=0`; parity claim limited to the shared vocabulary; register classes defined and rows 5/9/10/12/13/16 corrected; release coupling made explicit (task 8, `0.9.100`/`0.9.34`); citations corrected (Codex #11, Claude #1/#5). Ownership: seam ownership moves from `weft/client/_prepared.py` to `weft/client/_client.py` + `weft/commands/submission.py`. Blast radius: additive for engram (uses `WeftClient.from_weft_context` only). This revision **re-enters review** (task 1). | Review round 1: Codex BLOCKED (#1 class-D context side effect; #2-#7), Claude PASS with findings; dispositions below. |

## Review Record (append-only)

### Round 1 — 2026-09-08

- Reviewer: Codex CLI (OpenAI family). Verdict: **BLOCKED** (7 blocking,
  4 other).
- Reviewer: Claude (Anthropic family). Verdict: **PASS** (5 findings +
  1 raise for human review).
- Each finding was reproduced against 178e3a34 before disposition
  (probes: `scratchpad/plans/08-rev/probe_pure.py`, `probe_paths.py`;
  file reads cited in §3).

| # | Finding (short) | Reproduced? | Disposition | Plan section changed |
|---|---|---|---|---|
| Codex 1 | Export loses filesystem/broker independence via `build_context` (config write :268/:469, dir tightening :290-300 CTX.5, DB init :303); `create_dirs=False` variant still writes config | Yes — read `weft/context.py`; `WeftClient.__init__` :42 always has a context | **Accepted (class D).** Seam redesigned: context-free `normalize_taskspec_payload` over `submission.prepare_definition`; Django export never builds a client. Purity tests added on both surfaces. | §1, §4, delta ([PY-1]/[PY-3]/[DJ-8.1]/README), tasks 3-6, register row 10, Revision Log |
| Codex 2 | `timeout=-1` is accepted by the schema (`SpecSection.timeout` :958 unconstrained) | Yes — probe: accepted | **Accepted.** Rejected value is now `memory_mb=0` (`ge=1`, model.py:304-306); register row 5 corrected; note that `timeout=-1` is accepted on both surfaces | §1 table, tasks 3/3b/5, register row 5 |
| Codex 3 | Proposed [PY-1] "exactly `name`, `taskspec_payload()`, `submit()`" is false — `client` is a public dataclass field; parity guard is presence-only | Yes — `_prepared.py:18`; `test_client.py:239-246` checks `hasattr` only | **Accepted (moot by redesign).** No `PreparedSubmission` member claim remains; [DJ-2.2] edit withdrawn; the exactness guard is the exact `__all__` set test (:1002). `client` visibility is explicitly out of scope. | delta table, §3, §9, register row 12 |
| Codex 4 | `taskspec_payload()` specified as "what `submit()` would write" is false — it is the pre-transport snapshot; pipelines carry nested `_weft_bundle_root`; top-level-only test would miss it | Yes — transport.py:67-73, pipelines.py:471, spawn_requests.py:136 | **Accepted.** [PY-3] text redefined as the pre-transport snapshot, guarantee qualified as top-level only; test adds a nested-key-survives cell; pipeline-reference export declared out of scope | delta [PY-3], §4, task 3, §9 |
| Codex 5 | Release/floor plan incomplete: `0.9.99` and `weft-django 0.9.33` tagged; no Django bump, root extras, or lock refresh | Yes — `v0.9.99` tag, CHANGELOG:43, root pyproject:49/:52, uv.lock:1730 | **Accepted.** Task 8 names `0.9.100`/`0.9.34`, extras, `uv lock`, core-first ordering; `uv` lock feasibility explains the coupling to the root bump | §3 release coupling, task 8, register row 16, Q3 |
| Codex 6 | Parity claim contradicts the `wait` rule | Yes — `_submit_kwargs` :157-160 strips `wait`; export would raise | **Accepted.** Parity limited to the shared public override vocabulary; `wait` difference stated | delta [DJ-8.1] |
| Codex 7 | Task 3 red gate impossible: unknown-override cells already pass | Yes — :166-170, :611, :693 raise today | **Accepted.** Task 3 is red-only; new task 3b holds green characterization tests | tasks 3, 3b |
| Codex 8 | Strategy A broken: task 4 names an unbuilt Django function; no reciprocal backlink; README published before code | Yes — writing-plans.md :427-437 | **Accepted.** Spec 14 note maps core code only; Django function carries no `Spec:` line; README moves to task 6; task 9 grep checks the single reciprocal pair | tasks 2, 4, 6, 9; delta table |
| Codex 9 | Register unreliable: row 9 (`_weft.` names are rejected, OBS.9), row 10 understated, row 12 omits `client`, classes undefined | Yes — probe: `ValueError` "reserved"; OBS.9 :320-321 | **Accepted.** Classes defined in-plan; rows 5/9/10/12 rewritten; rows 13/16 added; newly rejected inputs classed A under the owner decision | register |
| Codex 10 | Tests: default-valued `None` test proves nothing; parity presence-only; four e2e Django runs; no invalid-value `prepare_spec` test; cited bundle fixture does not exist | Yes — test_cli_run.py:1304 is inline; :239-246 presence-only | **Accepted.** Fully-declared templates on both surfaces; exact `__all__` test instead of parity; one e2e cell; `prepare_spec`/`submit_spec` invalid-value cells in 3b; bundle provenance built inline via `validate_taskspec_payload(bundle_root=...)` | tasks 3, 3b, 5 |
| Codex 11 | Citations: `planned_spec_globs` also in writing-plans.md:412; decorated deferred test reaches the private function; `backstitch` in docs/lessons.md; [DJ-19] fail-clearly rule is post-split only | Yes — all four | **Accepted.** All four corrected; [DJ-19] now cited for the monorepo rule :1314-1316 only | §2, §3, §7 |
| Claude 1 | Wrong anchor: exactness test def is :1002, :952-975 is parametrize data | Yes | **Accepted.** | §3 |
| Claude 2 | [PY-3] under-enumerates: `submit`, `submit_spec`, `submit_pipeline` unnamed | Yes — `_client.py:63-70` | **Accepted.** Clause "each `submit*` raises what its `prepare*` counterpart raises" added; cells in 3b | delta [PY-3], task 3b |
| Claude 3 | `FIXTURE_WORKING_DIR` undefined; fixture cannot import `TEST_ROOT` | Yes — settings.py:6-7, test :23 | **Accepted.** `settings.BASE_DIR` | task 5 |
| Claude 4 | Version floor decidable (`weft>=0.9.100`); weft-django needs its own bump | Yes | **Accepted.** Merged with Codex 5 | task 8, Q3 |
| Claude 5 | Off-by-two anchor: 13C :288-290 | Yes | **Accepted.** | §2, §4 |
| Claude raise | Row 10 under-describes the no-`CONTEXT` case: `build_context(None)` discovers from `Path.cwd()` and creates `.weft/` there | Yes — context.py:234-236 | **Accepted (moot by redesign, guarded).** No discovery occurs on the export path; the purity tests snapshot the parent chain and `settings.BASE_DIR` so a regression would fire | task 3, task 5, register row 10 |

### Round 2 — 2026-09-08 (against revision 1)

- Reviewer: Codex CLI (OpenAI family). Verdict: **FAIL** (2 P1, 3 P2,
  1 P3, 1 nit).
- Reviewer: Claude (Anthropic family). Verdict: **PASS** (1 P2, 1 P3,
  1 nit).
- Each finding was reproduced against 178e3a34 before disposition
  (probes: `scratchpad/plans/08-rev2/probe_r2.py` — `payload=` capture
  under both leaf shapes and a `Path.resolve` trap on bundle-rooted vs
  mapping input; `probe_django_today.py` — today's `stream_output=None`
  export and the `weft_task` signature; file reads cited per row).

| # | Finding (short) | Reproduced? | Disposition | Plan section changed |
|---|---|---|---|---|
| Codex R2-1 (P1) | Context-free helper still resolves the filesystem for bundle-rooted input: `set_bundle_root` → `Path.resolve()` (model.py:1394-1404); "no I/O" is false; purity test would not catch it | Yes — trap: 2 `Path.resolve` calls on a bundle-rooted `TaskSpec`, 0 on a plain mapping | **Accepted (narrow, not redesign).** Contract wording on every surface becomes "constructs no Weft context, reads no configuration, opens no broker, writes nothing"; the read-only re-resolution is documented and excluded from the Django path (mappings only); purity tests trap `build_context`/`load_config`/broker construction, not `Path.resolve`; scope of bundle-rooted inputs raised as Owner Question 4 (default: accepted, provenance stripped, documented) | §1, §4, delta [PY-3]/[DJ-8.1]/README, tasks 3-7, §6, register rows 10/17, Q4 |
| Codex R2-2 (P1) | Task 8 omits `weft._constants.__version__` (`:47`); `test_constants.py:238` would fail; gates run before task 8 only | Yes — `__version__ = "0.9.99"` at :47; `test_version` compares to `pyproject.toml` | **Accepted.** Task 8 bumps `__version__` with `pyproject.toml`, uses the dated `## [0.9.100] - YYYY-MM-DD` heading, and re-runs `tests/system/test_constants.py` and the release gates after the edits | task 8, register row 16, Q3 |
| Codex R2-3 (P2) | Django purity test cannot detect regression to `get_core_client()`/`build_context()`: suite keeps `CONTEXT=TEST_ROOT` (:36, :96) and bootstraps `TEST_ROOT/.weft` (:80-85) | Yes — read test module :30-40, :80-85, :95-115 | **Accepted.** Test rebuilt: `override_settings(WEFT_DJANGO=_fixture_weft_settings(CONTEXT=None), BASE_DIR=tmp_path)`, `CORE_CONTEXT_OVERRIDE_ENV_KEYS` + `WEFT_CONTEXT` cleared, `resolve_context_override() == str(tmp_path)` asserted first, read-only `tmp_path`, `build_context` tripwire, `TEST_ROOT/.weft` snapshot | task 5, §6 |
| Codex R2-4 (P2) | Exhaustive `None` matrix cannot pass: fixture declares no `tags`; `weft_task` has no `tags=` argument | Yes — decorators.py:150-162; probe: `tags` not in signature | **Accepted.** Fixture `metadata={"declared_key": "declared", "tags": ["declared"]}`; rationale recorded | task 5 |
| Codex R2-5 (P2) | `prepare_definition` owns [PY-3] but carries no `Spec:` backlink; task 9 checks only `_client.py` | Yes — task-4 code block | **Accepted.** `Spec: … [PY-3]` on `prepare_definition`, [PY-3] added to the `submission.py` module docstring, task 9 verifies both backlinks against the `Implementation:` note | tasks 4, 9 |
| Codex R2-6 (P3) | [DJ-8.1] "performs no I/O" unsupportable: `build_envelope` runs the configured request-ID provider (decorators.py:85-90; `conf.py:146-148`) | Yes | **Accepted.** [DJ-8.1] and README narrowed to the Weft context/configuration/broker/write boundary and state that the provider runs as it does for `enqueue(...)` | delta [DJ-8.1]/README, §4, register row 10 |
| Codex R2-7 (nit) | RUFF-SUP-209's second proof is the native deferred test (never reaches the copy); [DJ-19] operative text is :1318-1319 | Yes — registry :87; 13C :1316-1319 | **Accepted.** §3 evidence rewritten; §2 anchors :1316/:1318-1319; fresh-eyes item 5 annotated; register row 16 anchor corrected | §2, §3, §10, register row 16 |
| Claude R2-1 (P2) | `**overrides` leaf captures `payload=` in the keyword parameter; `payload={"x": 1}` silently accepted, `payload=object()` → snapshot `ValueError`; contradicts "any other name raises `TypeError`" | Yes — probe under both shapes | **Accepted.** `prepare_definition(taskspec, overrides: Mapping[str, Any], *, payload=None)`; `prepare` passes its dict through; `payload={"x": 1}` → `TypeError` cells in tasks 3 and 5; [PY-3] sentence says every keyword is an override name | §1, delta [PY-3], tasks 3, 4, 5, 6, register row 12 |
| Claude R2-2 (P3) | "the last raises today" is wrong: `stream_output=None` returns an invalid payload; the cell is red by inequality | Yes — probe: returned with `spec.stream_output == None`, fails validation | **Accepted.** Parenthetical corrected | task 5 |
| Claude R2-3 (nit) | Optional wording for `set_bundle_root`'s read-only `Path.resolve()` | Yes | **Accepted (folded into Codex R2-1).** | as R2-1 |

Not a finding (Claude): the [PY-3] reserved-name `ValueError` holds on
`prepare_spec` because `CommandUsageError` subclasses `ValueError`
(`weft/_exceptions.py:24`) — no text change.

### Round 3 — 2026-09-08 (scoped verification against revision 2)

- Reviewer: Codex CLI (OpenAI family). Verdict: **FAIL** (1 P1, 1 P2, 1 P3) —
  all three on test mechanism and wording, none on the seam, the delta,
  or the register.
- Reviewer: Claude (Anthropic family). Verdict: **PASS** (1 P3, 1 nit) —
  the same tripwire-binding defect.
- All ten round-2 dispositions were verified real by both reviewers.

| # | Finding (short) | Reproduced? | Disposition | Plan section changed |
|---|---|---|---|---|
| Codex R3-1 (P1) / Claude R3-1 (P3) | Purity tripwires patch the defining modules; `_client.py:16` and `context.py:70-77` import `build_context`/`load_config` by name so the traps never fire; broker opens via `open_broker`, not `Queue.__init__` | Yes — `grep -n "^from" weft/client/_client.py`, `weft/context.py:61,:157` | **Accepted.** Traps moved to the use-site bindings `weft.client._client.build_context`, `weft.context.load_config`, `weft.context.open_broker`, plus `weft_django.client.get_core_client` in the Django test | tasks 3, 5, §6 |
| Codex R3-2 (P2) | Django purity test snapshots `weft.db`; the default database is `.weft/broker.db` | Yes — `tests/system/test_constants.py:514` | **Accepted.** Snapshot `_bootstrap_context.database_path` instead of a hard-coded filename | task 5 |
| Codex R3-3 (P3) / Claude R3-2 (nit) | §1 still calls today's export "a pure function"; task 5 traps fewer symbols than §6/Revision Log claim | Yes | **Accepted.** §1 reworded to the narrowed boundary; task 5 now traps the same set as task 3 | §1, task 5 |

Revision 3 is non-material (test mechanism and one sentence). The
remaining owner questions are unchanged (Q1–Q4).

### Round 4 — 2026-09-08 (scoped verification against revision 3)

- Reviewer: Codex CLI (OpenAI family). Verdict: **PASS**. R3-1, R3-2,
  R3-3 verified real; one nit (NEW-1): the `open_broker` call-site
  citation should be `context.py:304` → `:453-466` (`_ensure_database`),
  not `:157` (`WeftContext.broker()`). Accepted; corrected in tasks 3
  and §6.
- Reviewer: Claude (Anthropic family), round 3 against revision 2:
  **PASS**; the same fixes verified.
- Independent review of the plan and its Proposed Spec Delta is complete
  from two agent families. The plan is review-clean pending the owner
  questions Q1–Q4.
