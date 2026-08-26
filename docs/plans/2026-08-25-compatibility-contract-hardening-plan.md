# Compatibility Contract Hardening Release Plan

Status: completed
Source specs: docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/10-CLI_Interface.md [CLI-1.2.3], [CLI-4]; docs/specifications/10B-Builtin_TaskSpecs.md `probe-agents`; docs/specifications/13-Agent_Runtime.md [AR-7]
Superseded by: none

Class: 5: spec-changing and risky. This changes resolved TaskSpec TID
acceptance and compatibility behavior at CLI, provider, and extension
boundaries. Independent review and the hardening checklist are required.

Review state: implementation passed scoped reviews and the outside
completed-work review; closed by the implementation commit.

## Goal

Remove the remaining representation-only constraints found by the August 25
audit before the next release. Keep the change small: validate the exact IDs
and calls Weft consumes, replace only demonstrated prose classifiers, and
relax only tests that pin non-contractual presentation. Do not introduce a
general validation framework, error taxonomy, compatibility layer, or new
public API.

The Monitor schema repair in `6cfb81ef266d26d58e4d476272c351f1944a7bc1`
is complete and out of scope.

## Source Documents

- `docs/specifications/02-TaskSpec.md` [TS-1]: TID shape, assignment, and
  immutability.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2]: canonical
  SimpleBroker exact message-ID normalization.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.3], [CLI-4]: task-status
  Monitor fallback and queue JSON errors.
- `docs/specifications/10B-Builtin_TaskSpecs.md` `probe-agents`: exact builtin
  diagnostic output.
- `docs/specifications/13-Agent_Runtime.md` [AR-7]: provider diagnostics and
  Microsandbox runner behavior.
- `docs/agent-context/runbooks/testing-patterns.md` Pattern 8: structured
  fields exact, prose by stable substring.
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md`: truthful exit
  classes, structured codes, and no user-facing tracebacks.
- `docs/lessons.md`, "2026-08-25 Migration Fixtures Must Come From Release
  Lineage": validate semantic dependencies, not incidental representation.

## Context and Key Files

### Required reading

- `weft/helpers/message_ids.py`: `normalize_exact_message_id()` already owns
  canonical 19-character ASCII form and delegates range validation to
  SimpleBroker. Reuse it.
- `weft/core/taskspec/model.py:TaskSpec.validate_tid()`: currently adds the
  unrelated 2020 and local-time-plus-one-year checks.
- `extensions/weft_microsandbox/tests/test_runtime_adapter.py`: compares the
  full installed SDK parameter inventory even though `_runtime.py` only makes
  one positional `get/remove` call.
- `weft/core/agents/provider_cli/probes.py`: classifies two English help
  phrases as OpenCode `run` support.
- `weft/commands/queue.py::_exact_message_id()` and
  `weft/cli/app.py::_queue_command_exit()`: CLI JSON selection parses exception
  prose.
- `weft/core/monitor/store.py:MonitorStore.get_task()` and
  `weft/commands/tasks.py::_monitor_store_task_snapshot()`: an absent Monitor
  store is recognized through backend missing-table text.
- `weft/context.py:normalize_backend_resolution_error()`: exact upstream
  SimpleBroker messages are recognized only to substitute a Weft-specific
  install sentence.
- `tests/cli/test_status.py` and
  `tests/architecture/test_import_boundaries.py`: one test pins full plain-text
  layout; another pins a redundant magic command count in addition to the
  actual set bijection.

### Files expected to change

- Specs and release docs: the four source specs above, `CHANGELOG.md`,
  `docs/lessons.md`, this plan, and `docs/plans/README.md`.
- TIDs: `weft/core/taskspec/model.py`, `tests/taskspec/test_taskspec.py`,
  `tests/taskspec/test_taskspec_properties.py`,
  `tests/helpers/hypothesis_strategies.py`, and the new focused helper test
  `tests/helpers/test_message_ids.py`.
- External capabilities: the two Microsandbox tests above,
  `weft/core/agents/provider_cli/probes.py`, `weft/builtins/agent_probe.py`,
  `weft/core/agents/provider_cli/registry.py`,
  `tests/core/test_agent_validation.py`,
  `tests/core/test_provider_cli_backend.py`, the provider fixture, and the new
  focused builtin test `tests/core/test_builtin_agent_probe.py`.
- Direct prose classifiers: `weft/commands/queue.py`, `weft/cli/app.py`,
  `weft/core/monitor/store.py`, `weft/commands/tasks.py`,
  `weft/context.py`, `weft/commands/init.py`, and their focused tests.
- Test cleanup: `tests/cli/test_status.py`,
  `tests/architecture/test_import_boundaries.py`, plus the private-validation
  tests named in Task 4 only where they pin full prose.

### Comprehension checks

1. Why does TaskSpec own canonical TID representation but not allocation-clock
   plausibility or broker high-water policy?
2. Why does `inspect.signature(method).bind("probe")` express the production
   Microsandbox requirement without freezing added optional parameters?
3. Why must all Monitor tables absent mean no fallback evidence, while a
   partial or unreadable schema must remain degraded `unknown`?
4. Why can `run --help` report probe mechanics but not semantic provider
   support?

## Invariants and Constraints

- Resolved TIDs stay immutable canonical nonzero 19-character ASCII exact
  message IDs and remain the spawn-request message ID for the whole task
  lifecycle.
- Template TaskSpecs may omit TID; resolved TaskSpecs may not.
- Use `normalize_exact_message_id()`. Do not copy SimpleBroker limits or import
  private SimpleBroker constants.
- Broker insertion/import continues to own duplicate, allocation-floor, and
  skew policy. This plan does not alter those operations.
- Queue names, state transitions, `spec`/`io` immutability, and reserved policy
  do not change.
- `INVALID_MESSAGE_ID` retains exit 2, empty stdout, and its existing JSON
  code. Fix its selector with one specific internal exception subtype, not a
  generic code registry or new public exception surface.
- Task-status Monitor fallback remains read-only. It must not initialize,
  migrate, or repair the store.
- A wholly absent Monitor store may mean no fallback row. Partial schema,
  version failure, or read failure remains `monitor_store_unavailable`.
- Do not import `simplebroker._exceptions`. Prefer SimpleBroker's public error
  unchanged over string matching solely to improve its install sentence.
- OpenCode diagnostics remain advisory and out of startup validation. A real
  delegated invocation remains the semantic compatibility proof.
- `RunnerCapabilities.supported_types` remains a tuple, but tuple order is not
  semantic.
- Do not relax exact public JSON fields, exit classes, exception inheritance,
  pass-through output, queue order, control-envelope keys, primary-key/index
  order, or any enumerated contract.
- No new dependency, persisted format, queue, framework, or execution path.
- Stop if a fix needs a general abstraction or changes more than the producing
  and consuming boundary named by the audit.

## Audit Disposition

| Finding | Smallest planned correction | Constraint retained |
|---|---|---|
| TaskSpec clock bounds reject valid broker IDs | Use the existing exact-ID helper plus the resolved-TID nonzero check | Canonical form and nonzero storage range |
| Microsandbox test pins full SDK signatures | Bind the call production makes | Required-argument compatibility |
| Microsandbox task types pin tuple order | Compare the exact set | No unsupported type |
| OpenCode diagnostic pins English help | Report execution facts, not semantic support | Real invocation remains authoritative |
| Queue JSON code parses prose | Use one private usage-error subtype | Exact public JSON code and exit |
| Monitor absence parses backend prose | Use one explicit read-only absence signal | Partial/broken store stays degraded |
| Runner/agent registration parses self-owned prose | No change: no external input or demonstrated failure | Existing simple local behavior |
| Postgres hint parses upstream prose | Remove the rewrite; keep upstream error | Clear chained failure |
| OpenCode execution special-cases stderr prose | Use existing structured/compact error path | Nonzero execution still fails |
| Status test pins full layout | Assert required facts | Required content and JSON stay exact |
| CLI verb test pins `41` | Delete redundant count | Exact export bijection stays |
| Private tests pin full prose | Apply Pattern 8 case by case | Type, cause, field/path, stable phrase |

## Rollout and Rollback

No stored representation changes. Most fixes are test or in-memory
classification changes and are directly reversible.

TID acceptance has one rollback edge: after this release persists a canonical
TID outside v0.9.97's 2020/+1-year window, an older binary may reject that
TaskSpec. Normal manager-generated IDs do not cross that edge. Release notes
must state it. If such an ID exists, do not rewrite it for rollback; keep the
new reader or forward-fix because TIDs are immutable.

No destructive migration or cleanup is authorized.

## Spec Baseline

- `6cfb81ef266d26d58e4d476272c351f1944a7bc1`: code and specs at plan
  authoring time, including the completed Monitor repair.
- Plan type: implementation with spec revision.
- Promotion strategy A: promote the exact in-file text below after review,
  before code. Record baseline SHA plus spec diff as the promotion identifier.
- Promotion identifier:
  `6cfb81ef266d26d58e4d476272c351f1944a7bc1+72a254717af2d74f3712e82f1985bdce90ab8c6b`.

## Proposed Spec Delta

### `docs/specifications/02-TaskSpec.md` [TS-1]

Replace the current **TID format** bullet with:

> **TID format**
> - A resolved TaskSpec TID is the canonical 19-character ASCII decimal form
>   of a SimpleBroker exact message ID. Validation reuses the shared [SB-0.2]
>   exact-ID normalizer and enforces the nonzero SimpleBroker message-ID storage
>   range. Weft adds no local-wall-clock lower or future-plausibility bound.
>   Allocation, insertion, import high-water, and skew policy belong to the
>   broker operation that writes the ID, not TaskSpec deserialization.

Replace the `tid` implementation-status line with:

> - `tid`: Implemented. `TaskSpec.validate_tid()` delegates canonical exact-ID
>   and range validation to
>   `weft.helpers.message_ids.normalize_exact_message_id()` and rejects the
>   reserved zero value; templates may omit it and resolved TaskSpecs require
>   it.

### `docs/specifications/10-CLI_Interface.md` [CLI-1.2.3]

Insert after the Monitor fallback bullets:

> A completely uninitialized Monitor store supplies no fallback task evidence.
> A partially present, unsupported, unreadable, or failed store produces the
> existing degraded `monitor_store_unavailable` snapshot. The distinction uses
> an explicit store result or exception type, never backend exception prose,
> and performs no schema mutation.
>
> Plain task-status output must contain the required facts. Exact line order,
> punctuation, spacing, and label spelling are presentation details unless
> explicitly enumerated here. JSON names and semantic values remain exact.

### `docs/specifications/10-CLI_Interface.md` [CLI-4]

Add to queue input-error behavior:

> An invalid exact message ID under `--json` exits 2, writes no stdout, and
> writes one stderr JSON object with `error="INVALID_MESSAGE_ID"` and
> `retryable=false`. The CLI selects that shape from an internal typed result,
> not from human-readable prose.

### `docs/specifications/13-Agent_Runtime.md` [AR-7]

Insert after the explicit-diagnostics bullets:

> External compatibility checks validate only capabilities Weft consumes. SDK
> checks bind the actual call form; added optional parameters remain compatible
> while added required parameters or positional-call breakage remain
> incompatible.
>
> Explicit provider diagnostics may record whether `run --help` executed,
> timed out, and its exit status. Help prose cannot prove semantic command
> support. The first real delegated invocation remains authoritative. Nonzero
> Provider-authored stderr phrases are not execution classifiers.

### `docs/specifications/10B-Builtin_TaskSpecs.md` `probe-agents`

Replace the OpenCode provider-report field with:

> - for `opencode`, a `run_help` object with `attempted`, `timed_out`,
>   `returncode`, and compact `detail` fields. This describes probe mechanics
>   only and makes no semantic support claim.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## Tasks

1. **Promote the spec delta and fix TID validation.**
   - Obtain the outside review described below. Delete any step the reviewer
     finds ceremonial or unsupported by a demonstrated failure.
   - Apply the source spec edits and add plan backlinks. Record the promotion
     identifier before code changes.
   - Add one focused test for `normalize_exact_message_id()` covering the
     canonical boundary: largest accepted value, first out-of-range value,
     non-ASCII digits, padding, wrong length, and non-decimal input. Do not copy
     that matrix into every consumer.
   - TaskSpec red tests remain thin: accept an exact ID just below the old 2020
     floor and the storage maximum beyond the old +1-year window; reject the
     reserved zero value and one plainly invalid value; preserve template-null
     and resolved-required behavior.
   - Replace the custom time logic with `normalize_exact_message_id(v)` and
     return the original canonical string. Keep the module's `time` import,
     which is also used by task-state methods.
   - Expand the valid Hypothesis strategy to the shared exact-ID domain instead
     of the current contemporary interval.
   - Do not touch broker insertion or add a TID helper.
   - Verify:
     `. ./.envrc && ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/helpers/test_message_ids.py tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py -q`.
   - Stop for owner review if the outside reviewer disputes the TID ownership
     or rollback boundary.

2. **Fix external capability checks.**
   - Microsandbox: replace exact `Sandbox.get/remove` parameter tuples with
     `inspect.signature(...).bind("probe")` against the installed SDK. Compare
     `supported_types` as `frozenset({"command", "agent"})`. Do not add
     synthetic tests of stdlib `inspect`, and do not change production adapter
     code unless the actual bind fails.
   - OpenCode: change the explicit help probe to report only attempted,
     timeout, return code, and compact detail. Do not emit
     supported/unsupported from help text. Remove the exact stderr special case
     from `OpencodeProvider.parse_result()` and use its existing structured or
     compact nonzero-result path.
   - Fixture tests cover arbitrary successful help copy, nonzero help, timeout,
     and real invocation failure detail. No credentialed or network probe.
   - Verify:
     `. ./.envrc && ./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_plugin_validation.py tests/core/test_agent_validation.py tests/core/test_provider_cli_backend.py tests/core/test_builtin_agent_probe.py -q`.

3. **Replace only the demonstrated prose classifiers.**
   - Queue: add one private `CommandUsageError` subtype in
     `weft/commands/queue.py`; `_exact_message_id()` raises it and the CLI
     checks that type. Prove unrelated usage prose cannot select
     `INVALID_MESSAGE_ID`. This subtype is retained because it selects a
     stable public JSON error code; do not modify the public exception
     hierarchy or generalize it into a taxonomy.
   - Monitor: add one read-only explicit not-initialized signal. It applies
     only when the existing store table checks prove all Monitor tables
     absent. Reuse `_MonitorTableAccess.table_exists()` and the existing table
     specifications; add no parallel catalog query or schema detector. Partial
     tables and read failures remain degraded. Remove backend-text matching;
     do not call `ensure_schema()`.
   - Postgres: remove `normalize_backend_resolution_error()` and its now-unused
     constants. Preserve SimpleBroker's public resolver error and chain; test
     backend name/install guidance by substring.
   - Verify focused tests:
     `. ./.envrc && ./.venv/bin/python -m pytest tests/cli/test_cli_queue.py tests/core/test_monitor_store.py tests/commands/test_task_commands.py tests/context/test_context.py tests/cli/test_cli_init.py -q`.
   - Stop rather than generalize if any case cannot be fixed inside its named
     producer/consumer pair.

4. **Relax incidental tests and close the release.**
   - Status: assert required facts without a whole-output line snapshot; keep
     exit and stderr exact.
   - Import boundary: delete only the redundant `len(paths) == 41`; keep both
     exact bijection checks.
   - In `tests/commands/test_submission.py`,
     `tests/core/test_runner_validation.py`,
     `tests/core/test_agent_runtime.py`, `tests/core/test_pipelines.py`,
     `tests/core/test_spec_store.py`, and
     `tests/system/test_builtin_contract.py`, change only the full-prose
     assertions identified by the audit. Retain exact exception class, cause,
     offending field/path, and stable substring. Do not relax public JSON,
     pass-through payloads, empty-output guarantees, or enumerable sets.
     These are test-only edits; do not add production exception types or
     registry behavior to support them.
   - Update `CHANGELOG.md` with the TID rollback caveat and changed OpenCode
     diagnostic fields. Extend the August 25 lesson with capability/prose
     examples. Reconcile plan/spec/code backlinks and close deviations.
   - Mark this plan completed only after implementation, independent completed-
     work review, full gates, and a commit.

## Testing and Verification

Use real Pydantic validation and the shared exact-ID helper for TIDs. Use a
real temporary SQLite catalog for Monitor absence/partial-schema cases. Mock
only the external OpenCode subprocess. Inspect the installed Microsandbox SDK
for the integration assertion. Do not add synthetic signature tests that only
retest `inspect.signature().bind()`.

After each task's focused command, run:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json --output /tmp/weft-compatibility-hardening-backstitch.json
```

Backstitch must show no new error or warning from touched files. Before release,
black-box check invalid queue ID JSON, alternate OpenCode help copy, and the
README `run/status/result` smoke path. No traceback may reach the user.

## Outside Review

Use an outside model family if available. The review's first job is subtraction,
not completeness theater.

Reviewer prompt:

> Review `docs/plans/2026-08-25-compatibility-contract-hardening-plan.md`, its
> proposed spec delta, baseline `6cfb81ef`, and cited code. Do not implement.
> First look for unnecessary complication, brittle new contracts, generalized
> abstractions, duplicated tests, or ceremony that does not increase
> correctness. Recommend deletion whenever a task is not tied to a demonstrated
> failure mode. Specifically challenge every new exception type, spec sentence,
> test matrix, and release gate. Then check that the remaining plan preserves
> canonical TID range, Monitor partial-schema degradation, exact public JSON,
> and real provider execution semantics. BLOCK if a zero-context engineer could
> implement a more complex system than the audit requires, or if the plan
> weakens a real correctness boundary. Otherwise PASS with any nonblocking
> deletions.

After each implementation task, use a scoped review. Before completion, run one
outside completed-work review focused on both regression risk and unnecessary
machinery.

## Review Log

| Date | Reviewer | Scope | Verdict | Findings and disposition |
|---|---|---|---|---|
| 2026-08-25 | Claude Opus 4.8 | plan and proposed spec delta | BLOCKED, revised | Accepted: removed runner/agent exception types, synthetic `inspect` tests, duplicated TID matrices, and duplicate capability spec text; constrained Monitor work to the existing store verification boundary. Retained one private queue subtype because it selects a stable public JSON contract; round-2 review pending. |
| 2026-08-25 | Claude Opus 4.8 | subtractive revision | PASS | No blockers. Confirmed four coherent slices and that the queue subtype is proportionate to the public JSON selector. Nonblocking cautions adopted: keep the six named prose-assertion edits test-only and keep provider classification wording compact. |
| 2026-08-25 | scoped code reviewer | TID implementation | PASS after revision | Reserved zero and the actual storage maximum are explicit; removed a redundant clock-monkeypatch test. |
| 2026-08-25 | scoped test reviewer | external capability checks | PASS after revision | Added the unattempted probe state and asserted the exact subprocess call; stopped pinning compact diagnostic prose. |
| 2026-08-25 | scoped spec reviewer | typed prose-classifier replacements | PASS after revision | Unsupported and unversioned Monitor stores now degrade through the same read-only version gate. |
| 2026-08-25 | scoped code reviewer | incidental test cleanup | PASS after revision | Status assertions use word- and digit-bounded semantic facts without freezing spacing or accidental numeric substrings. |
| 2026-08-25 | Claude Opus 4.8 | completed worktree diff | PASS | No unnecessary machinery or weakened boundary found. Confirmed both new exception types are local selectors for existing machine contracts; noted the Monitor version gate is intentionally strict and the plan must remain draft until committed. |

## Verification Log

- Fast suite: 4,262 passed, 2 skipped.
- Slow-inclusive suite: 4,263 passed, 13 skipped.
- Spec metadata, spec hygiene, import boundaries, mypy, and Ruff passed.
- Backstitch remained at the pre-existing 27 errors and 988 warnings, with no
  new issue attributable to this change.
- Black-box probes passed for `run/status/result`, invalid message-ID JSON, and
  successful provider help with arbitrary output.

## Out of Scope

- Monitor schema, migration, DDL, or index work
- SimpleBroker allocator, import floor, skew policy, or public API changes
- Noncanonical or out-of-range TIDs, or any TID rewrite
- Generic error-code, registry-error, SDK-compatibility, or validation frameworks
- Public exception hierarchy changes
- Repository-wide message rewrites or blind relaxation of exact assertions
- Credentialed/network provider probes or new startup checks
- Provider argv, authority, session, or result-shape changes
- New dependencies, persisted formats, queues, or execution paths

## Fresh-Eyes Review Checklist

- [x] Every task maps to an audit finding and names what correctness remains.
- [x] No task introduces a reusable framework for a one-site problem.
- [x] TID tests cover old clock bounds and actual storage bounds.
- [x] Monitor absence tests use real catalog state, not exception prose.
- [x] OpenCode diagnostics make no support claim from help copy.
- [x] Exact public and semantic assertions remain exact.
- [x] Rollback caveat is in the release note.
- [x] Outside review findings and deletions are recorded before promotion.
