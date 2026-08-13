# Public API Surface Remediation: One Seam, Restored Contracts, Closed Test Floor

Status: completed
Source specs: `docs/specifications/14-Python_API_Surfaces.md` [PY-1], [PY-2], [PY-3], [PY-4]; `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] (run stdin-channel sentence; exit classes); `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-8.2], [DJ-8.4]; `docs/specifications/07-System_Invariants.md` [IMPL.1], [IMPL.7], [OBS.12a], [CTX.1], [CTX.2], [MANAGER.4].
Superseded by: none

Class: 5. Two remediation items require normative spec-14/spec-10 edits —
the `TaskControlResult` exact contract gains a `failures` field (with a new
exported `TaskControlFailure` type), and the `cmd_run`/client stdin channel
design is unified into one `stdin_text` parameter with seam-owned routing.
Both deltas are declared in Section 7 and promote before their owning
slices (strategy A). All other work aligns code and tests to the
already-promoted contract. Risky triggers also fire (public exit-code
contract, destructive-verb semantics, execution-seam consolidation), so the
hardening checklist and review-before-implementation apply.

Plan type: implementation with spec revision. Promotion strategy: **A —
in-file requirement text before link claims**.

## Spec Baseline

- Committed baseline: `8cc122993a05db2f8d1a415570ee65254a6f7b1f` (HEAD;
  contains promoted spec 14 and the implementation under remediation).
- Promotion baseline identifier: `8cc122993a05db2f8d1a415570ee65254a6f7b1f`
  plus the uncommitted spec-promotion diff in
  `docs/specifications/14-Python_API_Surfaces.md`,
  `docs/specifications/10-CLI_Interface.md`, and
  `docs/specifications/13C-Using_Weft_With_Django.md` recorded below.
- Review provenance: the 2026-08-12 implementation review (ten CONFIRMED
  findings), conformance audit, and this plan's own two pre-implementation
  reviews (Execution Log).

## 1. Goal

Finish what `2026-08-11-python-api-surfaces-sb-contract.md` promised and
the implementation partially delivered: fix the confirmed correctness
defects, restore the CLI contracts the rework regressed, complete the
single-seam architecture (one declared-argument pipeline, one selection
owner, one error-translation regime; delete the parallel paths), and build
the test floor whose absence let all of it ship green. Organizing rule,
owner-ratified: **more than one code path for the same behavior is
incorrect.**

## 2. Source Documents

Read before editing:

1. `AGENTS.md`; the `docs/agent-context/` read order; `writing-plans.md`,
   `hardening-plans.md`, `review-loops-and-agent-bootstrap.md`,
   `adversarial-acceptance-probes.md`, `testing-patterns.md`;
   `docs/lessons.md` (2026-08-11 entries).
2. Prior plan `2026-08-11-python-api-surfaces-sb-contract.md` §6 and its
   logs (this plan's Final slice adds its missing Deviation rows).
3. Spec 14 in full; spec 10's run stdin sentence; spec 07 sections cited
   above.

Comprehension questions (hardening; answer before editing):

- Where does `submit_prepared` compute the runtime context
  (`submission.py:314-335`), what does it return today, and how will the
  live `WeftContext` object reach `PreparedSubmission.submit()` without
  putting a non-JSON-safe object inside the receipt (§6.1)?
- How do the two pipelines differ today (`run.py:1394-1478` vs
  `submission.py:389-472`): rejection wording, stdin acceptance for
  no-`run_input` specs, parameterization context, name validation?
- Where do ALL deadline branches synthesize `TaskResult(status="timeout")`
  — the one-shot wait (`_result_wait.py:285` region), materialization, and
  the persistent wait, besides `result.py:794` — and which tests currently
  pin the conflation and must be retargeted, not deleted (§6.5)?
- What did fd544c3's `stop_tasks` do per-TID (visible: `continue` on
  unresolvable, success count; characterize the `_send_control` /
  control-surface failure tail before claiming any "restoration")?
- Which callers besides tests/benchmarks touch the legacy tuple commands
  and `render_run_execution_result`? (Pre-verified: none in production;
  `manager.py::stop_command` IS still used by the test harness and stays.)

## 3. Context and Key Files

Files to modify (by workstream):

- Spec delta: `docs/specifications/14-Python_API_Surfaces.md`,
  `docs/specifications/10-CLI_Interface.md`
- Correctness: `weft/client/_prepared.py`, `weft/client/_task.py`,
  `weft/client/_client.py` (provenance), `weft/commands/submission.py`,
  `weft/commands/tasks.py`, `weft/commands/result.py`,
  `weft/commands/run.py` (session fields; `RunSession.wait` timeout branch),
  `weft/commands/task_monitor.py`
- CLI restorations: `weft/cli/app.py` (incl. `_queue_command_exit`),
  `weft/cli/run.py`
- Seam: `weft/commands/run.py`, `weft/commands/submission.py`,
  `weft/client/_namespaces.py`, `weft/commands/_boundary.py`,
  `weft/commands/queue.py`, `weft/commands/manager.py`,
  `weft/commands/load.py`
- Hygiene: `weft/commands/types.py`, `weft/commands/serve.py`,
  `weft/commands/__init__.py`
- Tests: `tests/commands/`, `tests/cli/`, `tests/core/test_client.py`,
  `integrations/weft_django/tests/`, `tests/architecture/
  test_import_boundaries.py` (COMMAND_TYPES row for `TaskControlFailure`)
- Prior plan: Deviation Log rows; `docs/plans/README.md`

Shared paths — do not duplicate:

- `weft/helpers/resolve_cli_message_content`/`read_limited_stdin` own
  stdin; the CLI calls them (wrapped so their `ValueError` reaches the
  adapter's translation boundary, not a traceback).
- `typed_command_errors` is the sole translation owner after Slice 4, with
  the scoped table of §6.4 and the stream wrapper for generator-lazy
  errors.
- After Slice 3, `prepare_spec` is the only declared-argument pipeline;
  `_execute_spec_via_manager` keeps only its enqueue +
  `_run_with_managed_execution` tail (manager assurance and lifecycle
  metadata are NOT duplicated into the seam).

## 4. Invariants and Constraints

Preserve: TID format/commitment order ([MANAGER.4] path untouched;
preparation stays pre-enqueue); forward-only transitions; reserved-queue
policy; spec/io immutability (`persistent_override` shapes the pre-model
payload dict only); spawn isolation; `weft.state.*` runtime-only; facade
laziness/bijection/marker guards; the [PY-4] one-invocation callback gate
(`test_cli_callbacks_only_reach_their_matching_command_export`) — no
design may require a second facade call from an adapter callback;
`prepare_spec`'s `allow_internal_runtime` stays default-False on the
absorbed cmd path ([IMPL.7]); per-TID control classification stays inside
`stop_task`/`kill_task` and the `control_convergence` reducer ([OBS.12a]);
no new dependency; no new execution path; no compatibility shims.

Deliberate behavior changes (the authorizing record; each lands with its
firing test in the same slice):

1. Queue/manager/system usage errors exit 2 (spec 14 exit map). Scope:
   all `usage_code=1` sites AND `_queue_command_exit`'s forced exit 1,
   including its `INVALID_MESSAGE_ID` JSON envelope (envelope kept,
   exit becomes 2). Release-notes line required.
2. `client.tasks.stop_many/kill_many` select `include_terminal=False`.
3. Control sweeps become best-effort with structured failures (§6.2).
   This is **new behavior**, not restoration — fd544c3's tail must be
   characterized first, and the full matrix is defined in §6.2.
4. Wait-deadline expiry becomes a raised `CommandTimeoutError` at every
   deadline branch that today synthesizes `TaskResult(status="timeout")` —
   the one-shot wait (`weft/commands/_result_wait.py:285` region), the
   materialization deadline, and the persistent wait, plus every
   downstream interpreter that treats `status == "timeout"` as wait
   expiry — `_require_available_result`, `Task.result()`,
   `RunSession.wait`, `weft/commands/events.py:244`, and
   `weft/commands/result.py:1155` — which all stop doing so. A task's own terminal `timeout` status returns its real
   `TaskResult`; `TaskResult`'s public shape is unchanged. Tests pinning
   the conflation are retargeted. CLI: `weft result TID` for a
   terminal-timeout task prints the result and exits 124 ([IMPL.1]);
   wait-expiry keeps 124 with the timeout message. Characterize 0.9.95's
   exact bytes first; deviations get log rows.
6. `client.tasks.stop_many/kill_many` return `TaskControlResult` instead
   of `int` (§6.2; release-notes line).
5. One stdin rule on all surfaces (§6.3 / §7): `stdin_text` routed by the
   seam — declared run-input stdin when the spec declares it; initial work
   payload for specs with no `run_input` contract (preserving 0.9.95 CLI
   behavior, now uniformly on the client too, replacing its current
   rejection); rejected only when a `run_input` contract exists but
   declares no stdin.

Review gates: no drive-by refactors beyond named deletions; spec deltas
promote before their owning slices; independent review after Slice 3;
fresh-eyes before completion.

## 5. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [DJ-8.2] | The reviewed delta named only specs 14 and 10 for the unified `stdin_text` rule. | The cited Django spec still required rejection when no `run_input` contract exists, contradicting promoted [PY-3]. | All public spec-submission surfaces must share one routing rule; leaving Django stale would create two normative contracts. | §7.3; promote the matching [DJ-8.2] sentence in the same spec-promotion slice. |
| [PY-3], [DJ-8.2] | The plan did not define precedence when both `payload` and `stdin_text` are supplied without `run_input`. | The shared seam rejects the pair as a typed usage error. | Both values claim the one initial work payload; choosing either silently would make surface behavior order-dependent. | Promoted the mutual-exclusion rule in specs 14 and 13C during implementation. |
| Test floor 3 | The draft described every conflict as if every surface exposed every input. | Cross-surface identity is pinned for the shared unknown-argument case; stdin routing is pinned on each surface; `payload` conflicts are pinned on client/shared/Django surfaces only. | CLI and `cmd_run` intentionally expose no `payload` argument, so a CLI payload-conflict probe cannot be constructed without inventing a public option. | No spec change; this is a correction to the verification matrix. |
| Test floors 2 and 9 | The draft asked one test to compare client, CLI, and Django runtime objects directly. | Explicit/discovery × client/`cmd_run` is one real-manager wrong-broker matrix; CLI and Django real-manager declared-argument probes independently assert the same payload contract. | Django uses its configured process-global context and subprocess CLI cannot return a live `Task` object. Canonical behavior is proved at each available boundary without a test-only cross-process object bridge. | No spec change; retain the per-surface public contracts. |
| [PY-2], [CLI-1.2.3] | The first open-issues repair made every known terminal task controllable so `kill` could reap runtime residue. | Terminal tasks reject `stop` before a control write, while `kill` retains the cleanup and escalation path. | STOP cannot change an already terminal task and otherwise leaves an undrainable control message; KILL has a distinct process-reaping duty. | §7.4; promote the verb-specific rule in specs 14 and 10. |

## 6. Owner Decisions Recorded in This Plan

### 6.1 Task handles bind the submission's runtime context

`submit_prepared` returns its result enriched with the resolved runtime
context: the **receipt** gains a JSON-safe `context_root: str` field (for
rendering/record), and the internal return to the client path carries the
**live `WeftContext` object** alongside it, so no caller-supplied backend
configuration is lost to a rebuild and no context object enters a
renderable dataclass. `PreparedSubmission.submit()` constructs
`Task(client, tid, context=runtime_context)`; `Task` gains a defaulted
`context` override (all three existing two-positional call sites
unaffected). Scope note: `TasksNamespace` verbs (`stop(tid)` etc.) continue
to use the client context by design — they operate on TIDs the caller
addresses in the client's own project; one probe pins that
`Task`-handle observation and namespace-verb addressing are both
documented behaviors (test floor 2 and its scope assertion).

### 6.2 One selection owner; best-effort sweeps with structured failures

`_task_control_result` gains a `tids: Sequence[str] | None` parameter and
becomes the single selection/dispatch owner; `cmd_task_stop`/`cmd_task_kill`
pass single-TID/all/pattern as today; `stop_many`/`kill_many` delegate
with their TID list to the **shared internal owner** (not the `cmd_*`
wrappers, whose spec-pinned signatures stay single-TID), deleting the
hand-rolled blocks (`_namespaces.py:141-148, 162-169`). Dispatch matrix:

- attempt every selected TID; collect per-TID failures as
  `TaskControlFailure(tid, error)` where `error: str` is the failure's
  rendered message and `error_type: str` its exception class name
  (JSON-safe; spec delta §7.1);
- empty selection (`requested=()`): success, zero counts, exit 0 —
  preserving "Stopped 0 task(s)";
- some accepted: success; CLI renders "Stopped k of n; m failed: …"
  (rendering added in the same slice — cli/app.py:1384 region);
- zero accepted AND at least one requested: raise `ControlRejected` whose
  message names the first failure and whose documented `failures`
  attribute carries the full tuple (spec delta §7.1);
- **client return type**: `stop_many`/`kill_many` return the same
  `TaskControlResult` (an authorized client API change from the current
  `int`, recorded in §4 and the release-notes line; callers wanting the
  old count read `len(result.accepted)`).

### 6.3 One declared-argument pipeline; one stdin parameter

`cmd_run`'s two stdin parameters (`run_input_stdin_text`,
`work_input_text`) are replaced by one `stdin_text` (spec delta §7.2);
routing is spec-aware and owned by the seam per §4 rule 5. This dissolves
the adapter-knowledge problem: `weft/cli/app.py` passes piped stdin as
`stdin_text` unconditionally and keeps exactly one facade invocation
([PY-4] gate). `prepare_spec` absorbs the cmd path's rules — the
parameterization-context rule (explicit context else template
`weft_context`; provenance recorded at client construction:
path-constructed and prebuilt-context (`from_weft_context`/
`WeftClient(context=…)`) count as explicit, discovery does not — recorded
here as the mapping decision), `persistent_override` pre-parameterization
shaping, and name validation — with a characterization test proving
order-equivalence against the current cmd path before the cutover.
`_execute_spec_via_manager` reduces to seam + enqueue +
`_run_with_managed_execution` tail, and Slice 3 re-runs Slice 1's
session-field rendering tests (both touch `run.py`). Rejection wording
unifies on the seam; message changes enumerated in the release-notes line.
`cmd_load` delegates to `cmd_system_load`'s flow (mirroring dump).

### 6.4 One error-translation regime; exit map to spec

`typed_command_errors` decorates every exported `cmd_*` (manager and
builtins included) with a **scoped translation table** documented in
`_boundary.py`: the queue-only `TypeError → CommandUsageError` rule stays
scoped to queue commands (a global rule would misclassify programmer
defects as usage errors); everything else unifies. Streaming commands get
a closable stream wrapper that applies the same table during iteration
(generator-lazy errors currently escape the decorator; queue-watch and
task-monitor follow are wrapped, and follow's missing `try/finally`
closure at `app.py:1557` is fixed here). `_command_failure` and per-site
translation are deleted. CLI: remove all `usage_code=1` sites AND
`_queue_command_exit`'s independent exit-1 forcing; `RunResolutionError`
string dispatch becomes `isinstance`.

### 6.5 Deletions and hygiene

Delete: legacy tuple command layer (`queue.py:1477-1898`,
`manager.py:365-441` `list_command`/`status_command` family — `stop_command`
STAYS, the harness consumes it), tests/benchmarks retargeted;
`render_run_execution_result` (cli/run.py's renderer is the sole owner —
it is CLI presentation and stays in the adapter); `_stdin_message`
(replaced by the helpers with adapter-boundary wrapping and the
context-resolved size bound); `_consume_outbox_task_result` dead code; the
tautological facade assert (the spec-inventory architecture test carries
the double-entry load alone — accepted, noted); `run.py.__all__`'s seven
underscore-privates. Restore `--quiet` suppression forwarding on
`alias add`. Add the three missing dataclass docstrings,
`serve.py.__all__`, and [PY-2] in `types.py`'s module docstring. Retarget
(never delete) the tests that pinned the timeout conflation and the
tuple layer.

## 7. Proposed Spec Delta

| Spec file | Strategy | Sections |
|---|---|---|
| `docs/specifications/14-Python_API_Surfaces.md` | A | [PY-2] `TaskControlResult` contract + exported-type list; [PY-2] `cmd_run` signature exceptions; [PY-3] stdin rule |
| `docs/specifications/10-CLI_Interface.md` | A | [CLI-1.1.1] run stdin sentence; [CLI-1.2.3] terminal task-control distinction |
| `docs/specifications/13C-Using_Weft_With_Django.md` | A | [DJ-8.2] native-submission stdin rule |

### 7.1 [PY-2] task-control outcome

> `TaskControlResult(command, requested, accepted, failures, snapshots)`.
> `failures` is a tuple of `TaskControlFailure(tid, error, error_type)`
> records — `error` the rendered failure message, `error_type` the
> exception class name — for selected tasks the control attempt could not
> confirm; `accepted` and `failures` partition `requested`. An empty
> selection is a successful zero-count outcome. The command raises only
> when at least one task was requested and none was accepted; the raised
> `ControlRejected` carries the full tuple on its documented `failures`
> attribute.

`TaskControlFailure` joins the exported-type list (and the facade
inventory + `COMMAND_TYPES` architecture row in the same slice); the
`ControlRejected.failures` attribute is part of the same delta.

### 7.2 [PY-2]/[PY-3]/[CLI-1.1.1] one stdin parameter

> `cmd_run` and client spec submission accept a single
> `stdin_text: str | None`. The submission seam routes it: to the declared
> run-input stdin when the spec's `run_input` declares stdin; as the
> initial work payload when the spec has no `run_input` contract; rejected
> with a typed usage error when a `run_input` contract exists but declares
> no stdin. Surfaces never read process stdin; the CLI adapter reads its
> piped stdin once and forwards it as `stdin_text`.

Spec 10's sentence is replaced to match. (This supersedes the prior plan's
two-channel §6.11 design; its Deviation row is added by the Final slice.)

### 7.3 [DJ-8.2] native-submission stdin rule

> `spec_args` and `stdin_text` follow [PY-3]; `payload` is rejected when the
> resolved spec declares `run_input`. The shared submission seam routes
> `stdin_text` through declared run-input stdin when available, as the initial
> work payload when no `run_input` contract exists, and rejects it when a
> `run_input` contract exists but declares no stdin.

### 7.4 [PY-2]/[CLI-1.2.3] terminal task-control distinction

> A known terminal task rejects `stop` with `ControlRejected` and its existing
> terminal status before any control write. The same task remains eligible for
> `kill` because KILL owns runtime-residue escalation. If no live runtime can be
> proven, the kill rejection names the existing terminal status and says that
> no live runtime was found.

## 8. Implementation Slices

### Spec-promotion slice

Apply Section 7 after this plan's reviews PASS; gates:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
```

### Slice 1: standalone correctness

§6.2 matrix (shared owner + `failures` + CLI rendering + client
delegation for `stop_many`/`kill_many` — the client half moves here since
the shared owner lands here); single-pass consume in
`_collect_all_task_results`; timeout discriminant: the deadline-expiry
branch (`result.py:794` region) raises instead of synthesizing, terminal
timeout returns the result, applied uniformly to `_require_available_result`,
`Task.result()`, and `RunSession.wait` (`run.py:422`); follow-mode
high-water mark; session `wait()` preserves
`submitted_payload`/`manager_started_payload`/`error_prefix`.
Tests: floor 5, 6, 7 (incl. the session and the CLI 124 pins), 8(follow).

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_commands.py \
  tests/commands/test_result.py tests/commands/test_task_monitor.py \
  tests/commands/test_run.py tests/commands/test_run_public.py \
  tests/core/test_client.py -q
```

### Slice 2: CLI contract restorations

Stdin helper reinstatement (boundary-wrapped, context-resolved bound);
wait-phase try scope; Ctrl-C on the two streaming verbs; `task status
--watch` format restored; `--quiet` forwarding; exit-map sweep incl.
`_queue_command_exit` (§6.4 CLI half); `isinstance` dispatch; flake fix
(floor 10). Tests: floor 4, 8(Ctrl-C/format), stdin probes.

Gate:

```bash
./.venv/bin/python -m pytest tests/cli/ -q
```

### Slice 3: the seam (independent review after this slice)

§6.1 context binding; §6.3 single pipeline + one-stdin cutover +
provenance recording; re-run Slice 1 session-field tests. Tests: floor
1, 2, 3, 9.

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/test_submission.py \
  tests/commands/test_run.py tests/commands/test_run_public.py \
  tests/core/test_client.py tests/cli/test_cli_run.py \
  integrations/weft_django/tests -q
```

### Slice 4: unification deletions

§6.4 decorator/table/stream wrapper + deletions; §6.5 remainder;
architecture rows (`TaskControlFailure`; deleted-path absence).

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/ tests/architecture/ \
  tests/test_long_session_surface_benchmark.py -q
```

### Final slice: honesty, traceability, verification

Prior plan's Deviation rows (shared seam; §6.3 rename; parameterization
context; stdin channels — superseded by §7.2; exit map) + Execution Log
correction row; module docstrings/mappings; release-notes line (exit
codes, unified messages, stdin rule, sweep reporting); full verification:

```bash
. ./.envrc
./.venv/bin/python -m pytest && ./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths && bin/check-dom15-fixtures && bin/coalesce-check
```

Fresh-eyes review over the whole diff before completion.

## 9. Test Floor (all firing; owning slices marked)

1. CLI/`cmd_run` parity matrix (S3): CLI subprocess bytes vs `cmd_run`
   outcome **rendered through the CLI's own renderer**, TID/timestamp
   normalized; stdout+stderr+exit equality. (`cmd_run` itself emits
   nothing; parity is defined through the shared rendering.)
2. Wrong-database net (S3): differing-`weft_context` spec, explicit and
   discovery, client and `cmd_run`; assert broker target AND
   `Task.result()` observation; plus the §6.1 namespace-verb scope probe.
3. Cross-surface identity probes (S3): unknown `spec_args`;
   `payload`+`run_input` conflict; `stdin_text` under each of the three
   §7.2 routing cases — identical typed error / rendered error + exit.
4. Exit-map firing test (S2): parameterized over every exported `cmd_*`
   through the real adapters: usage→2, timeout→124, other CommandError→1.
5. Sweep matrix (S1): empty selection; one induced `ControlRejected`
   among N (others attempted, failures reported, exit 0); all-fail
   (typed raise); both surfaces.
6. Consume-mode structural single-pass proof (S1). (The concurrent-writer
   probe is optional hardening, not floor — flake-prone duplicate.)
7. Terminal-timeout vs wait-expiry (S1): command layer, client
   `Task.result()`, `RunSession.wait`, and CLI 124-with-result pin.
8. Watch/follow (S1/S2): no-duplicate follow under `--no-checkpoint` and
   `--since`; Ctrl-C exit 0 via real SIGINT; `--watch` line format; disk
   sink `--follow` interruption leaves no torn record (single write per
   record — fix the two-write append in the same slice, floor-owned S1).
9. Client/weft_django `spec_args` e2e (S3): real manager; resolved
   TaskSpec + payload + broker target equal to CLI.
10. Flake fix (S2): persistent-breach memory-limit child.

## 10. Explicitly Out of Scope

- Inventory tiering (owner release decision).
- [PY-4] ext-edge wording errata beyond the prior plan's Deviation row.
- New capabilities/verbs; mm-governance/engram migration.

## 11. Independent Review Protocol

Fresh same-family + different-family review BEFORE implementation (rows
below); repeat after Slice 3; fresh-eyes before completion.

## 12. Review Rework Log

| Review | Finding | Disposition | Plan section |
|---|---|---|---|
| Codex (OpenAI) v1 2026-08-12 | "Spec delta: none" false (`TaskControlResult` exact contract); sweep semantics matrix undefined and "restoration" misclaimed; timeout fix lacks a discriminant (both kinds collapse at result.py:794; `Task.result()` bypasses helper); receipt-root rebuild loses caller config; `from_context` erases provenance; `prepare_spec` cannot see the collapsed stdin channels; `_run_with_managed_execution` must be retained; decorator misses generator-lazy errors and would misclassify global `TypeError`; `_queue_command_exit` forces exit 1 independently; §4 authorization narrower than the sweep; stdin helper `ValueError` would escape the boundary; parity test literally impossible against a non-printing `cmd_run`; §8 probes unowned; follow stream lacks try/finally; disk sink two-write tear; 0.9.95-restoration unenumerable; `stop_command` has a harness caller. | All accepted: Class 5 + §7 deltas; §6.2 matrix + characterization; §4.4 discriminant design across all three consumers; §6.1 live-context + JSON-safe root; §6.3 provenance mapping incl. prebuilt; §7.2 one-stdin design dissolves the channel problem; §6.3 tail retention; §6.4 scoped table + stream wrapper + closure fix; §4.1 widened + `_queue_command_exit`; floor 1 reworded to rendered-outcome parity; floors own every probe; floor 8 disk-sink fix; §4 enumerated changes replace the broad restoration claim; §6.5 keeps `stop_command`. | metadata, §4, §6, §7, §9 |
| Codex focused re-review (v2) 2026-08-12 | Sweep public contract underdeclared: client `stop_many`/`kill_many` return type (`int` vs `TaskControlResult`); `TaskControlFailure.error` exact type; "failures attached" exception shape undefined. Timeout discriminant missed the actual one-shot deadline branch (`_result_wait.py:285`) plus materialization and persistent-wait sites; downstream consumers must stop reading `status=="timeout"` as wait expiry. | Accepted: §6.2 declares `TaskControlResult` return (authorized §4.6, release-noted) and `TaskControlFailure(tid, error: str, error_type: str)`; §7.1 adds `ControlRejected.failures`; §4.4 enumerates all three raise sites and the downstream interpreters; comprehension question updated. | §4.4, §4.6, §6.2, §7.1 |
| Codex final focused re-review (v3) 2026-08-12 | Timeout-conflation consumer enumeration incomplete: `events.py:244` and `result.py:1155` also interpret `status=="timeout"` as wait expiry. Sweep contract and producer enumeration verified resolved. | Accepted; both sites added to §4.4's interpreter list verbatim. | §4.4 |
| Fresh-agent (Claude) v1 2026-08-12 | B1: stdin rules jointly unsatisfiable under the [PY-4] one-invocation gate (adapter cannot know run-input-ness; every workaround fails a preserved constraint). B2: `TaskControlFailure` is a normative spec-14 change scheduled as plain work. F1 delegation mechanism (cmd wrappers are single-TID); F2 empty-selection rule; F3 conflation also in `RunSession.wait` + discriminant mechanism unnamed; F4 CLI 124 pin; F5 `_queue_command_exit` JSON envelope; F6 global `TypeError` misclassification; F7 receipt root vs object + prebuilt-context provenance; F8 namespace-verb residual asymmetry; F9 concurrency probe duplicative. | B1 resolved by design change: §7.2 single `stdin_text` with seam-owned routing — the adapter needs no spec knowledge and keeps one invocation; spec delta declared. B2: §7.1 delta declared; Class 5. F1: shared internal owner gains `tids` param. F2: §6.2 matrix. F3/F4: §4.4 + floor 7. F5: §4.1. F6: §6.4 scoped table. F7: §6.1 dual return + prebuilt=explicit recorded. F8: §6.1 scope note + floor 2 probe. F9: floor 6 demoted probe. | §4, §6.1-§6.4, §7, §9 |
| Independent post-Slice-3 review 2026-08-12 | BLOCKED: absorbed command payload routing lost the interactive `close` marker; run-input adapters received the unresolved template context; shared usage errors were rewrapped; required seam probes were incomplete; CLI imported a command leaf for redundant type discrimination. | Resolved before Slice 4: exact command payload semantics moved into `prepare_spec`; adapters receive the resolved absolute runtime root; seam errors retain `CommandUsageError` identity; focused parity/context tests added; leaf import removed. Slice-3 gate rerun is recorded below. | §6.3, §7.2, §9 floors 1–3 |
| High-recall post-completion review 2026-08-12 | BLOCKED: consume-all drained incomplete stream chunks; unknown single-task stop reported success; plain names used endpoint validation; `prepare_spec` catches mismasked adapter failures; runtime-root expansion had drifted; mixed sweep selectors broadened destructive scope; empty client sweeps raised; load alias-conflict exit 3 was removed despite spec 10; dead spec-pipeline helpers remained; queue argv decoding built a broker context. The review also identified missing changelog and review-log disclosure. | All accepted and repaired with firing regressions. Consume-all now peeks one observed set and drains it only after a complete result is present; stop and kill share the existence proof; exception scopes follow stage ownership; one runtime-root helper owns expansion; selector contradictions reject and empty client sweeps return zero results; exit 3 is restored; dead spec helpers are deleted; message decoding uses config without context construction and reports usage errors. Specs 10/14, CHANGELOG, this row, and the execution log were synchronized. | §4, §6.2–§6.5, §9 |
| Independent re-review of high-recall repairs 2026-08-12 | BLOCKED twice: first, client all/pattern sweeps converted omitted TIDs to an explicit empty selector, adapter import/runtime failures leaked, and task-control prechecks discarded a live broker context; second, terminal-snapshot fallback, control convergence, and TID resolution retained the same context reduction. | All accepted. Selector forwarding now distinguishes selectorless no-op from all/pattern sweeps; every ordinary run-input adapter failure becomes a chained `SubmissionValidationError`; every shared task-status/TID path passes the live `WeftContext`. Added firing probes for both verbs and selector forms, five adapter failure families, precheck, terminal fallback, convergence polling, and TID resolution. Final exact re-review: PASS, no adjacent P1/P2. | §6.1–§6.3, §9 |
| Open-issues implementation review 2026-08-13 | BLOCKED: terminal-known controls were rejected before cleanup; typed adapter errors were rewrapped; queue stdin sizing used env-only config; mixed completed/partial outboxes lost the partial suffix. The reported completion-race issue was the same terminal-known guard when evidence remained, but literal evidence disappearance remains a failure by [OBS.11a]. The reported grace-timeout message was not reproducible; a narrower remaining-budget diagnostic was confirmed. | Confirmed findings accepted test-first. Unknown-only control precheck restored; typed adapter errors pass through both adapter stages; command-layer size validation uses resolved context and stdin resolves context without database initialization; result-all consumes only through the last complete boundary; timeout diagnostics use the caller value. Specs, changelog, plan, and lesson synchronized. | §4, §6.2–§6.4, §9 |
| Independent open-issues repair review 2026-08-13 | BLOCKED: ordinary parameterization-adapter `RuntimeError` still escaped, and queue stdin context-resolution `OSError` escaped the CLI error boundary. | Accepted. Both adapter stages now preserve typed `WeftError` and wrap every ordinary `Exception`; stdin context/read failures become `CommandExecutionError`. Added parameterization RuntimeError and both queue-verb context-failure probes. | §6.3–§6.4, §9 |
| Updated open-issues review: OPEN-7 2026-08-13 | BLOCKED: the unknown-only shared guard let STOP report success and write undrainable control for a known terminal task. KILL still needs terminal-known access for runtime-residue escalation, so the prior all-verbs repair moved the defect instead of eliminating it. PROCESS-1 also found that `completed` was dishonest before commit. | Accepted. The precheck returns task evidence; `stop_task` rejects terminal status with `ControlRejected` before delegation, while `kill_task` retains convergence and reports terminal/no-runtime failure honestly. The completion-race test now records a sweep failure instead of acceptance. Specs 10/14 and CHANGELOG state the verb split. Plan and index close in the landing commit. | §5, §7.4, §9 |
| Independent OPEN-7 repair review 2026-08-13 | BLOCKED only on plan honesty: implementation, tests, specs, and CHANGELOG passed, but the rework and execution logs did not record the later STOP/KILL distinction. | Accepted. Added this review disposition and the matching execution row; draft metadata was retained during rework and closes with the landing commit. | §12–§13 |

## 13. Execution Log

| Date | Slice | Baseline / evidence | Result | Notes |
|---|---|---|---|---|
| 2026-08-12 | Plan authoring (v1) | `8cc122993a05db2f8d1a415570ee65254a6f7b1f`; implementation review (10 CONFIRMED) + conformance audit | superseded by v2 | Drafted with reviews launched before handoff per the 2026-08-11 lesson. |
| 2026-08-12 | Independent plan review ×2 (Codex; fresh Claude) | plan v1 + baseline code | BLOCKED / BLOCKED | Convergent blockers: undeclared spec deltas (TaskControlResult; stdin channels). Full finding sets and dispositions in the Review Rework Log. |
| 2026-08-12 | Plan rework (v2) | this document | superseded by v3 | Both v1 finding sets dispositioned; reclassified Class 5 with declared §7 deltas. |
| 2026-08-12 | Codex focused re-review (v2) | plan v2 + baseline code | BLOCKED (two narrow items) | Single-stdin design PASSED (adapter-knowledge problem dissolved within the one-invocation gate; [PY-3] routing consistent). Remaining: sweep return-type/failure-shape underdeclared (client `int` → `TaskControlResult` is an API change; `TaskControlFailure.error` type and the raised exception's failure attachment undefined); timeout discriminant missed the one-shot deadline branch at `_result_wait.py:285` and the materialization/persistent-wait sites. |
| 2026-08-12 | Plan rework (v3) | this document | superseded by v4 | §6.2 return-type + failure shapes declared; §4.4 raise sites enumerated. |
| 2026-08-12 | Codex final focused re-review (v3) | plan v3 + baseline code | BLOCKED (enumeration residue only) | Sweep contract resolved (no callers depend on the `int` return — verified incl. weft_django and tests); timeout producers complete (materialization `result.py:327-328/815`, one-shot `_result_wait.py:285-298`, persistent `result.py:711-737`). Residue: two unenumerated conflation consumers (`events.py:244`, `result.py:1155`), reviewer-supplied. |
| 2026-08-12 | Plan rework (v4) | this document | complete | Reviewer-supplied consumer sites added to §4.4 verbatim. Three-round arc closed: v1 dual-BLOCKED → v2 (design rework; stdin PASS) → v3 (contracts declared) → v4 (enumeration complete). Per §11, the promotion-slice reviewer re-verifies the final delta before promotion — the standing PASS-on-record gate. |
| 2026-08-12 | Final plan review (v4) | plan v4 + baseline code + proposed spec delta | PASS | Owner-confirmed final pass. No promotion blocker remains; implementation may proceed in the dependency order in §8. |
| 2026-08-12 | Spec promotion | `8cc122993a05db2f8d1a415570ee65254a6f7b1f` plus the uncommitted diff in specs 14, 10, and 13C | complete | Promoted §7.1–§7.3 using strategy A. Added the [DJ-8.2] synchronization as a declared deviation. Plan metadata, spec hygiene, and doc-path gates passed. |
| 2026-08-12 | Slices 1–2 | focused command, client, result, monitor, run, and CLI suites | complete | Restored sweep aggregation, timeout discrimination, one-pass consume-all, session field preservation, monitor high-water/atomic writes, bounded CLI stdin, wait translation, Ctrl-C, stable watch rendering, alias quiet, and exit mapping. |
| 2026-08-12 | Independent post-Slice-3 review | implementation diff + required seam | BLOCKED then resolved | Five findings recorded in the Review Rework Log. Corrected command payload parity, adapter runtime root, error identity, test coverage, and the CLI leaf import before continuing. |
| 2026-08-12 | Slice 4 focused gate | boundary, queue, manager, run, and architecture suites | complete | Unified eager/lazy typed errors; removed tuple queue helpers, manager list/status tuple helpers, and commands-layer run renderer; structured tests and deleted-name architecture gates pass. |
| 2026-08-12 | Fresh-eyes implementation review | whole shared-tree diff | BLOCKED then resolved | Repaired all-fail task-control CLI rendering, task-monitor follow closure, submission validation taxonomy, unknown override translation, iterator cleanup, runtime-root expansion, and deterministic SIGINT readiness. Added the missing parity, context, timeout, and cross-surface probes; focused review tests pass. |
| 2026-08-12 | Full verification | default and all-marker pytest; mypy; Ruff | PASS | Default suite and `-m ""` suite pass (environment/provider-only skips); mypy passes 187 source files; Ruff check and format check pass. Documentation gates recorded in the final row after rerun. |
| 2026-08-12 | Documentation and completion gates | plan metadata, spec hygiene, doc paths, DOM-15 fixtures, coalesce check; fresh-eyes whole-diff review | PASS | All documentation gates pass. Fresh-eyes review found no remaining P1/P2 implementation defect after the final repairs and test-floor additions. Plan and index marked completed. |
| 2026-08-12 | High-recall post-completion remediation | ten confirmed findings supplied after the completion claim; two independent repair re-reviews | PASS | Reopened the implementation evidence and repaired all ten findings through vertical red/green probes. Two re-reviews exposed and then closed adjacent selector, adapter-taxonomy, and live-context residues. Default and all-marker pytest pass; Ruff check/format pass; mypy passes 187 source files; plan metadata, spec hygiene, doc paths, DOM-15 fixtures, and coalesce checks pass. Final exact fresh-eyes review: PASS with no adjacent P1/P2. |
| 2026-08-13 | Open-issues fix wave | six reported findings plus independent validation and repair re-review | PASS | Four findings confirmed, one qualified as the terminal-known form of the first, and one rejected as stated with a narrower diagnostic fixed. The first fresh review exposed two adjacent exception-boundary gaps; both were repaired with firing tests, and the second exact review passed with no P1/P2. Fresh default and all-marker pytest pass; Ruff check/format, mypy (187 sources), plan/spec/doc gates, DOM-15, coalesce, and `git diff --check` pass. |
| 2026-08-13 | OPEN-7 terminal STOP/KILL split and closure | Updated open-issues report; vertical red/green command tests; real CLI no-residue probe; independent scoped review | complete | Terminal STOP rejects before control, terminal KILL still reaches cleanup/escalation, and no-runtime KILL wording is honest. Focused, default, and all-marker pytest pass; Ruff check/format, mypy (187 sources), plan/spec/doc gates, DOM-15, coalesce, and `git diff --check` pass. The landing commit records closure. |
