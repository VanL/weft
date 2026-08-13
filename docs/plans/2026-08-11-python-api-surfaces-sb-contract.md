# Python API Surfaces: Adopt the SimpleBroker Three-Surface Contract

Status: completed
Source specs: `docs/specifications/09-Implementation_Plan.md` [IP-1.0]; `docs/specifications/10-CLI_Interface.md` [CLI-0.2], [CLI-1.1.1] (run-input/parameterization clauses); `docs/specifications/02-TaskSpec.md` (parameterization and run-input sections currently scoped to `weft run --spec`); `docs/specifications/11-CLI_Architecture_Crosswalk.md` [CLI-X1], [CLI-X2], [CLI-X3]; `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-8.2], [DJ-8.4]. No weft spec currently owns a complete public-Python-surface contract; this plan adds one (new spec file, Section 7). Design source (non-normative for weft, normative for the pattern being adopted): `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-1], [SB-API-10].
Superseded by: none

Class: 5. This declares public Python surfaces and a stability policy,
replaces the current mixed rendered-text/exit-code command returns with
structured Python outcomes and typed failures, makes `weft.commands.__all__` the
authoritative full-CLI inventory, moves non-I/O CLI behavior into those exact
exports, restores a public `cmd_run`,
extends the public client and weft_django submission interfaces
(`spec_args`), introduces a typed submission-error taxonomy, and adds
normative spec text. Public contracts change, so the hardening trigger fires
and the hardening checklist applies. There are no persistence, wire-format,
or one-way-door changes; rollback before release is a revert.

Plan type: implementation with spec revision. Promotion strategy: **A —
in-file requirement text before link claims** (spec text promotes first;
implementation mappings land with their owning slices).

## Spec Baseline

- Original authoring baseline for source specs and inspected implementation:
  `1edafaf27af451d6533ea0b7f65b856ff4474c39` (tag `v0.9.95`).
- Implementation-start baseline: `fd544c33092cd8fb135098cfd43b7dc6c7aaadc3`.
  The only relevant-path change between these identifiers is the completed
  shared-reactor addition to `docs/specifications/08-Testing_Strategy.md`;
  the API-surface source specs and implementation files are unchanged.
- Promotion baseline identifier: to be recorded in the Execution Log when the
  spec-promotion slice lands (committed baseline plus the reviewed promotion
  diff for the files in the Section 7 table).
- No pre-existing uncommitted spec edits exist at plan authoring time.

## 1. Goal

Finish adopting SimpleBroker's public-API contract in weft, both halves:

1. `weft.client` remains the primary embedder interface and gains the one
   capability that today forces consumers into internals: declared-argument
   submission for stored specs (`spec_args`), threaded through `weft_django`.
2. `weft.commands` becomes the declared public command surface (the analog
   of `simplebroker.commands` under [SB-API-10]). Its `__all__` mirrors all
   41 CLI verbs and is the sole authoritative name inventory. Names are the
   CLI path with a `cmd_` prefix and underscores between path segments:
   `weft queue alias remove` maps to `cmd_queue_alias_remove`. Each Typer
   callback performs only input decoding and output formatting, and invokes
   that exact exported function for validation, orchestration, execution, and
   result construction. The facade resolves exports lazily so importing one
   command does not initialize unrelated capabilities.
3. The layering rules become normative spec text ([PY-4]). Most of the
   matrix is already mechanically enforced by
   `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries`;
   this plan adds the adapter-pair rows (`cli` ↔ `client` never import each
   other), full Typer-tree-to-`__all__` mapping enforcement, and a structural
   thin-adapter check. Parity comes from shared function identity, not from
   two implementations that happen to produce equal output.

Design bar (Ousterhout): deep modules — the caller's sentence "submit stored
spec X with args Y, no wait, give me the TID or a typed error" must be
expressible in ≤ 4 arguments on each surface, with resolution, validation,
declared-argument processing, TID commitment, and receipt semantics hidden
behind the seam. The motivating counterexample: mm-governance called the
internal `execute_run` with 1 positional + 22 required keywords, and the
call broke on 0.9.95's parameter removal. (The `monitor=` hotfix has since
been applied downstream; the structural coupling remains until this plan's
surfaces exist.)

## 2. Source Documents

Read before editing:

1. `AGENTS.md` and the complete `docs/agent-context/` read order.
2. The source specs and section codes in this plan's metadata.
3. `docs/agent-context/runbooks/writing-plans.md` (§4c, §4d),
   `hardening-plans.md`, `review-loops-and-agent-bootstrap.md`,
   `adversarial-acceptance-probes.md`, `testing-patterns.md`.
4. `docs/agent-context/lessons.md` and `docs/lessons.md`.
5. `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-1],
   [SB-API-10] — including its framing: the commands layer is "for process
   and CLI reuse"; "default embedding for application logic" uses the
   primary API. Note the structural difference this plan must bridge:
   `simplebroker.commands` is a single module; `weft.commands` is a package
   of ~25 leaf modules and therefore needs a lazy facade to preserve import
   isolation while presenting the same `__all__`-owned contract.
6. Consumer evidence (read-only, out-of-repo):
   `../mm-governance/opsweb/apps/monitoring/management/commands/submit_weft_spec.py`
   and `../mm-governance/agents/monitoring/weft_inputs.py`.

Comprehension questions (hardening requirement — answer before editing):

- In `weft/commands/run.py::_execute_spec_via_manager` (~1287-1371), what is
  the exact processing order from template resolution to TID commitment, and
  where do parameterization (~1310) and run-input adaptation (~1324) sit
  relative to template revalidation and `_enqueue_taskspec`?
- Which two contexts does the run path use — the reference-lookup context
  (`context_dir`, possibly discovery) and the runtime context built from the
  materialized spec's `spec.weft_context` (~1322) — and where does
  `prepare_spec`'s single bound `context.root` (submission.py:378) diverge
  from that?
- Which existing exceptions on the submission path are NOT `WeftError`
  today (`RunUsageError(ValueError)` at run.py:90; bare `RuntimeError` from
  `ensure_manager_after_submission` at submission.py:226-274; raw pydantic
  errors), and which callers depend on their current types?
- Which architecture guards constrain `weft/commands/__init__.py` today
  (`test_internal_package_initializers_are_markers` at
  test_import_boundaries.py:845 — currently no `__all__`, no `__getattr__`;
  `test_commands_specs_import_does_not_initialize_sibling_capabilities` at
  :773 — no sibling initialization on submodule import), and how must the
  first guard narrow while the second remains an invariant after
  `weft.commands` becomes a lazy public facade?

## 3. Context and Key Files

Files to modify:

- `docs/specifications/14-Python_API_Surfaces.md` (new)
- `docs/specifications/README.md` (register spec 14 in current-contract
  overview and reading order)
- `docs/specifications/09-Implementation_Plan.md` ([IP-1.0])
- `docs/specifications/02-TaskSpec.md` (parameterization/run-input scope
  clauses: from "`weft run --spec`" to "spec submission surfaces")
- `docs/specifications/10-CLI_Interface.md` (same generalization where the
  clauses claim CLI exclusivity)
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` (ownership rows)
- `docs/specifications/13C-Using_Weft_With_Django.md` ([DJ-2.1] stable list;
  [DJ-8.2] and [DJ-8.4] helper signature/argument inventories)
- `docs/specifications/00-Quick_Reference.md` (surface summary row)
- `weft/commands/__init__.py` (lazy public facade; authoritative `__all__`)
- `weft/commands/run.py` (new commands-layer `cmd_run`; `execute_run`
  remains private engine)
- `weft/cli/run.py`, `weft/cli/validate_taskspec.py`, and `weft/cli/app.py`
  (retain only Typer/input decoding, help, and output formatting; remove
  validation and orchestration now owned by exact command exports)
- `weft/commands/init.py`, `weft/commands/dump.py`, `weft/commands/load.py`,
  `weft/commands/tidy.py`, `weft/commands/builtins.py`,
  `weft/commands/result.py`, `weft/commands/system.py`,
  `weft/commands/prune.py` (result-contract normalization, Section 6.1)
- `weft/commands/queue.py`, `weft/commands/manager.py`,
  `weft/commands/serve.py`, `weft/commands/specs.py`,
  `weft/commands/tasks.py`, `weft/commands/task_monitor.py`, plus focused
  command-owner leaves where needed to keep `weft.client` imports light
  (one canonical exported owner per CLI verb; move cross-option validation,
  target selection, orchestration, error translation, and result construction
  out of Typer callbacks)
- `weft/commands/submission.py` (declared-argument pipeline in
  `prepare_spec`/`submit_spec`; typed manager-wait errors)
- `weft/_exceptions.py` (submission/usage error taxonomy)
- `weft/client/_client.py`, `weft/client/_prepared.py` (`spec_args`; typed
  submission errors)
- `weft/ext.py` (declare `__all__`; move `SpecRunInputRequest` here as its
  acyclic public owner)
- `weft/core/taskspec/run_input.py`, `weft/core/taskspec/__init__.py`,
  `weft/builtins/run_input.py`, `weft/builtins/dockerized_agent_examples.py`
  (import the request contract from `weft.ext`; stop exporting a core-owned
  duplicate)
- `integrations/weft_django/weft_django/client.py`
  (`submit_spec_reference`(+`_on_commit`) gain `spec_args`)
- `tests/architecture/test_import_boundaries.py` (adapter-pair rows +
  facade inventory/lazy-load tests; narrow the marker guard to `weft.cli`
  and `weft.core`; preserve the sibling-isolation invariant — see Section
  6.2)
- `tests/commands/*`, `tests/cli/test_cli_run.py`, `tests/core/test_client.py`,
  `integrations/weft_django/tests/`

Read first:

- `weft/core/taskspec/run_input.py` and
  `weft/core/taskspec/parameterization.py` (the two halves of
  declared-argument processing; parameterization consumes tokens first,
  remainder feeds run-input — run.py:1310→1324)
- `weft/commands/types.py` (`SubmittedTaskReceipt`, `PreparedSubmissionRequest`)
- `weft/core/spawn_requests.py` (TID commitment at queue write, [MANAGER.4])
- `tests/architecture/test_import_boundaries.py:773, :845`

Current structure (verified at baseline; corrected after review):

- Nine command functions exist with three result shapes: `cmd_init -> int`
  (prints directly); `cmd_load`, `cmd_dump`, `cmd_tidy`,
  `cmd_system_builtins`, `cmd_result`, `cmd_status ->
  tuple[int, str | None]` (no printing — the tuple's second element is the
  full rendered output, routed to stdout or stderr by the Typer adapter
  based on exit code, e.g. cli/app.py:1238-1249); `cmd_prune ->
  tuple[int, str, str]` (separate stdout and stderr payloads,
  cli/app.py:1183). Watch/stream paths (`cmd_status(watch=True)`,
  `cmd_result(stream=True)`) emit incrementally and cannot return their
  output as a value.
- `weft/commands/__init__.py` is a bare marker today, enforced by the broad
  marker guard. That current rule must change because the owner-designated
  public surface is `weft.commands`. The sibling-isolation guard remains
  load-bearing. `weft/ext.py` has NO `__all__` today.
- `weft/cli/run.py:42` defines a Typer-facing `cmd_run` (adapter callback);
  the pre-0.9.95 commands-layer `cmd_run` was a thin
  `execute_run`-plus-renderer wrapper returning `int`.
- The Typer tree registers 41 verbs. Many already delegate to a command-layer
  function, but `run`, all six `spec` verbs, task list/status/stop/kill/tid,
  and parts of queue write/list/watch/delete still contain validation or
  orchestration in `weft.cli`. Those split ownership points must be removed;
  exporting lower-level helpers without moving the behavior does not satisfy
  this plan.

Shared paths — do not duplicate:

- Declared-argument processing stays owned by
  `weft/core/taskspec/parameterization.py` and
  `weft/core/taskspec/run_input.py`; the shared seam in
  `weft/commands/submission.py` orchestrates both in run.py's current
  order; run.py, `cmd_run`, and the client consume the seam.
- `cmd_run` must be the public semantic wrapper over `execute_run`; run
  formatting and typed-error-to-exit translation stay in `weft.cli`. No
  duplicate orchestration in either wrapper (deletion test).
- Every Typer callback must reduce to: decode Typer-specific input, invoke one
  matching `weft.commands.cmd_<cli_path>` export, format its structured
  outcome or typed error, write the selected streams, and exit. Cross-option
  validation and target selection are command behavior, not parsing, and
  therefore belong in `weft.commands`.

## 4. Invariants and Constraints

Preserve:

- TID format/immutability; forward-only transitions; reserved-queue policy;
  `spec`/`io` immutability; spawn isolation; `weft.state.*` runtime-only.
- TID commitment order: declared-argument processing, validation, and
  snapshotting complete on the template BEFORE the spawn-request queue write
  commits the TID ([MANAGER.4]); this plan moves where preparation is
  *invoked from*, never its position relative to TID commitment.
- CLI behavior, rendered output bytes, stream routing, and exit classes stay
  exactly as characterized. Rendering moves or remains in `weft.cli`; command
  semantics move or remain in `weft.commands`.
- Import isolation: `weft.cli` and `weft.core` remain marker initializers.
  `weft.commands` intentionally graduates to a public lazy facade with
  `__all__` and `__getattr__`; the marker guard (:845) narrows accordingly.
  The sibling-isolation behavior guarded at :773 remains unchanged. No eager
  import cascade may be added to `weft/commands/__init__.py`; importing
  `weft.client` must not initialize unrelated command capabilities.
- Import direction is a mechanical invariant:
  `cli -> commands -> core` and `client -> commands -> core`. `core` imports
  none of `commands`, `cli`, or `client`; `commands` imports neither adapter;
  `cli` and `client` never import each other; command leaf modules never
  import the `weft.commands` facade. Extend the existing AST edge tests to
  enumerate both allowed and forbidden directions. A new CLI-callback AST
  gate requires exactly one expected facade-export call and rejects calls to
  `weft.core`, command leaf modules, or imported domain helpers. Other calls
  must resolve to Typer/stdlib or local `weft.cli` parsing/formatting helpers.
- No new dependency; no new execution path; no framework; `execute_run`
  stays private.

Interface-depth requirements (review gates for every public callable):

- Docstring states the complete interface per house style §4.5 (args,
  defaults, return, typed raises, `Spec:` reference).
- The mm-governance sentence in ≤ 4 arguments on both surfaces.
- Client- and commands-surface failures are typed `WeftError` exceptions
  (after Slice 2 lands the complete taxonomy); CLI adapters translate them
  to the governing exit class and formatted diagnostic.
- No new "common"/"api" module; adapters stay thin (deletion test).

Review gates: no drive-by refactors; spec text promoted before production
edits (§4d); independent review of the delta before the promotion slice;
independent review after Slice 3, after Slice 4, and before completion.

## 5. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [CLI-6] `system task-monitor --follow` | Return/consume a live `CommandStream[TaskMonitorSummary]` until close or interruption. | The registered `--once/--follow` flag sets `follow=True`, but `run_task_monitor` still performs one pass and returns. | The public command contract cannot truthfully call a non-following one-pass result a stream. Implementing the option's stated lifecycle is a defect correction and an execution-path change, covered by controlled-close tests. | [PY-2] makes `follow` the sole positive lifecycle parameter and specifies stream ownership/close behavior. |
| [PY-2] command ownership | Each CLI callback delegates to one structured command owner. | Stored-spec execution initially retained a second preparation path in `commands.run`; the 2026-08-12 remediation absorbed it into `commands.submission.prepare_spec`. | Parameterization, overrides, runtime-context selection, and stdin routing must have one pre-commit order on client and command surfaces. | [PY-3] now names the shared seam and order. |
| [PY-3] stdin channels | The first implementation exposed separate run-input and work-input stdin parameters. | The remediation replaced them with one `stdin_text`, decoded once by the CLI and routed by the shared submission seam. | An adapter cannot know the resolved TaskSpec contract without violating the one-command-call boundary. | Specs 10, 13C, and 14 now define the single-input rule. |
| [PY-3] parameterization context | Client preparation always parameterized against the client root. | Explicit client contexts use the client root; discovery preserves the TaskSpec-declared context, matching `cmd_run`. | Context provenance affects materialization and must be retained rather than inferred from a constructed context object. | Spec 14 now records explicit-versus-discovery behavior. |
| [PY-2] exit mapping | Several queue, manager, and system adapters forced usage failures to exit 1. | Usage failures now map uniformly to 2; timeout remains 124 and other command failures remain 1. | Per-callback overrides contradicted the promoted public error taxonomy. | Spec 14 owns the uniform mapping. |

## 6. Owner Decisions Recorded in This Plan

### 6.1 Structured command outcomes; CLI owns formatting

Every exported `cmd_*` accepts already-parsed Python values, performs semantic
validation and orchestration, and returns a documented structured outcome.
Reuse existing public result/receipt dataclasses where they fit; add a focused
frozen dataclass only when a CLI verb currently has no structured outcome.
Do not introduce one untyped catch-all result dictionary or a generic wrapper
that erases command-specific types. Failures raise typed `WeftError`
subclasses, with a typed usage error for invalid option combinations. The CLI
maps those outcomes and errors to human/JSON text, stdout/stderr, and shell
exit codes.

Streaming exports return an iterator or session over structured events; the
CLI owns formatting each event. They must not write process stdout/stderr from
`weft.commands`. This applies to `cmd_run` when `wait=True`,
`cmd_status(watch=True)`, `cmd_task_status(watch=True)`,
`cmd_result(stream=True)`, `cmd_queue_watch`, and
`cmd_system_task_monitor(follow=True)`. `cmd_manager_serve` is the explicit
foreground non-stream exception: it blocks in the canonical manager runtime,
emits no process output, and returns `None` after clean shutdown.

This intentionally differs from SimpleBroker [SB-API-10]'s print-plus-integer
contract. The owner boundary here is stronger: `weft.cli` exclusively owns
I/O parsing and formatting, while `weft.commands` is the exact semantic code
the CLI invokes and is useful without capturing or parsing terminal text.
Existing short `cmd_*` callers face a breaking return/error contract and name
change; the surface was not previously declared stable, so no compatibility
shim is added. A blocker discovered during implementation produces a
Deviation Log row, not a mixed text/structured public surface.

### 6.2 `weft.commands.__all__` is the authoritative public inventory

The official public surface is the package `weft.commands`, not its leaf
modules. `weft.commands.__all__` is the sole authoritative inventory for both
the 41 command functions and the public outcome/error/stream types needed to
call them. Each listed name is available directly
(`from weft.commands import cmd_run, RunExecutionResult`). The CLI bijection
applies to the `cmd_*` subset, not to type exports.

An eager re-export initializer would initialize unrelated command modules and
make every `weft.client` import pay for the whole surface. Implement the
facade with an explicit private export map plus PEP 562 `__getattr__`: resolve
and cache one named export from its owning leaf module on first attribute
access. `__dir__` may expose the same inventory if needed for discoverability,
but it must derive from `__all__`; it is not a second registry. Unknown names
raise `AttributeError`. Do not expose leaf modules or private helpers through
`__all__`.

The current marker guard (:845) is not an invariant for `weft.commands`; it
must narrow to `weft.cli` and `weft.core`. The sibling-isolation behavior at
:773 remains an invariant and gains stronger fresh-process probes: importing
`weft.commands` alone loads no command leaf module; resolving one export loads
its owning leaf and unavoidable dependencies but not unrelated public command
owners. The existing own-facade/backedge guard remains unchanged: command leaf
modules must not import names from the facade, which would create lazy-export
cycles. Surface growth requires one coordinated change to `__all__`, the
private export map, the public spec's inventory assertion, and firing tests.
The map assertion is `set(_EXPORTS) == set(__all__)`; the CLI assertion is
`{name for name in __all__ if name.startswith("cmd_")} == derived_cli_names`.

### 6.3 `cmd_run` naming

The commands-layer function takes the canonical name
`weft/commands/run.py::cmd_run`. The CLI-local Typer callback currently
named `cmd_run` (cli/run.py:42) is renamed `run_cli_command` in the same
slice — internal adapter, no public contract. Two same-named functions with
different contracts across the seam is a standing hazard both reviews
flagged.

### 6.4 Declared-argument pipeline and precedence

`spec_args` feeds the full declared-argument pipeline, not run-input alone:
parameterization consumes its declared tokens first, the remainder feeds the
run-input adapter. `prepare_spec` performs **run.py's exact current order**
(run.py:1287-1371 — preserve it; do not regroup overrides):
reference resolution (lookup context) → template validation →
`persistent_override` application → revalidation → parameterization
materialization → `name` override → revalidation → runtime-context
resolution from the materialized spec's `weft_context` → stdin gating and
run-input adaptation (producing the work payload) → snapshot. TID commits
afterward at the spawn-request queue write, unchanged. All other submit
overrides (`SUBMIT_OVERRIDE_NAMES`: timeout, env, tags, …) apply AFTER
parameterization alongside `name` — only `persistent`-class shaping
precedes it — because materialization deep-copies the pre-parameterization
payload and adapters may drop earlier mutations. Precedence rules: for a
spec that declares a `run_input` contract, the adapter's output is the
work payload and `payload=` is rejected with the typed usage error —
regardless of whether the adapter consumed `spec_args`, `stdin_text`, or
only its defaults; `payload=` is valid only for specs with no `run_input`
contract, where it is the sanctioned initial work payload. `stdin_text`
is valid only when the spec's `run_input` declares stdin; supplying it
otherwise (including for no-`run_input` specs) is the typed usage error —
the CLI's piped-stdin behavior for no-`run_input` specs remains a
CLI-only affordance, not part of this surface. `prepare_spec` NEVER reads
process stdin — a spec whose run-input requires stdin fails without
`stdin_text` with the same rendered error the CLI produces when piped
input is absent.

### 6.5 Two contexts, kept distinct

`prepare_spec` (and `cmd_run`/client callers) take the reference-lookup
context (`context_path`, discovery-capable) and derive the runtime context
from the materialized spec's `spec.weft_context`, mirroring
run.py:203/1322 exactly. Parameterization has its own context rule that
must be preserved verbatim (run.py:1310): the parameterization adapter
receives the explicit `context_path` when one was supplied, otherwise the
template's `weft_context` — not the lookup default and not the
post-materialization runtime context. Client mapping of "explicit": a
client whose context was constructed from a caller-supplied path counts as
explicit (like `--context`); a discovery-constructed client context does
NOT — parameterization then uses the template's `weft_context`. Never pass
`context.root` unconditionally. Additionally, `PreparedSubmission.submit()`
today always submits through `self.client.context` (_prepared.py:26);
Slice 4 must route submission through the runtime context built from the
materialized `weft_context`, without breaking callers that intentionally
share one client across specs (record the resolution in the Deviation Log
if the two conflict). The parity matrix must include a stored spec whose
`weft_context` differs from the invoking directory, in both the
explicit-`context_path` and discovery cases, and must assert the **broker
target / context root** the submission landed in — not only TaskSpec and
payload equality — so a wrong-database submit cannot pass the net.

### 6.6 Typed submission errors

New `weft/_exceptions.py` members: `SubmissionError(CommandError)`,
`SubmissionValidationError(SubmissionError)`, and
`SubmissionManagerError(SubmissionError)`. Spec lookup keeps the existing
`SpecNotFound`; invalid arguments use `CommandUsageError(CommandError,
ValueError)`, replacing/re-parenting `RunUsageError` while preserving existing
`except ValueError` callers. Validation wraps pydantic `ValidationError` with
the original chained; manager-wait failure wraps the current bare
`RuntimeError` from `ensure_manager_after_submission`. The translation
seam covers the **entire client-surface call path, preparation included**:
`WeftClient.prepare_spec`/`submit_spec` and `PreparedSubmission.submit()`
wrap `prepare_spec()`-phase errors (resolution, validation, adapter, OS)
and submit-phase errors alike, so [PY-3]'s promise holds for every failure
a client caller can observe — not only post-preparation ones. The
commands surface adopts the same typed failures; only `weft.cli` translates
them to exit codes and formatted diagnostics. [PY-3]'s sentence promotes with the delta and is
implemented by Slice 4 before any completion claim.

### 6.7 Full CLI inventory and deterministic naming

`weft.commands.__all__` contains exactly one function per canonical CLI verb.
The name is `cmd_` plus the complete CLI path joined by underscores, with CLI
hyphens normalized to underscores. Root verbs omit a group segment. Examples:
`weft run` → `cmd_run`, `weft task status` → `cmd_task_status`, and
`weft queue alias remove` → `cmd_queue_alias_remove`. This yields 41 initial
exports: 4 root, 14 queue, 6 spec, 6 task, 5 manager, and 6 system commands.
There are no short compatibility aliases such as `cmd_tidy`, no generic
exports such as `list_command`, and no duplicate spellings for one verb.

The root callback and global `--version` are parser features, not verbs, and
do not get command exports. Dynamic stored-spec options remain
`spec_args: Sequence[str]` on `cmd_run`; they do not create dynamic Python
parameters. An executable bijection gate recursively inventories the Typer
tree, applies the naming transform, and asserts exact equality with
`weft.commands.__all__`. It also asserts the private lazy-export map has the
same keys. Thus either a CLI-only verb or an API-only command fails CI.

This intentionally makes all 41 names and signatures compatibility-governed.
Every future CLI option/default change must update the same command callable
and its contract tests. That coupling is the desired discoverability rule:
someone who knows the CLI can derive the Python API without documentation.
Function parameter names follow the canonical CLI argument or long-option
spelling literally, with hyphens changed to underscores; callback-local names
do not govern the public signature. Thus `--all`, `--context`, `--input`,
`--arg`, `--kw`, `--tag`, `--continuous`, and `--autostart` become `all`,
`context`, `input`, `arg`, `kw`, `tag`, `continuous`, and `autostart`, even
where the current Typer callback uses names such as `all_tasks`,
`context_path`, `pipeline_input`, `args`, or `persistent_override`.
Repeatable options retain the singular CLI spelling and accept a `Sequence`.
For a dual boolean flag, the non-negated semantic spelling is canonical
(`apply` for `--apply/--dry-run`; `follow` for `--once/--follow`) and the
opposite spelling does not create another parameter. Positional CLI arguments
become positional-or-keyword parameters; semantic options are keyword-only
with the CLI default. Presentation-only flags are excluded per §6.11. No
public command accepts catch-all `**kwargs`; `cmd_run(spec_args:
Sequence[str] = ())` is the explicit dynamic-spec escape hatch.

Three `cmd_run`-only keyword parameters are explicit parser-decoding
exceptions: `describe: bool = False` represents dynamic `--help`, while
`run_input_stdin_text: str | None = None` and `work_input_text: str | None =
None` carry decoded shell stdin. The signature gate requires these exact
names/defaults on `cmd_run` and forbids them on every other command. Two other
intentional semantic normalizations are exact: `cmd_queue_write(queue_name,
message=None, *, endpoint=None)` exposes the CLI positional overload without
the parser-local `name_or_message`, and
`cmd_system_task_monitor(*, follow=False, ...)` exposes the positive lifecycle
mode instead of the callback-local inverse `once` value.

### 6.8 `weft.ext.__all__`

`weft/ext.py` currently has no `__all__`. Slice 4 declares exactly these 13
names: `RunnerHandle`, `RunnerCapabilities`, `RunnerRuntimeDescription`,
`AgentResolverResult`, `AgentToolProfileResult`,
`AgentMCPServerDescriptor`, `RunnerEnvironmentProfileResult`, `AgentResolver`,
`AgentToolProfile`, `RunnerEnvironmentProfile`, `TaskRunnerBackend`,
`RunnerPlugin`, and `SpecRunInputRequest`. The first 12 are the module's
existing public protocol/dataclass names; `SpecRunInputRequest` moves from
`weft.core.taskspec.run_input` to `weft.ext` as the one canonical class.
Core and builtin adapters import it from `weft.ext`; core no longer re-exports
it. A literal identity/inventory test prevents drift.

### 6.9 Public command outcome matrix

Outcome and stream types are exported from `weft.commands.__all__` alongside
the 41 functions. The exact public type inventory is: `InitResult`,
`RunSpecDescription`, `RunSession`, `CommandStream`, `RunExecutionResult`,
`TaskSnapshot`, `TaskResult`, `TaskEvent`, `ServiceSnapshot`,
`SubmittedTaskReceipt`, `TaskPingResult`, `TaskControlResult`, `QueueEntry`,
`QueueInfo`, `QueueWriteReceipt`, `QueueMoveResult`, `QueueDeleteReceipt`,
`QueueBroadcastReceipt`, `QueueAliasRecord`, `EndpointResolution`,
`ManagerSnapshot`, `SpecRecord`, `SpecValidationResult`,
`SpecMutationResult`, `SystemStatusSnapshot`, `SystemTidyResult`,
`SystemLoadResult`, `SystemDumpResult`, `SystemPruneResult`,
`BuiltinSpecRecord`, `TaskMonitorConfig`, `TaskMonitorResult`,
`TaskMonitorRecord`, and `TaskMonitorSummary`. Existing matching definitions stay in
`weft.commands.types`; add the missing frozen/Protocol definitions there.
All are imported through `weft.commands`; direct `weft.commands.types`
imports are not the public contract. Internal transport values such as
`PreparedSubmissionRequest` are deliberately not facade exports.

Required fields for new values:

- `InitResult(root: Path, config_path: Path, created: bool)`
- `RunSpecDescription(reference: str, usage: str,
  arguments: tuple[Mapping[str, Any], ...], stdin: Mapping[str, Any] | None)`
- `CommandStream[T]`: iterator protocol plus idempotent `close()`; natural
  exhaustion or `close()` releases owned queue/session resources; iteration
  failures raise typed command errors
- `RunSession`: `tid`, `events() -> CommandStream[TaskEvent]`,
  `send_input(str)`, `close_input()`, `stop()`, and
  `wait(timeout=None) -> RunExecutionResult`; `close()` is idempotent and does
  not imply task cancellation
- `QueueMoveResult(source: str, destination: str,
  entries: tuple[QueueEntry, ...], moved_count: int)`; `entries` is the exact
  ordered set moved, including the unbounded backend-native path
- `TaskPingResult(tid: str, acknowledged: bool, timed_out: bool,
  error: str | None, observed_at: int | None, pong: Mapping[str, Any] | None,
  snapshot: TaskSnapshot | None)`
- `TaskControlResult(command: Literal["stop", "kill"],
  requested: tuple[str, ...], accepted: tuple[str, ...],
  snapshots: tuple[TaskSnapshot, ...])`
- `SpecMutationResult(action: Literal["create", "delete"], record: SpecRecord)`
- `QueueDeleteReceipt(queue: str | None, deleted_count: int,
  queues_deleted: int, all_queues: bool, exact_message: str | None)`
- `SystemDumpResult(path: Path, queues: int, messages: int, aliases: int,
  omitted_claimed_queues: int, omitted_claimed_messages: int)`
- `SystemPruneResult(families: tuple[str, ...], applied: bool,
  candidates: int, deleted: int, failed: int,
  details: Mapping[str, Any])`; `details` losslessly carries the current
  runtime/retention candidate, archive, error, and report data needed by both
  CLI renderers
- `BuiltinSpecRecord(name: str, description: str | None, category: str | None,
  function_target: str | None, supported_platforms: tuple[str, ...],
  path: Path, source: str = "builtin")`
- `TaskMonitorResult` is refactored to structured fields only: `log_path`,
  `records_written`, `events_scanned`, `tids_seen`, `summaries_emitted`,
  `checkpoint_timestamp`, and `records: tuple[TaskMonitorRecord, ...]` in
  exact emission order; remove `exit_code`, `stdout`, `stderr`, and JSON
  rendering. `TaskMonitorRecord(record: Mapping[str, Any])` represents every
  run-start, per-task summary, and run-completed JSONL record. Its mapping is
  the lossless pre-serialization payload. `TaskMonitorConfig` drops
  `json_output`. `TaskMonitorSummary` remains the per-task structured record
  yielded by follow mode.
- `TaskMonitorConfig(context: str | Path | None = None,
  follow: bool = False, sink: Literal["stdout", "disk"] = "stdout",
  log_dir: Path | None = None, checkpoint: Path | None = None,
  no_checkpoint: bool = False, since: int | None = None,
  limit: int | None = None, monitor_name: str = "default")`
- `TaskMonitorSummary(record: Mapping[str, Any])`
- `RunExecutionResult.submission_error` is removed; submission failures raise
  the typed hierarchy. Its task-terminal `status`, `result_value`, and
  `error_message` remain outcome data, not command-invocation failure.

`SpecRecord` gains `payload: Mapping[str, Any] | None = None`; list results may
leave it `None`, while `cmd_spec_show` always returns the resolved payload.
`TaskEvent.payload` losslessly carries lifecycle, stdout/stderr chunk, result,
and control envelopes in observed order, so root-result streams and
`RunSession.events()` do not discard the current streaming data.
`TaskSnapshot` gains optional `host_pids: tuple[int, ...] | None`,
`managed_pids: tuple[int, ...] | None`, and
`live_managed_pids: tuple[int, ...] | None`; `cmd_task_status(process=True)`
populates them, while other snapshot producers leave them `None`.
`ManagerSnapshot` gains optional
`liveness: Literal["live", "stale", "unknown", "non_live"] | None = None`,
`proof_source: str | None = None`, `proof_detail: str | None = None`,
`dispatch_eligible: bool | None = None`,
`canonical_candidate: bool | None = None`, and
`canonical: bool | None = None` fields;
`cmd_manager_list(diagnostic=True)` populates them and non-diagnostic calls
leave them `None`. `ManagerSnapshot` also has
`started_here: bool | None = None`; only `cmd_manager_start` populates it so
the CLI can preserve its started-versus-existing lifecycle message without a
second semantic query.

The return matrix is normative for implementation and tests:

| Family | Function(s) | Return |
|---|---|---|
| root | `cmd_init` | `InitResult` |
| root | `cmd_status` | `SystemStatusSnapshot` or `CommandStream[TaskEvent]` when watching |
| root | `cmd_result` | `TaskResult`, `tuple[TaskResult, ...]`, or `CommandStream[TaskEvent]` when streaming |
| root | `cmd_run` | `RunSpecDescription` iff `describe=True`; `RunSession` iff `describe=False` and `wait=True`; `RunExecutionResult` iff `describe=False` and `wait=False`. `interactive` changes the session capabilities, not the return branch. |
| queue | `cmd_queue_read`, `cmd_queue_peek` | `tuple[QueueEntry, ...]` |
| queue | `cmd_queue_write` | `QueueWriteReceipt` |
| queue | `cmd_queue_move` | `QueueMoveResult` |
| queue | `cmd_queue_list` | `tuple[QueueInfo, ...]` or `tuple[EndpointResolution, ...]` for endpoint mode |
| queue | `cmd_queue_exists` | `bool` |
| queue | `cmd_queue_stats` | `QueueInfo` |
| queue | `cmd_queue_resolve` | `EndpointResolution` |
| queue | `cmd_queue_watch` | `CommandStream[QueueEntry]` |
| queue | `cmd_queue_delete` | `QueueDeleteReceipt` |
| queue | `cmd_queue_broadcast` | `QueueBroadcastReceipt` |
| queue alias | add/remove/list | `QueueAliasRecord`, `QueueAliasRecord`, `tuple[QueueAliasRecord, ...]` respectively |
| spec | create/delete | `SpecMutationResult` |
| spec | list/show/validate/generate | `tuple[SpecRecord, ...]`, `SpecRecord`, `SpecValidationResult`, `Mapping[str, Any]` respectively |
| task | list/status/ping | `tuple[TaskSnapshot, ...]`; `TaskSnapshot` or `CommandStream[TaskEvent]`; `TaskPingResult` |
| task | stop/kill/tid | `TaskControlResult`; `TaskControlResult`; canonical full-TID `str` |
| manager | start/status | `ManagerSnapshot` |
| manager | stop | `ManagerSnapshot | None`; a returned snapshot has terminal `status="stopped"`, while an already-absent manager preserves the current successful no-result behavior with `None` |
| manager | list | `tuple[ManagerSnapshot, ...]` |
| manager | serve | blocks until shutdown and returns `None`; raises typed startup/runtime failure; emits no process output |
| system | tidy/load | `SystemTidyResult`; `SystemLoadResult` |
| system | task-monitor | `CommandStream[TaskMonitorSummary]` iff `follow=True`; otherwise `TaskMonitorResult`. `sink` changes persistence/output behavior but not the return branch. The public function has only `follow`, not an inverse `once` parameter. |
| system | prune/dump/builtins | `SystemPruneResult`; `SystemDumpResult`; `tuple[BuiltinSpecRecord, ...]` |

### 6.10 Typed command failures and CLI exit mapping

All command functions use one typed hierarchy exported from both
`weft.commands` and `weft.client` (same class objects, no adapter-specific
duplicates): `CommandError(WeftError)`, `CommandUsageError(CommandError,
ValueError)`, `CommandTimeoutError(CommandError, TimeoutError)`, and
`CommandExecutionError(CommandError, RuntimeError)`, plus the existing
`InvalidTID`, `TaskNotFound`, `SpecNotFound`, `ControlRejected`,
`ManagerNotRunning`, `ManagerStartFailed`, and the submission subclasses in
§6.6 (`SubmissionError`, `SubmissionValidationError`, and
`SubmissionManagerError`). Wrap backend/Pydantic/OS failures at the owning command boundary with
the original exception chained; do not leak raw exceptions from a public
`cmd_*` call.

The CLI owns this exhaustive exit mapping, enforced by a parameterized firing
test: `CommandUsageError`, `InvalidTID`, `TaskNotFound`, and `SpecNotFound` →
2; `CommandTimeoutError` → 124; every other `CommandError` → 1. Success returns
0. Ctrl-C remains Typer/Click's shell concern and is not synthesized by the
command API.

`weft.client.__all__` keeps its current names and adds exactly:
`CommandError`, `CommandUsageError`, `CommandTimeoutError`,
`CommandExecutionError`, `SubmissionError`, `SubmissionValidationError`, and
`SubmissionManagerError`. Existing `WeftError`, `InvalidTID`, `TaskNotFound`,
`SpecNotFound`, `ControlRejected`, `ManagerNotRunning`, and
`ManagerStartFailed` remain. The identical class objects are also exported by
`weft.commands`; neither facade defines wrapper exception classes.

### 6.11 Presentation flags, stdin, and dynamic help

Presentation-only CLI flags do not appear in public command signatures:
`--json`, `--quiet`, `--verbose`, `--error`, and `--timestamps`. The CLI uses
them only while formatting structured values. Semantic controls remain, such
as `--all`, `--peek`, `--watch`, `--stream`, `--wait`, queue-list `--stats`,
`--endpoints`, `--diagnostic`, and `--sink`, because they change work, data
collection, or lifecycle. Tests introspect signatures and fail if a
presentation-only parameter appears or a semantic option disappears.
Task-list `--stats` is presentation-only aggregation over the returned
`tuple[TaskSnapshot, ...]` and therefore does not appear on `cmd_task_list`.

No code under `weft.commands` reads `sys.stdin`, Click/Typer input streams, or
the controlling terminal. CLI parsing reads and size-bounds stdin, then passes
`message`, `run_input_stdin_text`, `work_input_text`, or a CLI-driven
`RunSession` input call explicitly. `run_input_stdin_text` is accepted only
when a stored spec declares `run_input.stdin`; `work_input_text` is the
ordinary initial input for inline command/function runs, pipelines without an
explicit `pipeline_input`, and stored specs with no `run_input` contract. The
two are mutually exclusive. This preserves the current no-`run_input` piped
stdin behavior without weakening [PY-3]'s declared-adapter rule. This includes
queue write/broadcast and all run modes. An AST gate rejects stdin APIs in
`weft/commands/`.

`weft run --spec REF --help` calls the exact public `cmd_run` with
`describe=True`; it returns `RunSpecDescription` after reference resolution
and declared-argument inspection but performs no submission. The CLI formats
that description into dynamic help. `describe=True` is mutually exclusive
with execution-only inputs and raises `CommandUsageError` on conflict. This
keeps semantic spec loading in the command layer without adding a second
public helper or allowing CLI access to command leaves/core.

## 7. Proposed Spec Delta

| Spec file | Strategy | Sections |
|---|---|---|
| `docs/specifications/14-Python_API_Surfaces.md` | A (new file) | [PY-1]–[PY-4] |
| `docs/specifications/README.md` | A | current-contract inventory and reading order |
| `docs/specifications/09-Implementation_Plan.md` | A | [IP-1.0] |
| `docs/specifications/02-TaskSpec.md` | A | parameterization/run-input scope clauses |
| `docs/specifications/10-CLI_Interface.md` | A | [CLI-1.1.1] run-input scope clause |
| `docs/specifications/13C-Using_Weft_With_Django.md` | A | [DJ-2.1], [DJ-8.2], [DJ-8.4] |
| `docs/specifications/11-CLI_Architecture_Crosswalk.md` | A | mapping rows |
| `docs/specifications/00-Quick_Reference.md` | A | surfaces summary |

### 7.1 New spec `14-Python_API_Surfaces.md`

> # Python API Surfaces
>
> Normative public Python surfaces for embedding weft. Operation meaning is
> owned by the vertical specs; this document owns which names are public,
> the stability policy, and the layering relating the surfaces.
>
> ## Public surfaces [PY-1]
>
> | Surface | Import form | Role |
> |---|---|---|
> | `weft.client` | package (`__all__`) | Primary embedder interface. Default surface for application logic. |
> | `weft.ext` | module (`__all__`) | Extension and downstream-contract surface, including `SpecRunInputRequest`. |
> | `weft.commands` | package (`__all__`, lazy facade) | CLI-equivalent second adapter for process and CLI reuse. |
>
> Each surface's `__all__` is its authoritative public-name inventory.
> `weft.commands.__all__` contains both the CLI-mirroring `cmd_*` callables
> and their documented public outcome, stream, and error types.
> Modules and names not exported there — including `weft.core.*`,
> `weft.helpers`, `weft._constants`, command leaf modules, and command
> helpers such as `execute_run` — are not public and may change in any
> release.
>
> `weft.ext.__all__` is exactly: `RunnerHandle`, `RunnerCapabilities`,
> `RunnerRuntimeDescription`, `AgentResolverResult`,
> `AgentToolProfileResult`, `AgentMCPServerDescriptor`,
> `RunnerEnvironmentProfileResult`, `AgentResolver`, `AgentToolProfile`,
> `RunnerEnvironmentProfile`, `TaskRunnerBackend`, `RunnerPlugin`, and
> `SpecRunInputRequest`.
>
> ## Commands surface contract [PY-2]
>
> Each function exported by `weft.commands.__all__` is the actual
> implementation used by one CLI verb, not a parallel equivalent. The
> function name is deterministically `cmd_` plus the full CLI path joined by
> underscores, with hyphens normalized to underscores; root verbs omit a
> group segment. It
> accepts that verb's semantic options as parsed Python parameters with the CLI's
> defaults, performs semantic validation and orchestration, and returns a
> documented structured outcome. Existing public result and receipt types
> are reused where they fit; invalid calls and operation failures raise typed
> `WeftError` subclasses. Streaming modes (`cmd_run` with `wait=True`,
> `cmd_status`/`cmd_task_status` watch, `cmd_result` stream,
> `cmd_queue_watch`, and `cmd_system_task_monitor` with `follow=True`) return
> typed streams or sessions over structured events. `cmd_manager_serve`
> blocks and returns `None` after clean shutdown. Command exports do not write
> process output.
> Canonical CLI argument and long-option names become Python parameter names
> by replacing hyphens with underscores; callback-local names and short
> aliases do not govern the public signature. Repeatable options retain the
> singular CLI spelling and accept a `Sequence`; dual boolean flags use the
> non-negated semantic spelling (`apply`, `follow`).
> Positional arguments are positional-or-keyword, semantic options are
> keyword-only, and public commands accept no catch-all `**kwargs`.
> `cmd_run` additionally accepts the keyword-only parser-decoded channels
> `describe=False`, `run_input_stdin_text=None`, and `work_input_text=None`;
> these correspond to dynamic help and shell stdin rather than ordinary CLI
> options. `cmd_queue_write(queue_name, message=None, *, endpoint=None)`
> exposes its positional overload without the parser-local
> `name_or_message`, and `cmd_system_task_monitor(..., follow=False)` exposes
> the positive side of `--once/--follow`. These are the only signature-rule
> exceptions.
>
> `weft.cli` owns shell input decoding, human/JSON formatting, stream routing,
> and exit-code translation. Each Typer verb invokes its exact corresponding
> `weft.commands` export once; it contains no semantic validation,
> orchestration, or direct `weft.core` access.
> Presentation-only flags (`--json`, `--quiet`, `--verbose`, `--error`, and
> `--timestamps`) are CLI parameters, not command-function parameters.
> Task-list `--stats` is likewise presentation-only aggregation and is absent
> from `cmd_task_list`; queue-list `--stats` remains semantic. No
> command module reads process stdin; the CLI passes bounded input explicitly.
> `cmd_run(describe=True, spec=REF)` returns structured spec-aware help
> metadata and never submits work.
>
> `weft.commands.__all__` is a bijection with the recursively enumerated CLI
> verb tree. The initial tree contains 41 verbs: 4 root, 14 queue, 6 spec,
> 6 task, 5 manager, and 6 system verbs. Examples are `cmd_run`,
> `cmd_queue_alias_remove`, `cmd_spec_validate`, `cmd_task_status`,
> `cmd_manager_start`, and `cmd_system_task_monitor`. The root callback and
> global `--version` are parser features, not verbs. The package resolves
> exports lazily; import isolation is an implementation invariant, not a
> reduction of the public contract.
>
> The same `__all__` exports the command-consumption types:
> `InitResult`, `RunSpecDescription`, `RunSession`, `CommandStream`,
> `RunExecutionResult`, `SubmittedTaskReceipt`, `TaskSnapshot`, `TaskResult`,
> `TaskEvent`, `ServiceSnapshot`, `TaskPingResult`, `TaskControlResult`, `QueueEntry`, `QueueInfo`,
> `QueueWriteReceipt`, `QueueMoveResult`, `QueueDeleteReceipt`,
> `QueueBroadcastReceipt`, `QueueAliasRecord`, `EndpointResolution`,
> `ManagerSnapshot`, `SpecRecord`, `SpecValidationResult`,
> `SpecMutationResult`, `SystemStatusSnapshot`, `SystemTidyResult`,
> `SystemLoadResult`, `SystemDumpResult`, `SystemPruneResult`,
> `BuiltinSpecRecord`, `TaskMonitorConfig`, `TaskMonitorResult`,
> `TaskMonitorRecord`, and `TaskMonitorSummary`; plus `WeftError`, `CommandError`,
> `CommandUsageError`, `CommandTimeoutError`, `CommandExecutionError`,
> `InvalidTID`, `TaskNotFound`, `SpecNotFound`, `ControlRejected`,
> `ManagerNotRunning`, `ManagerStartFailed`, `SubmissionError`,
> `SubmissionValidationError`, and `SubmissionManagerError`. These are imported from
> `weft.commands`, not its leaf/type modules.
>
> | Command family | Structured return |
> |---|---|
> | root `init` | `InitResult` |
> | root `status` | `SystemStatusSnapshot` or `CommandStream[TaskEvent]` |
> | root `result` | `TaskResult`, tuple of results, or `CommandStream[TaskEvent]` |
> | root `run` | `RunSpecDescription`, `RunExecutionResult`, or `RunSession` |
> | queue read/peek/watch | tuple/stream of `QueueEntry` |
> | queue write/move/delete/broadcast | corresponding queue receipt/result type |
> | queue list/exists/stats/resolve/alias | `QueueInfo`/`EndpointResolution`/`QueueAliasRecord` values, tuples, or `bool` as named |
> | spec create/delete/list/show/validate/generate | `SpecMutationResult`, tuple/one `SpecRecord`, `SpecValidationResult`, or `Mapping[str, Any]` as named |
> | task list/status/ping/stop/kill/tid | snapshots/events, `TaskPingResult`, `TaskControlResult`, or full-TID `str` as named |
> | manager start/status/list | one or a tuple of `ManagerSnapshot`; serve blocks and returns `None` |
> | manager stop | terminal `ManagerSnapshot`, or `None` when already absent |
> | system tidy/load/task-monitor/prune/dump/builtins | the correspondingly named `System*`, `TaskMonitor*`, or `BuiltinSpecRecord` type |
>
> The following public types have these exact fields and lifecycle rules:
>
> - `InitResult(root: Path, config_path: Path, created: bool)`.
> - `RunSpecDescription(reference: str, usage: str,
>   arguments: tuple[Mapping[str, Any], ...], stdin: Mapping[str, Any] | None)`.
> - `CommandStream[T]` is an iterator of `T` with an idempotent `close()`.
>   Exhaustion and `close()` release resources; iteration failures use the
>   typed command-error hierarchy.
> - `RunSession` exposes `tid: str`,
>   `events() -> CommandStream[TaskEvent]`, `send_input(text: str) -> None`,
>   `close_input() -> None`, `stop() -> TaskControlResult`,
>   `wait(timeout: float | None = None) -> RunExecutionResult`, and an
>   idempotent `close()`. Closing a session releases owned resources but does
>   not cancel the task.
> - `QueueMoveResult(source: str, destination: str,
>   entries: tuple[QueueEntry, ...], moved_count: int)`; `entries` is the
>   exact ordered moved set for bounded and unbounded paths.
> - `TaskPingResult(tid: str, acknowledged: bool, timed_out: bool,
>   error: str | None, observed_at: int | None,
>   pong: Mapping[str, Any] | None, snapshot: TaskSnapshot | None)`.
> - `TaskControlResult(command: Literal["stop", "kill"],
>   requested: tuple[str, ...], accepted: tuple[str, ...],
>   snapshots: tuple[TaskSnapshot, ...])`.
> - `SpecMutationResult(action: Literal["create", "delete"],
>   record: SpecRecord)`.
> - `QueueDeleteReceipt(queue: str | None, deleted_count: int,
>   queues_deleted: int, all_queues: bool, exact_message: str | None)`.
> - `SystemDumpResult(path: Path, queues: int, messages: int, aliases: int,
>   omitted_claimed_queues: int, omitted_claimed_messages: int)`.
> - `SystemPruneResult(families: tuple[str, ...], applied: bool,
>   candidates: int, deleted: int, failed: int,
>   details: Mapping[str, Any])`; `details` carries the existing candidate,
>   archive, error, and report data without loss.
> - `BuiltinSpecRecord(name: str, description: str | None,
>   category: str | None, function_target: str | None,
>   supported_platforms: tuple[str, ...], path: Path,
>   source: str = "builtin")`.
> - `TaskMonitorResult(log_path: Path | None, records_written: int,
>   events_scanned: int, tids_seen: int, summaries_emitted: int,
>   checkpoint_timestamp: int | None,
>   records: tuple[TaskMonitorRecord, ...])` contains structured data only.
>   `TaskMonitorConfig` has no `json_output` field, and
>   `TaskMonitorResult` has no exit-code, stdout, stderr, or JSON-rendering
>   fields. `TaskMonitorSummary` remains a structured outcome.
> - `TaskMonitorRecord(record: Mapping[str, Any])` is the lossless
>   pre-serialization form of every run-start, per-task summary, and
>   run-completed record, kept in exact emission order.
> - `TaskMonitorConfig(context: str | Path | None = None,
>   follow: bool = False, sink: Literal["stdout", "disk"] = "stdout",
>   log_dir: Path | None = None, checkpoint: Path | None = None,
>   no_checkpoint: bool = False, since: int | None = None,
>   limit: int | None = None, monitor_name: str = "default")` and
>   `TaskMonitorSummary(record: Mapping[str, Any])`.
> - `RunExecutionResult` has no `submission_error` field. Terminal status,
>   task result, and failure detail remain structured data on the result.
>
> `SpecRecord` has `payload: Mapping[str, Any] | None = None`; list results
> may omit it, while `cmd_spec_show` always supplies the resolved payload.
> `TaskEvent.payload` losslessly carries lifecycle, stdout/stderr chunk,
> result, and control envelopes in observed order.
> `TaskSnapshot` additionally has optional
> `host_pids: tuple[int, ...] | None`,
> `managed_pids: tuple[int, ...] | None`, and
> `live_managed_pids: tuple[int, ...] | None`; task-status process mode
> populates them. `ManagerSnapshot` additionally has optional
> `liveness: Literal["live", "stale", "unknown", "non_live"] | None = None`,
> `proof_source: str | None = None`, `proof_detail: str | None = None`,
> `dispatch_eligible: bool | None = None`,
> `canonical_candidate: bool | None = None`, and
> `canonical: bool | None = None` fields; manager-list diagnostic mode populates
> them. `started_here: bool | None = None` is populated only by
> `cmd_manager_start` so the CLI can preserve its started-versus-existing
> lifecycle message without a second semantic query.
>
> Return selection is deterministic. `cmd_run(..., describe=True)` returns
> `RunSpecDescription` and never submits. Otherwise `cmd_run(..., wait=True)`
> returns `RunSession`, and `cmd_run(..., wait=False)` returns
> `RunExecutionResult`; `interactive` only changes session capabilities and
> never changes the selected type. `cmd_status(..., watch=True)`,
> `cmd_task_status(..., watch=True)`, `cmd_result(..., stream=True)`, and
> `cmd_queue_watch(...)` return `CommandStream` values; their non-streaming
> modes return the concrete outcomes in the table. Root `cmd_result` returns
> one `TaskResult` or a tuple when its semantic `all` mode is selected.
> `cmd_system_task_monitor(..., follow=True)` returns a `CommandStream`;
> otherwise it returns `TaskMonitorResult`; the function has no separate
> `once` parameter and the selected sink does not alter the return type.
> `cmd_manager_stop` returns a terminal `ManagerSnapshot`, or `None` when the
> manager was already absent, preserving that successful no-op. `cmd_manager_serve`
> is the sole foreground exception: it
> blocks, returns `None` after clean shutdown, and reports failures through
> typed exceptions without writing process output.
>
> `CommandStream.close()` and `RunSession.close()` are idempotent and release
> owned resources; closing a session does not imply task cancellation.
>
> `cmd_run` covers every `weft run` mode, including stored-spec submission
> with declared arguments (`spec_args`) and no-wait submission whose
> `--json` stdout payload carries the TID. Listed names follow the same
> compatibility policy as `weft.client`; additions, removals, and renames are
> one change to the CLI tree, its derived `weft.commands.__all__` export, and
> their bijection gate.
>
> Command failures use this exact hierarchy:
> `CommandError(WeftError)`,
> `CommandUsageError(CommandError, ValueError)`,
> `CommandTimeoutError(CommandError, TimeoutError)`, and
> `CommandExecutionError(CommandError, RuntimeError)`. Existing
> `InvalidTID`, `TaskNotFound`, `SpecNotFound`, `ControlRejected`,
> `ManagerNotRunning`, and `ManagerStartFailed` retain their existing
> inheritance. Submission errors are
> `SubmissionError(CommandError)`,
> `SubmissionValidationError(SubmissionError)`, and
> `SubmissionManagerError(SubmissionError)`. Backend, Pydantic, and OS errors
> are translated at the command boundary with exception chaining; raw
> implementation failures do not cross the public surface.
>
> `weft.client.__all__` retains every existing name and adds exactly
> `CommandError`, `CommandUsageError`, `CommandTimeoutError`,
> `CommandExecutionError`, `SubmissionError`,
> `SubmissionValidationError`, and `SubmissionManagerError`. Those names and
> the corresponding names in `weft.commands` resolve to the identical class
> objects; neither facade defines wrappers.
>
> CLI mapping is exhaustive: `CommandUsageError`, `InvalidTID`,
> `TaskNotFound`, and `SpecNotFound` map to exit 2;
> `CommandTimeoutError` maps to 124; every other `CommandError` maps to 1;
> successful formatting maps to 0.
>
> ## Client submission with declared arguments [PY-3]
>
> `WeftClient.submit_spec(reference, *, spec_args=(), payload=None,
> stdin_text=None, **overrides)` and `prepare_spec(...)` process declared
> arguments through the same pipeline and ordering as `weft run --spec`:
> parameterization first, remaining tokens to the run-input adapter, on the
> template, before submission commits a TID. For a spec that declares a
> `run_input` contract, the adapter's output is the work payload and
> `payload=` is rejected with a typed usage error — regardless of whether
> arguments, `stdin_text`, or only adapter defaults produced it; `payload=`
> is valid only for specs with no `run_input` contract. `stdin_text` is
> valid only when the spec's run-input declares stdin. The client never
> reads process stdin; required-stdin specs fail without `stdin_text`
> exactly as the CLI fails without piped input. Submission failure raises a
> typed `WeftError`; a returned `Task` is the committed receipt, submitted
> through the runtime context of the materialized spec's `weft_context`.
>
> ## Layering [PY-4]
>
> `weft.cli` and `weft.client` are adapters over `weft.commands`
> capabilities; `weft.commands` orchestrates `weft.core`. Import direction
> is one-way: `cli -> commands -> core` and `client -> commands -> core`;
> `commands` never imports an adapter; `core` never imports `commands`,
> `cli`, or `client`; the two adapters never import each other. The
> `weft.cli` and `weft.core` package initializers remain import-light
> markers. `weft.client` and `weft.commands` are public package facades;
> `weft.ext` is a public module. Each public surface owns an authoritative
> `__all__`. The `weft.commands` facade resolves exports lazily and importing
> it must not initialize unrelated command capabilities.
> Enforcement:
> `tests/architecture/test_import_boundaries.py` (pre-existing
> `test_internal_import_boundaries`, the narrowed marker guard, the preserved
> sibling-isolation guard, plus the adapter-pair, lazy-facade, and
> surface-inventory tests added with this contract). The import graph is
> mechanically one-way: `cli -> commands -> core` and
> `client -> commands -> core`; runtime direction for extension contracts is
> `core -> ext`, while `ext` may use type-checking-only core imports for
> protocol annotations. Reverse edges, `cli <-> client` edges,
> and imports from `ext` into adapters/commands are forbidden. A separate
> structural gate limits Typer callbacks to input
> decoding, one invocation of the corresponding `cmd_<full_cli_path>` export,
> output emission, and exit propagation. Semantic validation, target
> selection, and orchestration belong to the command export; formatting and
> error-to-exit translation belong to the CLI adapter.

### 7.2 `09-Implementation_Plan.md` [IP-1.0] — replace the adapter paragraph

> The public Python surfaces are `weft.client` (primary adapter),
> `weft.commands` (CLI-equivalent second adapter), and `weft.ext`
> (extension contracts). Each surface's `__all__` is its authoritative
> public-name inventory, per
> `docs/specifications/14-Python_API_Surfaces.md`. Package initializers
> that are not public facades remain import-light markers; the `weft`
> package root contains package metadata; `weft.core` is a package namespace,
> not an export surface. The `weft.commands` facade resolves exports lazily
> so public imports do not initialize unrelated command capabilities.

### 7.3 Exact edits to `02-TaskSpec.md` and `10-CLI_Interface.md`

In `02-TaskSpec.md`, replace the JSON-schema comments for `parameterization`
and `run_input` with these exact comments:

> `"parameterization": { // OPTIONAL. Submission-time TaskSpec materialization for spec submission surfaces. Declares named options and an adapter that converts those inputs into a concrete TaskSpec template before queueing.`
>
> `"run_input": { // OPTIONAL. Submission-time shaping for spec submission surfaces. Declares named options plus optional stdin and an adapter that converts those inputs into the ordinary initial work payload before queueing.`

Replace the two implementation-snapshot bullets [currently lines 284–285]
with:

> - `spec.parameterization`: Implemented. `ParameterizationSection` and
>   `ParameterizationArgumentSection` live in
>   `weft/core/taskspec/model.py`; parsing/materialization lives in
>   `weft/core/taskspec/parameterization.py`; the shared submission ordering
>   is owned by `weft/commands/submission.py` and used by `cmd_run`,
>   `WeftClient`, and `weft_django` per [PY-3].
> - `spec.run_input`: Implemented. `RunInputSection`,
>   `RunInputArgumentSection`, and `RunInputStdinSection` live in
>   `weft/core/taskspec/model.py`; adapter parsing/invocation lives in
>   `weft/core/taskspec/run_input.py`; the same shared submission path applies
>   it after materialization and before submission per [PY-3].

Replace the shaping-hook bullets [currently lines 387–391] with:

> - `spec.parameterization`: submission-surface materialization from declared
>   arguments into a concrete TaskSpec template before queueing
> - `spec.run_input`: submission-surface shaping from remaining declared
>   arguments plus explicit stdin text into the ordinary initial work payload
>   after materialization and before queueing

Also replace the explanatory bullets [currently lines 355–362] with:

> - `spec.parameterization` is static spec-owned submission behavior shared by
>   `weft run --spec`, `cmd_run`, `WeftClient`, and `weft_django`: a spec can
>   declare named arguments and an adapter that returns a concrete TaskSpec
>   template before the spawn request is queued.
> - `spec.run_input` is the next shared submission step after materialization:
>   a spec can declare named arguments plus optional adapter stdin and point
>   them at a Python adapter that returns the ordinary initial work payload
>   before the spawn request is queued.

In `10-CLI_Interface.md` [CLI-1.1.1], replace the four bullets beginning
“when the selected TaskSpec declares” (current lines 169–177 and 213–219)
with this exact rule block:

> Spec submission surfaces (`weft run --spec`, `cmd_run`, `WeftClient`, and
> `weft_django`) use one declared-argument pipeline [PY-3]. Parameterization
> consumes its declared arguments first and materializes the TaskSpec; the
> run-input adapter then consumes remaining declared arguments and explicit
> stdin text to produce the initial work payload. The CLI alone tokenizes
> dynamic long options and reads bounded piped stdin; it passes `spec_args`
> and either `run_input_stdin_text` or `work_input_text` into the shared
> command path. `run_input_stdin_text` is only for a declared run-input stdin;
> `work_input_text` preserves ordinary piped initial input when no run-input
> contract exists. No command function reads
> process stdin.

Replace the `--help` bullet [current lines 220–222] with:

> - `weft run --spec NAME|PATH --help` calls
>   `cmd_run(spec=..., describe=True)` and formats its returned
>   `RunSpecDescription`; no task is queued

### 7.4 Exact `13C-Using_Weft_With_Django.md` edits

In [DJ-2.1], replace the existing `WeftClient.submit_spec(...)` and
`WeftClient.prepare_spec(...)` bullets with:

> - `WeftClient.submit_spec(reference, *, spec_args=(), stdin_text=None, ...)`
> - `WeftClient.prepare_spec(reference, *, spec_args=(), stdin_text=None, ...)`

In [DJ-8.2], replace the two spec-reference signatures with:

> - `weft_django.submit_spec_reference(reference, *, spec_args=(), stdin_text=None, payload=None, **overrides)`
> - `weft_django.submit_spec_reference_on_commit(reference, *, spec_args=(), stdin_text=None, payload=None, **overrides)`

After “Native-submission rule,” insert:

> - `spec_args` and `stdin_text` follow [PY-3]; `payload` is rejected when the
>   resolved spec declares `run_input`, and `stdin_text` is rejected unless
>   that contract declares stdin

In [DJ-8.4], add `spec_args` and `stdin_text` to the supported per-call list.

### 7.5 Exact crosswalk, Quick Reference, and spec-index edits

In `11-CLI_Architecture_Crosswalk.md` [CLI-X1], replace the `run` row with:

> | `run` | `weft.commands.cmd_run` (lazy facade export),
> `weft/commands/run.py`, `weft/commands/submission.py` | The public command
> function owns validation and orchestration; `weft/cli/run.py` owns only
> Typer parsing and formatting of structured outcomes. |

Append this paragraph after the [CLI-X1] table:

> Every registered CLI verb maps bijectively to the public
> `weft.commands.cmd_<full_cli_path>` export defined by [PY-2]. The owning
> command function contains semantic validation and orchestration; Typer
> callbacks contain only input parsing, formatting, stream routing, and exit
> adaptation.

In `00-Quick_Reference.md`, insert after the CLI command table:

> Public Python surfaces: `weft.client`, `weft.ext`, and `weft.commands`.
> Each surface's `__all__` is authoritative. `weft.commands` mirrors every
> CLI verb through `cmd_<full_cli_path>` names; see
> [Python API Surfaces](14-Python_API_Surfaces.md) [PY-1]–[PY-4].

In `docs/specifications/README.md`, add to “Current contract”:

> - [`14-Python_API_Surfaces.md`](14-Python_API_Surfaces.md): current public
>   Python surface, command-mirroring, and adapter-layering contract

Add it as reading-order item 17 after `13-Agent_Runtime.md`.

### 7.6 Exact promotion-time plan backlinks

The new `docs/specifications/14-Python_API_Surfaces.md` ends with:

> ## Related Plans
>
> - [Python API surfaces plan](../plans/2026-08-11-python-api-surfaces-sb-contract.md)

During the same promotion commit, add that exact bullet to the existing
`Related Plans` section of each of these files:
`docs/specifications/00-Quick_Reference.md`,
`docs/specifications/02-TaskSpec.md`,
`docs/specifications/09-Implementation_Plan.md`,
`docs/specifications/10-CLI_Interface.md`,
`docs/specifications/11-CLI_Architecture_Crosswalk.md`, and
`docs/specifications/README.md`. Add the same bullet to the existing
`Backlinks` section of
`docs/specifications/13C-Using_Weft_With_Django.md`. These backlinks land in
the promotion slice; code-owner implementation mappings may land with their
own implementation slices.

## 8. Implementation Slices

### Spec-promotion slice

Apply Section 7 after independent review of this revised delta; no
implementation claims for unchanged code; record the promotion baseline.

Gate:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
```

### Slice 1: characterize all CLI command contracts

Owner files: CLI and command tests only; no production edits.

1. Generate the 41-verb inventory by recursively walking the Typer command
   tree. Commit the expected CLI-path → `cmd_<full_cli_path>` mapping as the
   firing fixture for the naming rule; do not hand-maintain a second ad hoc
   list in production code.
2. For every verb, characterize exit code and exact stdout/stderr bytes on
   its cheapest success or dry-run path and at least one usage/error path.
   Preserve current per-verb routing even where it is inconsistent today
   (manager failures, Rich spec validation, and queue newline framing are
   known traps). Record any intentional normalization as a Deviation Log row
   and proposed spec revision before implementation.
3. Characterize the CLI-only semantic branches identified in §3 so the next
   slices can prove they moved rather than disappeared: queue positional and
   cross-option validation; spec CRUD/validation orchestration; task
   selection, status process enrichment, ping projection, TID no-match,
   rendering, and watch dispatch; task-monitor once/follow mapping; manager
   missing/status exit behavior; run error translation; and every stdin/TTY
   ownership branch. Record queue-watch startup suppression separately because
   `quiet` is presentation after stream metadata becomes structured.
4. Use bounded hermetic probes for non-terminating verbs: controlled shutdown
   for manager serve, queue watch with limit 1, no-wait run plus a bounded
   session fixture, and a controlled follow-stream fixture. Do not invoke an
   unbounded foreground command in characterization.
5. Populate the Deviation Log before Slice 2 for every intentional exit or
   routing normalization. Existing manager-not-found, spec-validation, queue
   usage, and init-quiet behavior are not silently rewritten by the general
   error map.

Gate: focused CLI characterization suite plus the inventory fixture, all
green before production edits.

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/ tests/cli/ \
  tests/architecture/test_import_boundaries.py -q
```

### Slice 2: establish the 41-command facade and result contract

Owner files: `weft/commands/__init__.py`, `weft/commands/types.py`,
`weft/_exceptions.py`, `weft/client/__init__.py`, all command-owner leaves,
typing support for the lazy facade, and focused command tests.

1. Implement the exact §6.9 outcome matrix and the complete §6.10 command and
   submission error hierarchy before converting functions. Export every
   referenced type/error from `weft.commands` and the declared error additions
   from `weft.client`; add no unnamed implementation-selected outcome.
   Streaming/foreground verbs return the specified iterator/session contract
   and perform no process I/O. Move stdin/TTY extraction into the relevant CLI
   adapter in the same vertical slice as each converted queue/run export; the
   command-layer no-I/O gate runs only after those conversions, not against a
   half-migrated owner.
2. Rename every public owner to the deterministic full-path form. Existing
   short names (`cmd_tidy`, `cmd_dump`, `cmd_prune`, and peers) become private
   implementation helpers or are replaced; they are not compatibility
   exports. Split cross-verb owners such as root `cmd_status` versus
   `cmd_task_status` over shared private engines.
3. Implement the lazy facade (§6.2): 41 `cmd_*` names plus the exact public
   type/error inventory in `__all__`, one private export map with identical
   keys, cached PEP 562 resolution, normal
   `AttributeError`, derived `__dir__`, and static typing via explicit
   `TYPE_CHECKING` imports or a package stub. Package import loads no command
   leaves; per-export tests allow only the declared transitive dependency
   closure.
4. Add direct-API contract tests for every `__all__` name using the same
   fixtures as the CLI characterization. Stop if a verb cannot be represented
   without retaining semantic behavior in `weft.cli`.

Gate: command contract suite, public-surface inventory, lazy import probes,
and mypy over `weft.commands` consumers.

### Slice 3: make CLI callbacks I/O adapters only

Owner files: `weft/cli/app.py`, `weft/cli/run.py`,
`weft/cli/validate_taskspec.py`, the command-owner leaves, and CLI tests.

1. Move every semantic branch named in Slice 1 to its canonical command
   export. This includes queue write/list/watch/delete validation, all spec
   CRUD/validation orchestration, task selection/watch dispatch, and creation
   of typed run failures. Typer retains shell parsing, formatting (including
   spec CRUD and dynamic help), typed value conversion, output writes,
   exhaustive error-to-exit translation, and exit raising.
2. Make every callback call exactly the facade attribute derived from its
   registered CLI path. Do not import public command functions from leaf
   modules in `weft.cli`; use `import weft.commands as commands` so the object
   invoked by the CLI is the public API object.
3. Format structured outcomes and streaming events in the CLI, preserving the
   exact byte, stream-routing, and exit-code characterization from Slice 1.
   Re-run the 41-verb matrix to prove no terminal behavior changed. Move every
   stdin/terminal read into CLI helpers and pass bounded text or a CLI-driven
   `RunSession` explicitly; run the command-layer stdin AST gate.
4. Add the callback-purity AST gate: recursively identify registered Typer
   callbacks, derive the expected facade name, and require exactly one call to
   it. Reject direct core access and calls to command leaves or imported domain
   helpers; allow only Typer/stdlib calls and local `weft.cli` helpers whose
   modules are themselves import-boundary checked and limited to parsing,
   help, value conversion, formatting, stream emission, and `typer.Exit`.

Gate: the full CLI suite, 41-verb characterization, and callback-purity test.

### Slice 4: declared arguments through preparation, client, weft_django;
typed errors; ext inventory

Owner files: `weft/commands/submission.py`, `weft/commands/run.py`,
`weft/_exceptions.py`, `weft/client/_client.py`, `weft/client/_prepared.py`,
`weft/ext.py`, `integrations/weft_django/weft_django/client.py`, tests.

1. Build the shared seam in `prepare_spec` per §6.4's exact ordering and
   §6.5's two-context rule; retarget `_execute_spec_via_manager` and
   `cmd_run` to consume it. Characterization tests pin order equivalence
   (same resolved TaskSpec + work payload as the current run path for the
   parity corpus, including the differing-`weft_context` spec).
2. Retarget client submission to the already-created §6.6/§6.10 error
   taxonomy; `PreparedSubmission.submit()` and
   `ensure_manager_after_submission` translate at the seam; existing
   `except ValueError` compatibility preserved via dual inheritance.
3. Thread `spec_args`/`stdin_text` through client and weft_django
   (+`_on_commit`); enforce §6.4 precedence with typed errors.
4. Declare `weft.ext.__all__` per §6.8 with inventory/identity tests; move
   `SpecRunInputRequest` ownership to `weft.ext` and retarget core/builtin
   imports atomically so there is one class and no `ext -> core` runtime edge.
5. End-to-end probe: client `submit_spec(..., spec_args=...)` through a
   real manager equals the CLI invocation's resolved TaskSpec and payload.

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/test_submission.py \
  tests/commands/test_run.py tests/core/test_client.py \
  tests/core/test_spec_run_input.py tests/core/test_spec_parameterization.py \
  integrations/weft_django/tests -q
```

### Slice 5: layering and one-way import gates

Owner files: `tests/architecture/test_import_boundaries.py`, focused public
surface tests.

Extend the existing AST import graph to assert the allowed edges
`cli -> commands -> core`, `client -> commands -> core`, and `core -> ext` for
extension contracts, with only type-checking-time `ext -> core` edges for
protocol annotations. Explicitly reject runtime `ext -> core` imports,
every reverse edge, `cli <-> client`, and imports from `ext` into
cli/client/commands. Retain the existing bans on core importing
adapters/commands and commands importing adapters. Keep the
command-leaf-to-facade backedge guard unchanged.

Add the [PY-2]/[PY-1] `__all__` surface-inventory tests and the executable
Typer-tree ↔ `weft.commands.__all__` bijection. Narrow the marker guard
(:845) to `weft.cli` and `weft.core`; replace its former `weft.commands`
coverage with dedicated facade tests. Preserve the sibling-isolation behavior
at :773 and add fresh-process probes proving: package import loads no command
leaf; resolving one exported name loads its owner but not unrelated public
owners; every `__all__` name resolves; no non-`__all__` helper is promoted.
Keep the own-facade/backedge guard unchanged so leaf modules cannot depend on
the lazy facade.

Gate:

```bash
./.venv/bin/python -m pytest tests/architecture/ -q
```

### Final slice: traceability and whole-tree verification

Reciprocal docstring/spec mappings; crosswalk and Quick Reference rows;
full Section 10 verification; fresh-eyes review over the whole diff; plan
status update.

## 9. Test Disposition

New: `__all__` surface-inventory + structured-outcome tests through the public
facades; fresh-process lazy-load and sibling-isolation probes;
TID-normalized `cmd_run`/CLI parity matrix incl. stderr and usage-error
cases; declared-argument pipeline characterization (order equivalence,
precedence rejections, stdin gating, two-context case); typed-error tests;
ext inventory test; adapter-pair layering rows. Retarget: all existing
command callers to structured outcomes and typed failures. Replaced: the
`weft.commands` row of the marker guard, because the package becomes a public
facade. Weakened: none — its import-isolation purpose is retained by stronger
facade-specific tests, and the sibling-isolation and own-facade guards remain.

Adversarial probes (per runbook floors): unknown `spec_args` for a spec
with no declared-argument contract fail identically (rendered error, no
traceback) via CLI, `cmd_run`, and client; `payload` + `spec_args` conflict
raises the same typed usage error on client and `cmd_run`, while the CLI maps
it to exit 2;
mutually exclusive run modes reproduce the CLI's exact rejection;
fresh-subprocess facade-import and per-export lazy-resolution probes; no-wait receipt probe
kills the manager post-submission and confirms the TID remains valid
evidence; required-stdin spec without `stdin_text` fails without reading
the process's stdin.

## 10. Full Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths && bin/check-dom15-fixtures && bin/coalesce-check
```

## 11. Rollout and Rollback

This is **not additive**: existing short command functions change result shape
and/or public spelling, while the full 41-verb facade becomes stable at the
next release. The surface was never previously declared stable, both known
consumers are same-owner, and the canonical-contract policy forbids public
compatibility aliases — so the normalization ships as one coordinated change,
documented in release notes. Rollback before release is a revert; after
release, the CLI-derived inventory is the intended one-way stability door.
Coordination (out-of-repo, non-gating): mm-governance's
`monitor=` hotfix is already applied; after release its `submit_weft_spec`
migrates to the **application lane** (`weft_django.submit_spec_reference(
spec_args=...)` → `Task`/receipt) — preferred over the shell-parity lane,
whose printed-JSON receipt would force stdout capture in-process — and adds
an import-boundary test forbidding `weft.core` and non-inventory
`weft.commands` imports. Engram migrates when next brought current.

## 12. Independent Review Protocol

This owner-revised delta requires a focused re-review (fresh reviewer,
different family when available) before the spec-promotion slice, checking:
the §6.1 contract against every current command caller; the 41-verb naming
bijection; §6.4's ordering against run.py's actual sequence; §6.2's lazy
facade against import cycles; the callback-purity and one-way import gates;
and whether any Section 7 sentence still promises behavior no slice
implements. Repeat after Slices 3 and 4; fresh-eyes over the whole diff before
completion.

## 13. Non-Goals

- No public promotion of `execute_run`, `weft.core.*`, or `weft.helpers`.
- No CLI behavior/output changes except the explicit Deviation Log row that
  makes the already-registered task-monitor `--follow` mode actually follow;
  no new CLI verbs or options.
- No command exports beyond the canonical CLI verbs; adding, renaming, or
  removing a CLI verb and its derived `cmd_<full_cli_path>` name is one public
  contract change.
- No async client API; no new packages, frameworks, or compatibility shims.
- No mm-governance/engram code changes inside this plan.

## 14. Review Rework Log

| Review | Finding | Disposition | Plan section |
|---|---|---|---|
| Fresh-agent (Claude) 2026-08-11 | `cmd_*` inventory mischaracterized: `cmd_prune` is a 3-tuple; tuple-returners do not print (adapters route); [PY-2] as drafted matched zero functions; `cmd_run` wait mode cannot return streamed output. | Accepted. §3 corrected from code; [PY-2] rewritten to the return-payload contract with a documented streaming exception; §6.1 records the 3-tuple normalization and its breaking scope. | §3, §6.1, [PY-2], Slice 1 |
| Fresh-agent (Claude) 2026-08-11 | Package `__all__` violates the marker guard (:845), defeats sibling isolation (:773), and adds eager import cost to `weft.client`; module-vs-package breaks the SB analogy mechanically. | Accepted. §6.2 adopts the leaf-module import contract; initializer and both guards untouched; [PY-1]/[PY-2] declare inventory via spec table + leaf modules. | §6.2, [PY-1], [PY-2], Slices 1/4 |
| Fresh-agent (Claude) 2026-08-11 | Two-context divergence; parameterization must move with run-input; `payload`/`spec_args`/stdin precedence undefined; `persistent_override`/stdin threading unspecified; typed-error promise unimplemented (`RunUsageError`, bare `RuntimeError`); ext has no `__all__`; cli/run.py `cmd_run` collision; mm-gov hotfix already applied; [PY-4] mostly pre-enforced. | Accepted. §6.3–§6.8 record each decision; Slice 3 restructured; §11 and Goal 3 corrected. | §6.3–§6.8, §7, Slice 3, §11 |
| Codex (OpenAI) 2026-08-11 | Convergent with the above on: inventory/3-tuple, [PY-2] false architecture, `cmd_run` underspecification + missing cli owner files, seam-order underspecification, two contexts, precedence absence, parameterization-first, typed-error falsity, ext `__all__`, additive-rollout falsity, parity-test TID invalidity, `__all__`-guard collision. Unique: specs 02/10 scope run-input to `weft run --spec` (contradictory normative text if unedited); [DJ-8.2]/[DJ-8.4] own helper signatures; formal Spec Baseline section required; "one function per supported CLI verb" contradiction; `cmd_result` lacks CLI defaults. | Accepted. 02/10 scope generalization added (§7.3); DJ-8.x added (§7.4); formal Spec Baseline section added; [PY-2] prose scoped to "each listed name"; Slice 1 includes `cmd_result` default alignment during normalization. | Spec Baseline, §7.3, §7.4, [PY-2], Slice 1 |
| Grok (xAI, third family) 2026-08-11 | PASS with nine non-blocking findings: Slice 1 inventory test sequenced before `cmd_run` exists (F1); client parameterization-context mapping unstated (F2); `PreparedSubmission.submit()` uses `self.client.context`, risking wrong-database submission for differing-`weft_context` specs with an e2e net that asserted only TaskSpec/payload equality (F3, sharpest); non-name submit overrides unsequenced vs parameterization (F4); [PY-3] exclusivity wording misreadable (F5); `stdin_text` for no-`run_input` specs unspecified (F6); non-CLI two-tuple callers (F7); minor test duplication (F8); `cmd_tidy` always-stdout edge noted harmless (F9). | All accepted and folded in one pass: Slice 1 actions 2-3 (F1, F7, F8); §6.5 client mapping + submit()-context requirement + broker-target assertion in the parity net (F2, F3); §6.4 override sequencing and stdin/no-`run_input` rules (F4, F6); [PY-3] rewritten with contract-based exclusivity and runtime-context submission (F5). | §6.4, §6.5, [PY-3], Slice 1 |
| Codex focused re-review (v2) 2026-08-11 | §6.1 and §6.2 cleared. Remaining: §6.4 order still regrouped submit overrides (actual: `persistent_override` → revalidate → parameterize → `name` override → revalidate); parameterization-context rule (explicit `context_path` else template `weft_context`, run.py:1310) missing from §6.5; `cmd_result` defaults claimed only in the rework log, not a slice action; [PY-3] typed-error seam did not cover `prepare_spec()`-phase errors; [PY-4] "all initializers under `weft/`" false for the `weft.client`/`weft.ext` facades; `payload`+`stdin_text` precedence ambiguous for stdin-only/default-only run-input adaptation. | Accepted and fixed at each location: §6.4 rewritten to run.py's verbatim order with a do-not-regroup rule and run-input-contract-based `payload` exclusivity; §6.5 gains the parameterization-context rule and both parity cases; Slice 1 action 1 aligns `cmd_result` defaults; §6.6 extends translation to the full client-surface path including preparation; [PY-4] names the three guarded initializers and exempts the facades. | §6.4, §6.5, §6.6, [PY-2] defaults, [PY-4], Slice 1 |
| Owner correction 2026-08-11 | The official surfaces are `weft.client`, `weft.ext`, and `weft.commands`; `weft.commands.__all__` must mirror the complete CLI; command names must be derivable as `cmd_<full_cli_path>`; CLI modules own input/output parsing and formatting only; layering and one-way imports require executable gates. | Accepted as controlling intent. Replaced the reviewed nine-name leaf-module compromise with a 41-verb lazy facade, deterministic names, exact shared command ownership, CLI callback-purity gate, Typer-tree/export bijection, and one-way import graph tests. Prior reviews remain historical evidence but do not approve this materially revised v6 delta. | Goal, §3–§4, §6.1–§6.2, §6.7, [PY-1], [PY-2], [PY-4], Slices 1–5 |
| Independent v6 review 2026-08-11 | BLOCKED: public outcomes/streams were left to implementation judgment and not importable from an official surface; typed errors were incomplete/missequenced; stale formatting and exit-code contradictions remained; presentation flags/stdin/help ownership was unresolved; proposed spec edits were summaries rather than exact text; `weft.ext` inventory and layering were incomplete. | Accepted all seven findings. Added exact public types/fields/return matrix and stream lifecycle (§6.9), full command/submission error taxonomy and exit mapping (§6.10), presentation/stdin/help rules (§6.11), exact ext inventory (§6.8), exact spec edits (§7.3–§7.5), spec-index registration, and narrow ext import edges. Moved base errors/types before command conversion and kept formatting solely in CLI. | §6.1, §6.6, §6.8–§6.11, [PY-1]–[PY-4], §7.3–§7.5, Slices 2–5 |
| Independent v7 review 2026-08-11 | BLOCKED: contradictory stream return branches and no ordinary piped-input parameter; submission errors created after facade freeze and missing client exports; two exact spec edits remained incomplete; `SpecRunInputRequest` re-export created bidirectional `core <-> ext`. | Accepted all four. Made every mode-to-return branch deterministic, split `run_input_stdin_text` from `work_input_text`, moved all errors plus exact client exports into Slice 2, completed TS/Django replacements, and moved `SpecRunInputRequest` ownership into `weft.ext` so runtime direction remains `core -> ext`. | §6.1, §6.8–§6.11, [PY-2]–[PY-4], §7.3–§7.4, Slices 2–5 |
| v8 focused round 2 2026-08-11 | FAIL on two residues: an obsolete “after Slice 4” taxonomy reference and the two decoded-stdin parameters were not exempted from the CLI-option-derived signature rule. All four substantive v7 blockers were otherwise verified fixed. | Accepted. Corrected the taxonomy reference to Slice 2 and made the exact two `cmd_run` decoded-input channels a gated exception to the signature transform in both plan and proposed [PY-2]. | §4, §6.7, [PY-2] |
| v9 focused round 3 2026-08-11 | PASS. Slice-2 taxonomy sequencing and both exact `cmd_run` decoded-input exceptions verified consistently in owner decisions, proposed [PY-2], and signature gates; no new scoped defect. | No changes required. | §4, §6.7, [PY-2] |

## 15. Execution Log

| Date | Slice | Baseline / evidence | Result | Notes |
|---|---|---|---|---|
| 2026-08-11 | Plan authoring (v1) | `1edafaf27af451d6533ea0b7f65b856ff4474c39` (v0.9.95) | superseded by v2 | Initial draft; mechanical gates passed; independent reviews not yet run at delivery (process gap noted for lessons). |
| 2026-08-11 | Independent plan review (fresh Claude agent) | plan v1 + baseline code | BLOCKED | Two blocking findings (cmd_* contract mischaracterization; `__all__` vs marker/sibling guards) + feasibility findings on the seam move. All accepted; see Review Rework Log. |
| 2026-08-11 | Independent plan review (Codex / OpenAI, different family) | plan v1 + baseline code | BLOCKED | Convergent on both blockers; unique findings on spec 02/10 scope, DJ-8.x ownership, Spec Baseline section, parity-test validity. All accepted. Reviewer availability: Claude fresh-agent + Codex CLI; different-family requirement satisfied. |
| 2026-08-11 | Plan rework (v2) | this document | superseded by v3 | Both v1 reviews' findings dispositioned; §6 owner decisions added; delta expanded. |
| 2026-08-11 | Codex focused re-review (v2) | plan v2 + baseline code | BLOCKED | §6.1/§6.2 cleared; four narrower findings on §6.4 ordering, parameterization context, [PY-3] preparation-phase coverage, [PY-4] scope, `cmd_result` defaults, stdin precedence. All accepted; see Review Rework Log. |
| 2026-08-11 | Plan rework (v3) | this document | complete | v2 re-review findings fixed at each location. |
| 2026-08-11 | Codex final focused re-review (v3) | plan v3 + baseline code | BLOCKED (editorial residue only) | All five substantive v2 findings verified correct against code (§6.4 order, §6.5 parameterization context, §6.6 preparation-phase coverage, `cmd_result` defaults, payload precedence for argument/stdin/default-only adapters). One remaining wording defect in [PY-4] ("`weft.ext` initializer" — it is a module), with exact replacement text supplied by the reviewer. |
| 2026-08-11 | Plan rework (v4) | this document | complete | Reviewer-supplied [PY-4] wording applied verbatim. No judgment remained in the fix; per §12, the promotion-slice reviewer re-verifies the full delta before promotion, which is the PASS-on-record gate for this residue. |
| 2026-08-11 | Independent plan review (Grok / xAI, third family) | plan v4 + baseline code, read-only repo inspection | PASS | All factual claims verified against code. Nine non-blocking findings (see Review Rework Log), the sharpest being the `PreparedSubmission.submit()` client-context routing risk with a test net that would not have caught a wrong-database submit. Recommendation: proceed to spec promotion after folding F1-F5. |
| 2026-08-11 | Plan rework (v5) | this document | complete | Grok findings F1-F8 folded in one pass. Three-family review record complete: Claude fresh-agent (BLOCKED→fixed), Codex (BLOCKED→BLOCKED→editorial→fixed), Grok (PASS with findings→folded). Ready for the spec-promotion slice, whose reviewer re-verifies the final delta per §12. |
| 2026-08-11 | Owner-directed plan rework (v6) | this document + 41-verb Typer/command inventory | review pending | Material scope correction: full `weft.commands` CLI mirror, deterministic `cmd_<full_cli_path>` names, exact shared code, CLI I/O-only boundary, and mechanical one-way layering gates. This invalidates v5's promotion-ready claim; no spec promotion may begin until v6 receives an independent PASS and findings are dispositioned. |
| 2026-08-11 | Independent plan review (v6) | current plan before §6.8–§6.11 expansion + baseline code | BLOCKED | Seven blockers; all dispositioned in the Review Rework Log. |
| 2026-08-11 | Plan rework (v7) | this document | review pending | Outcome/error/input/help contracts and exact spec delta added. No spec promotion may begin until a fresh independent review returns PASS and any findings are dispositioned. |
| 2026-08-11 | Independent plan review (v7) | current plan + baseline code | BLOCKED | Four blockers; all dispositioned in the Review Rework Log. |
| 2026-08-11 | Plan rework (v8) | this document | review pending | Stream/input branches, export sequencing, exact spec edits, and ext ownership corrected. No spec promotion may begin until focused verification returns PASS. |
| 2026-08-11 | Focused round-2 review (v8) | accepted v7 fixes only | FAIL | Two residues; substantive fixes verified. See Review Rework Log. |
| 2026-08-11 | Plan rework (v9) | this document | review pending | Two round-2 residues corrected; focused re-verification required. |
| 2026-08-11 | Focused round-3 review (v9) | accepted v8 residue fixes only | PASS | All v6/v7 blocking findings now dispositioned and their final fixes verified. Plan is ready for the separate spec-promotion review required by §12. |
| 2026-08-12 | Full spec-promotion review | v9 plus grounded 41-verb inventory | BLOCKED→PASS | Reviewer found and verified fixes for exact type, signature, stream-data, diagnostic-data, and backlink residues. Final verdict PASS; no promotion blocker remained. |
| 2026-08-12 | Spec promotion | `fd544c33092cd8fb135098cfd43b7dc6c7aaadc3` plus reviewed Section 7 diff | complete | Promoted [PY-1]–[PY-4] into `14-Python_API_Surfaces.md`, synchronized affected specs/index/crosswalk, and added reciprocal plan backlinks. Metadata, spec hygiene, and doc-path gates passed before production edits. |
| 2026-08-12 | Command facade and adapter implementation | `20117ec` spec-promotion commit through the working implementation diff | complete | Added the exact lazy `weft.commands.__all__` inventory, structured outcomes and typed errors, all 41 canonical `cmd_<full_cli_path>` functions, explicit-input command seams, and CLI adapters that call the matching facade export once. Updated `weft.client`, `weft.ext`, Django submission plumbing, and the direct-API long-session harness to the promoted contracts. |
| 2026-08-12 | Independent implementation review | focused command/CLI/architecture evidence | BLOCKED→PASS | Review found three release blockers: `cmd_run(wait=True)` returned an already-completed session, status had a stale test seam, and task-monitor follow exposed the wrong annotation. The implementation now submits without blocking, returns a queue-backed closable `RunSession`, renders interactive output from structured events, uses the exact `CommandStream` return, and passes the corrected status seam. |
| 2026-08-12 | Final verification | full default pytest; serial spec/architecture/commands/CLI suite; full production mypy; repo-wide Ruff | complete | Default fast suite passed with two environment-specific skips. The serial boundary-heavy suite passed with one Postgres-only skip. Full production mypy, Ruff lint, Ruff format, spec metadata/hygiene, lazy facade, 41-verb bijection, callback-purity, no-command-stdin, and one-way import gates passed. |
