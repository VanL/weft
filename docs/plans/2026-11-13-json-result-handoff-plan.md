# JSON Result Handoff with Child-Owned Conversion

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.5]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-5]; docs/specifications/07-System_Invariants.md [EXEC.5]-[EXEC.10]; docs/specifications/13-Agent_Runtime.md [AR-4], [AR-5], [AR-6]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-10.2]; docs/specifications/14-Python_API_Surfaces.md [PY-1]
Superseded by: none

Class: 5

Plan type: implementation with spec revision. Hardening is mandatory: this
changes a private execution protocol, conversion ownership, and the supported
host result values. This document plans implementation; approval of this plan
does not assert that its code or spec delta has shipped.

## Goal

Replace pickle in the host worker-to-parent result handoff with UTF-8 JSON.
Convert application return values to their public JSON/text representation in
the child that executed them. Cover function tasks, one-shot agents, and
persistent agent session responses. Preserve exact prepared output text,
per-item agent output boundaries, parent-owned queue publication, and the
existing terminal handoff reducer and cleanup guarantees. No application
object is reconstructed in the parent to publish a host result.

## Source Documents and Baseline

Spec baseline: `bd92b9c9caa47514919b2c4f88e7478c8d97b497` for the source specs
listed above. This plan explicitly revises that baseline. Promotion baseline: the base commit above plus the implementation/spec worktree
diff SHA-256 `6d0772da05036d241c134d37ac79aeb00aecc7a1483cb4595a7fcbd061dd31e1` (2026-11-13, before completed-slice review).
This identifies the atomic code/spec promotion without claiming a commit.

Read before implementation:

- [Program theory](../program-theory.md), especially [WT-2a.2], [WT-2a.5],
  [WT-2a.6], [WT-2a.9], [WT-4.2], [WT-5], and [ALT-WT-012]. The private pipe
  carries execution evidence, not a second durable truth or a trust boundary.
- [Agent context](../agent-context/README.md),
  [decision hierarchy](../agent-context/decision-hierarchy.md),
  [principles](../agent-context/principles.md),
  [engineering principles](../agent-context/engineering-principles.md),
  [runtime runbook](../agent-context/runbooks/runtime-and-context-patterns.md),
  [testing runbook](../agent-context/runbooks/testing-patterns.md),
  [planning runbook](../agent-context/runbooks/writing-plans.md),
  [hardening runbook](../agent-context/runbooks/hardening-plans.md),
  [review runbook](../agent-context/runbooks/review-loops-and-agent-bootstrap.md),
  [lessons pointer](../agent-context/lessons.md), and [lessons](../lessons.md).
  These were consulted during authoring, together with the source specs/code.
- [Core components](../specifications/01-Core_Components.md) [CC-3.5],
  [message flow](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-5],
  [invariants](../specifications/07-System_Invariants.md) [EXEC.5]-[EXEC.10],
  [agent runtime](../specifications/13-Agent_Runtime.md) [AR-4]-[AR-6],
  [Django](../specifications/13C-Using_Weft_With_Django.md) [DJ-10.2], and
  [Python surfaces](../specifications/14-Python_API_Surfaces.md) [PY-1].
- [Testing strategy](../specifications/08-Testing_Strategy.md) [TS-0], [TS-3.1]
  and [resource management](../specifications/06-Resource_Management.md) [RM-5.2]
  for real-process verification, supervision, and output/backpressure ownership.

## Current Structure and Evidence

| Owner | Current behavior and coupling |
| --- | --- |
| `weft/core/terminal_handoff_transport.py` | `send_terminal_payload` pickles synchronously before one `send_bytes`; `receive_terminal_payload` calls `recv_bytes` then unpickles. Pre-write failure may send one fallback; write failure never sends another frame. Poll/wait/EOF helpers own platform normalization. |
| `weft/core/runners/host.py` | `_worker_entry` returns function or agent values in `RunnerOutcome`; `_agent_session_worker_entry` sends booted/ready/result/startup-error mappings. Both use the same private transport. `_run_one_shot_terminal_handoff` requires a `RunnerOutcome`. |
| `weft/core/runners/sessions.py` | `AgentSession` handles startup separately from work-item supervision; parses responses into `SessionExecutionResult`, applies the shared reducer, and invalidates failed sessions. |
| `weft/core/targets.py` | `serialize_result`: strings unchanged; otherwise `json.dumps`; only `TypeError` selects whole-value `str(result)`. It does not use recursive `default=str` for public output. |
| `weft/core/agents/runtime.py` | `AgentExecutionResult` carries outputs separately from metadata/usage/tool trace/artifacts. Its current private-payload methods still contain arbitrary application values. |
| `weft/core/runners/agent_session_protocol.py` | Builds and parses versioned private session mappings. `make_result_response` currently prepares its nested mapping before the transport's serialization guard. |
| `weft/core/tasks/consumer.py` | `_emit_result` expands agent outputs; `_prepare_output` serializes, encodes/sizes, and spills; `_emit_single_output` publishes. `_build_agent_execution_payload` converts optional agent metadata with `default=str`, falling back to identity/status on TypeError/ValueError/OverflowError. `_commit_work_outcome` returns the value to private `run_work_item` callers. |
| `weft/ext.py` | Public `RunnerOutcome` and `SessionExecutionResult` have opaque `value` fields. Their fields and plugin signatures need not change. Core runner implementations themselves are private under [PY-1]. |
| Django worker | `integrations/weft_django/weft_django/worker.py::run_registered_task` initializes Django in the child and returns the application value. Parent-side object reconstruction can require application initialization that is absent there. |
| Other runners | Docker and Microsandbox can construct raw `AgentExecutionResult` locally. They do not use this host response codec; keep their current contracts and the Consumer's raw-plugin path. |

Authoring probe: a dict subclass whose `__reduce_ex__` raises failed
`ForkingPickler.dumps`, while `serialize_result` produced `{"answer": 42}` and
JSON bytes round-tripped over a real `multiprocessing.Connection`. This proves
the extra pickle restriction, not the complete implementation proposed here.

Comprehension checks: before the first code edit, record answers in the
execution log. (1) Does child exit prove no result? Expected: no; enter terminal
drain and await payload, seal, transport failure, or drain expiry. (2) Can the
parent parse each prepared output then re-encode it for publication? Expected:
no; that loses exact text, including duplicate JSON keys. Parsing is permitted
only for the private direct-call projection, never the publication path.

## Design and Difference Register

Use an explicit data-transfer representation, not a general object serializer.
Keep the framing/transport separate from result policy. Add private frozen
`PreparedOutput` and `PreparedTaskResult` dataclasses and their preparation,
validation, and direct-call projection helpers to `weft/core/targets.py` next
to the existing result policy. This is one shared conversion owner used by
host workers and Consumer's raw-plugin adapter. Do not export these names from
`weft.ext`, add serializer plugins, or add an alternate durable lane.

`PreparedOutput` holds `format` (`text` or `json`) and exact `text`.
`PreparedTaskResult` holds `kind` (`value` or `agent`), an ordered tuple of
prepared outputs, and `agent_execution` (null for value results, a detached
JSON-compatible snapshot for agent results). `value` has exactly one output,
including `None` as JSON text `null`; `agent` may have zero or more. Dispatch
preparation by `isinstance(value, AgentExecutionResult)`, preserving the current
case where a function returns that internal agent result type.

For a string, prepare text unchanged. Otherwise use the same `json.dumps`
defaults as `serialize_result`; on TypeError alone use whole-value `str` and
mark text. Extract that policy once and have `serialize_result` delegate to it,
so the existing non-host path does not grow a competing definition. Do not
normalize dates, bytes, Decimal, sets, or application classes recursively.
Cycles, recursion failures, or a failing `__str__` become bounded result
conversion failures. This intentionally replaces the accidental requirement
that a publicly representable value also be picklable.

Prepare the agent metadata snapshot in the child using the exact current
`_build_agent_execution_payload` policy: runtime/model/output_mode/status plus
metadata/usage/tool_trace/artifacts; `json.dumps(default=str)`; on TypeError,
ValueError, or OverflowError fall back to the four identity/status fields.
Round-trip that snapshot through JSON in the child, as the current helper does,
to detach application values. Retain omitted optional fields on the fallback,
rather than filling them with new empty values. Move this policy to the shared
preparation owner and have Consumer delegate for raw plugin results. Do not turn best-effort auxiliary
metadata into a fatal output failure. Exceptions outside those existing caught
classes remain explicit failures. Validate raw agent identity fields during
child preparation using the existing `from_private_payload` predicates: runtime,
output_mode, and status are nonempty strings; model is a string or null. Invalid
identity produces the bounded result_serialization fallback, not a parent
transport failure. This deliberately applies the existing session predicate to
one-shot agents too; it does not broaden identity validation beyond it.

Encode the detached metadata snapshot as `agent_execution_json` text on the
private wire and parse it once on receipt to restore the log snapshot. The
parent checks that the parsed snapshot is an object, not its identity/schema
again; bad snapshot syntax/root remains a transport failure. Similarly carry
output JSON as text inside each `PreparedOutput`. Thus the outer protocol is strict
JSON even when the legacy public representation contains `NaN`/`Infinity`;
this plan does not change that public policy. `ensure_ascii=True` on the outer
encoder transports lone surrogates without a UTF-8 encoding failure; the
Consumer retains its existing UTF-8 replacement behavior at publication.

| Difference | Disposition |
| --- | --- |
| Application objects cross child-to-parent boundary | Retire: only prepared text and Weft-owned envelope data cross. No dynamic imports or object hooks during receive. |
| Unpicklable value with usable JSON/text output | Intentionally becomes success; prove this in function and agent paths. |
| Public output JSON/text selection and text bytes | Preserve the existing conversion policy and output text, including whitespace, JSON-looking strings, duplicate keys, nonfinite values, and whole-value string fallback. |
| Agent output cardinality/order | Preserve zero/one/many items, no public agent wrapper. |
| Optional agent metadata | Preserve best-effort conversion and four-field fallback; conversion now occurs in the child. Invalid required identity fields use the existing session predicates in child preparation for both host modes, producing result_serialization diagnostics. |
| Host private runner `value` | Intentionally carries `PreparedTaskResult`; no arbitrary application object contract. Update private direct-runner tests deliberately. Public ext dataclass fields stay unchanged. |
| Private `Consumer.run_work_item` return | Project prepared function output to text or `json.loads(text)` according to its tag. Project agent results to a Weft-owned `AgentExecutionResult` with normalized outputs and metadata; fallback metadata gets the dataclass's empty optional values only in this private projection. Publication uses original prepared text/snapshot. Never preserve arbitrary application object identity or tuple/subclass types. |
| Raw non-host plugin values | Preserve support; prepare them in Consumer through the shared owner. This is a current plugin adapter, not a legacy wire decoder. |
| Function/one-shot and session deadline precedence | Preserve their intentional difference; do not unify reducer policy. |
| Startup vs execute timeout | Preserve distinct session readiness budget and per-work-item timeout. |
| Serialization fallback vs failed write | Preserve one pre-write fallback frame; never append a fallback after a write starts. |
| Spawn/request serialization | Out of scope: multiprocessing spawn arguments and session request MPQueue may still use pickle. |

### Private response schema and ownership

Use one JSON codec in `terminal_handoff_transport.py`: UTF-8, ASCII escapes,
`allow_nan=False`, no `default` handler, and no object hooks. The sender emits
standard JSON. The receiver uses the standard JSON decoder and checks the root
object and protocol field shapes; do not add a duplicate-key hook or a second
whole-document numeric-validation pass.
Protocol-specific adapters validate exact keys, types and discriminants before
accepting an outcome; booleans are not valid integers. Do not add a legacy pickle
decoder, a format negotiation layer, or a configurable codec.

The prepared result wire form has exact keys `kind`, `outputs`, and
`agent_execution_json`. Each output has exactly `format` and `text`, with a
known format tag and string text. Treat the inner output text as opaque on the
queue-driven parent path; it was produced by the child conversion owner. Parse
metadata text only because publication needs its mapping, retaining it in the
prepared record without a second schema-validation pass. Reject malformed
envelope/record shapes or unparsable/non-object metadata as transport failures,
never partial successes. Only direct-call projection parses output JSON.

One-shot wire envelope: exact keys `type` (literal `outcome`), `status` (`ok`
or `error`), `result` (prepared result or null), `error`, `stdout`, `stderr`
(string or null), `returncode` (integer or null), `duration` (finite nonnegative
number), and `diagnostics` (JSON object or null). `ok` requires a prepared
result and null error; `error` requires null result and nonempty error. Child
metrics/runtime handles are not sent: the current child leaves them unset and
the parent attaches its existing observed metrics and runtime authority.
Reconstruct only `RunnerOutcome` and private prepared dataclasses in the parent.

Session wire: retain `version`/`type` and existing booted, ready, startup_error,
and result categories. Increment `AGENT_SESSION_PROTOCOL_VERSION` in
`weft/_constants.py` once for the new response shape, updating execute/stop
request producers and consumers together. Booted/ready have only version/type;
startup_error adds nonempty error and optional diagnostics object; result adds
status (`ok`/`error`), error, optional diagnostics, and result. Successful result
requires prepared `kind=agent`; error requires null result and nonempty error.
Do not keep the old nested `AgentExecutionResult.to_private_payload` response
shape. Remove its private encode/decode methods after updating the known
assertion in `tests/core/test_agent_runtime.py` and verifying the remaining
full-tree consumers are the session helpers being replaced; retain that test's
independent runtime/output assertions. Keep the runtime adapter
dataclass and aggregate method for non-host users. The session parent returns
`SessionExecutionResult(value=prepared_result)` through the existing path.

Put preparation and JSON encoding inside the same pre-write guard. Add private
`prepare_terminal_payload(payload_factory, serialization_failure_factory=...)`
and `send_prepared_terminal_payload(sender, frame)` helpers to the existing
transport module. The first calls the payload factory once and returns the
complete bytes plus the original/fallback flag; the second writes exactly once.
Keep `send_terminal_payload` as their small composition for session/handshake
callers, taking a payload factory. These are the two existing framing phases,
not independently configurable codecs. The fallback factory must build only fixed Weft data, with `phase=result_serialization` diagnostics
for both one-shot and persistent work. It must never inspect or stringify the
original result again. If fallback construction/encoding also fails, raise
`TerminalHandoffTransportError`; the caller's existing `finally` closes the
endpoint and normal bounded parent classification applies. Transport helpers
never acquire endpoint-lifetime ownership. Make bounded exception-cause
formatting robust to an exception whose own `__str__` raises (use its type name in that case).
Keep transport exceptions outside the execution-exception handler: a failed
send must not be caught as a task execution error and trigger a second send.

One-shot structure is explicit: enter `_worker_runtime_context`; run the target
under its execution exception handler; capture execution end time; call the
frame preparation helper while env/cwd are still active; leave the context;
then write the prepared frame outside the execution/context exception handler.
Re-raise TerminalHandoffTransportError before any broad execution catch. Keep
context entry/exit failures classified as execute failures: if context exit
fails after a frame was prepared but before any write, discard that unsent
frame and prepare one execution-error frame outside the failed context. Outer
`finally` closes the sender in every case. Never send both frames. This also
preserves restoration-failure behavior instead of publishing success before a
failed context exit. Persistent conversion and its composed send stay inside
the existing session context, before reading another request.

Keep the existing duration owners: one-shot duration is worker-reported
execution time, captured before conversion; persistent duration is measured
by Consumer around `session.execute` and includes the handoff. Do not add a
new persistent wire duration or move its clock into the child. Conversion
remains under the supervising parent's timeout/cancel/resource controls.
Ordinary execution exceptions retain their execution failure classification.

Consumer normalizes successful non-command raw plugin values once in
`_commit_work_outcome`, after `_ensure_outcome_ok` and before publication.
Already-prepared host values pass through. This avoids repeating application
conversion or optional metadata hooks in separate output/log helpers.
`_emit_result` passes each `PreparedOutput.text` to the existing
`_emit_single_output`; `_commit_work_outcome` uses the detached snapshot
directly, and the obsolete metadata wrapper is removed. `_prepare_output` stays unchanged: serialize_result already returns
strings unchanged, so prepared JSON is not double-quoted. Byte sizing, encoding,
spill, streaming, metrics, reserved disposition, and finalization retain their
owners. `_emit_result` accepts only the prepared record; the commit boundary
is the sole Consumer preparation point.

Compute direct-call projection only when `_direct_work_waiting` is true, and
before output/finalization so an exception cannot follow durable success. The
ordinary queue-driven path never parses output text or constructs an unused
projected AgentExecutionResult. Projection does not replace prepared text in
storage.

## Invariants, Rollout, and Limits

- Keep the `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
  spine. No new queue, persistence store, child broker publication, or file
  handoff. TID, frozen spec/io, forward-only state, reserved keep/clear policy,
  runtime-state dump exclusion, and generator-based history reads do not move.
- Preserve [CC-3.5] and [EXEC.5]-[EXEC.10]: exit is not EOF, drain stays bounded,
  malformed frames are transport failures, cancellation/timeout/limit precedence
  stays unchanged, consumed stop/exit edges cannot starve drain, and the parent
  stops/reaps/closes under existing cleanup rules before returning. Successful
  persistent turns keep the session; non-ok turns invalidate it.
- Preserve parent publication order: result outputs before terminal completion
  evidence; observe existing terminal/outbox grace rules in tests. No private
  result envelope reaches an outbox, task log, CLI, or pipeline input.
- Application conversion may execute application hooks in the child; JSON is
  not a sandbox. The single-operator trust model stays unchanged. No claim that
  Weft or multiprocessing becomes entirely pickle-free.
- No new dependencies, public CLI options, public ext fields, arbitrary-object
  adapters, protocol fallback, or output-size/transport budget redesign. Large
  values are still buffered for framing, and parent-owned spilling remains.
- Rollout is one coordinated code/spec release. A private connection's endpoints
  must run matching installed code. Drain/stop old contexts and persistent
  sessions before replacing code in place, then start new processes. Do not
  hot-swap a worker module under an old parent. There is no durable payload
  migration: stored outbox text and lifecycle schema are unchanged.
- Rollback reverts the whole codec/producer/consumer/spec slice together after
  draining the new processes. Do not roll back only one endpoint. Newly accepted
  unpicklable results can fail again under old code; document that limitation.
  No data deletion or one-way store migration is introduced.

## Proposed Spec Delta

Promotion strategy **B (atomic)** for the following sections. Apply these exact
requirements, implementation mappings, reciprocal code docstrings, and code in
one implementation slice; no intermediate release with mismatched producers and
consumers. The present planning task adds backlinks only, not these requirements.

### Core components [CC-3.5]

Replace only the sentence beginning "A receiver decode/unpickle failure" and
ending "never `outcome_received`." with:

> A receiver JSON decode failure, invalid private response shape, or invalid
> nested prepared result is `transport_failed`; it is never `outcome_received`.

The removed sentence's decode-error and wrong-payload-type requirements both
remain. Insert immediately after the paragraph ending "public queue or
runner-plugin protocol.":

> Host function, one-shot agent, and persistent agent responses use UTF-8 JSON
> frames over the existing private Connection. The sender emits a standard
> JSON object, rejecting nonfinite outer numbers during encoding. Receivers
> validate response discriminants and required field types and reconstruct only
> Weft-owned outcome/prepared-result records; they never unpickle or import
> application classes to restore a result. Multiprocessing process bootstrap
> and persistent request queues are separate from this response contract.
>
> The child prepares public output text and optional agent execution metadata
> before writing a response and while its task runtime context is active.
> Preparation and complete JSON encoding share one pre-write error guard.
> One-shot workers prepare the frame inside their runtime context and write
> it after successful context exit; context entry/exit failures retain execution
> failure classification and replace any unsent frame before the sole write.
> Preparation or JSON-encoding errors may produce one fixed error response with
> bounded result_serialization diagnostics. After any frame write begins, no fallback
> frame is attempted. On a failed fallback or write, the caller closes the
> channel through its existing cleanup owner; bounded transport-failure rules
> still apply. A persistent session ends after a result conversion failure. Execution exceptions retain their
> execution failure classification.
>
> A host success carries a private prepared result: an ordered sequence of
> tagged text/JSON output strings plus an optional agent execution snapshot.
> Function values have one output, including JSON null; agent results have
> zero or more outputs. The result is not a public envelope. The Consumer
> publishes prepared text through the existing output path without decoding
> and re-encoding it, and alone owns spilling, stream delivery, reservation
> disposition, and lifecycle publication. Non-host plugin values remain
> supported through the same preparation policy at the Consumer boundary.
>
> Direct core runner values may be private prepared records. Private
> Consumer.run_work_item return projection contains plain JSON values or text,
> or a Weft-owned AgentExecutionResult containing those values; arbitrary
> application types and object identity are not preserved. Projection runs
> only for a waiting direct caller and before publication. Queue-driven
> publication does not parse or re-encode prepared output text.

Extend this section's implementation mapping to name `weft/core/targets.py`
for preparation and projection, the transport module for JSON framing, host
and session adapters for protocol validation, and Consumer for publication.
The reducer table and ordering paragraphs are untouched.

### Message flow [MF-2]

Insert before the existing "Captured command stdout is text" rule:

> Function result conversion preserves strings as text. For other values it
> uses Python JSON encoding with its existing defaults, falling back to
> whole-value string conversion only on TypeError. Failure of that conversion
> is a work failure. Agent outputs use the same policy independently for each
> output item. Host children perform this conversion before response handoff;
> the Consumer publishes the prepared representation without re-encoding its
> value. This preserves output text, including whitespace, JSON-looking string
> values, and the current JSON encoder's nonfinite-number representation.
> JSON output is carried as text inside the strict private JSON frame.
> Existing encoding, size/spill, stream, and lifecycle ordering rules continue
> to govern publication. Successful conversion does not require picklability.

Extend the nearby implementation mapping with `weft/core/targets.py` and
child preparation in `weft/core/runners/host.py`; keep the existing Consumer
publication mapping and command output rules.

### Agent runtime [AR-4], [AR-5], [AR-6]

Append after the public output bullets in [AR-4]:

> Host execution prepares each public output in its child, preserving order
> and zero/one/many output cardinality. The parent publishes exact prepared
> text through Consumer's normal output path. It does not stringify or
> reconstruct application output objects. No private prepared-result tag is
> written to a public queue.

Insert before the implementation mapping in [AR-5]:

> Agent execution metadata is separate from public outputs. Its snapshot
> includes runtime, model, output_mode, status, metadata, usage, tool_trace,
> and artifacts, using JSON conversion with string fallback for unsupported
> values. Required runtime/output_mode/status are nonempty strings and model
> is a string or null, checked in child preparation with a bounded
> result_serialization error on failure. TypeError, ValueError, or OverflowError
> in the optional snapshot reduces it to runtime, model, output_mode, and
> status, without failing usable
> public output. Host children prepare the snapshot; non-host plugin results
> use the same conversion policy in Consumer, with failures following its
> existing publication-failure path. Bounded result_serialization responses
> apply to host children. The wire carries snapshot JSON as text,
> preserving the existing metadata number policy inside strict JSON framing.

Replace exactly the [AR-6] paragraph starting "That parent/subprocess link"
and ending "part of the public queue surface." with:

> The parent/subprocess link uses versioned private request and response
> envelopes. Response frames are UTF-8 JSON, including booted, ready,
> startup_error, successful result, execution error, and result conversion
> error. Request delivery remains on its existing multiprocessing queue.
> Session result responses carry the same prepared agent outputs and metadata
> as one-shot host agents, not arbitrary Python objects. The current version
> is the sole accepted version; startup, work-item, and session cleanup
> guarantees are unchanged. These envelopes are not public queue protocols.

This retains privacy/versioning while correcting the baseline's overly broad
claim that the entire link already uses JSON. Update the [AR-4]-[AR-6]
implementation notes for shared preparation and the changed session helpers;
do not remove the adapter's raw AgentExecutionResult contract.

### Django [DJ-10.2]

Insert immediately before "Preferred persistence rule:":

> Host-backed registered tasks convert their return values to core JSON/text
> output in the Django-initialized child. The parent does not unpickle Django
> or application objects to publish results. A value that cannot be pickled
> can still succeed when core output conversion succeeds. Core string fallback
> is not a model serializer; return explicit data or durable identifiers when
> structured application results are required.

No normative text changes are needed in [EXEC.5]-[EXEC.10] or [PY-1]: they
already express the protected lifecycle rules and private core boundary.

## Dependency-Ordered Tasks

1. [x] **Establish regression evidence and protocol fixtures.** Read the owners
   above. Extend `tests/core/test_targets.py`,
   `tests/core/test_terminal_handoff_transport.py`, `tests/core/test_agent_runtime.py`,
   `tests/tasks/sample_targets.py`,
   `tests/tasks/test_runner.py`, `tests/tasks/test_agent_execution.py`, and
   `tests/fixtures/llm_test_models.py`. Add literal output/schema fixtures and
   real spawned probes from the test matrix below. First demonstrate that
   usable unpicklable function and agent values fail under the baseline.
   Do not implement a second runner in fixtures. Record actual failed assertions;
   import/setup failures do not prove the premise. Preserve existing lifecycle
   coverage while replacing pickle-specific failure fixtures with real JSON or
   conversion failures. Stop if a proposed oracle assumes producer exit is EOF.

2. [x] **Atomic spec-promotion and response conversion.** Apply the exact delta
   above and update reciprocal mappings/docstrings together with
   `weft/core/targets.py`, `weft/core/terminal_handoff_transport.py`,
   `weft/core/runners/host.py`, `weft/core/runners/agent_session_protocol.py`,
   `weft/core/runners/sessions.py`, `weft/core/agents/runtime.py`,
   `weft/core/tasks/consumer.py`, and `weft/_constants.py`. Keep helper ownership
   as specified; update each sender to the factory guard and each receiver to
   validated prepared data. Keep `weft/core/terminal_handoff.py` unchanged
   unless a demonstrated codec integration defect requires a recorded deviation.
   Preserve raw extension values through the shared Consumer adapter and avoid
   new public dataclass fields. Record the promotion baseline and review this
   coherent slice cross-family before moving on. Stop on new queue/state
   ownership, arbitrary type reconstruction, or a public output shape change.

3. [x] **Prove integration, failure containment, and lifecycle parity.** Extend
   `tests/tasks/test_consumer_terminal_events.py`,
   `tests/commands/test_observation_connections.py`,
   `integrations/weft_django/tests/test_weft_django.py`, and its existing
   `fixture_project/testapp/weft_tasks.py`; use a new task fixture there for
   child-only Django/application conversion. Reuse raw extension tests in
   `extensions/weft_microsandbox/tests/test_microsandbox_agent_runner.py` and
   `extensions/weft_docker/tests/test_agent_runner.py`. Update
   `tests/helpers/leaked_sender.py` to the private sender API. Verify direct
   core projections deliberately rather than changing equality assertions to
   stringify both sides. Stop if tests need parent Django initialization,
   mocked broker/process lifecycle, longer product deadlines, or lower test
   concurrency to pass.

4. [x] **Reconcile and close implementation.** Update `CHANGELOG.md` with the
   new representable-but-unpicklable success behavior, child conversion,
   private core projection change, and coordinated restart requirement. Keep
   spec backlinks and implementation notes synchronized. Run final gates below,
   complete author fresh-eyes and cross-family completed-work review, disposition
   findings in this plan, and only then mark this plan and its index row
   `completed`. Plan approval alone leaves implementation status `draft`.

## Test Matrix and Falsifiable Proof

Use `WeftTestHarness`, real spawn-context workers/pipes, real queues, and existing
llm test plugins. No provider network calls are needed. Every launched process,
session, and Manager belongs to cleanup in `finally` or the harness; never
delete a live context. Literal expected output must be independent of the
production helper. Each row names the failure it would catch.

| Boundary | Required observation and counterexample |
| --- | --- |
| Function conversion | Child-defined dict subclass rejects pickle but emits literal `{"answer": 42}`; local class with stable child-only `__str__` succeeds as text. Define the class inside the target function and record hook PID evidence; assert only the child PID performed conversion and the parent did not invoke pickle hooks. Baseline fails. |
| Direct projection | Text `null`, `true`, `1`, JSON-looking text, whitespace/empty strings remain strings; None/bool/number/dict become corresponding plain values. Private runner exposes prepared records deliberately; Consumer direct callers retain plain projections. After proving the direct path invokes projection, a queue-driven real Consumer must complete with a module-owned projection hook that raises if called. |
| Output fidelity | Literal expectations for tuple-as-array, integer/string key collision, unsupported nested object causing whole-value string fallback, NaN/infinity, lone surrogate replacement, and None. Decode/re-encode mutation must fail exact outbox text checks. |
| Agent parity | Same unpicklable-but-convertible outputs under one-shot, persistent per_message, and per_task session; verify ordered zero/one/many rows, structured/messages output, usage/tool_trace/artifacts, and a successful second per_task turn. Use existing llm plugin fixtures, not a mocked AgentSession. |
| Optional metadata | Existing circular-metadata test plus non-picklable printable metadata reaches the full snapshot or exact four-field fallback. No failed task for optional conversion covered by existing fallback. Invalid required identity produces child result_serialization diagnostics in both host modes; assert it is not misreported as transport_failed. |
| Fatal preparation | Cycle in public output or stable output object whose `__str__` raises gives one bounded error/diagnostics, no public partial outputs, correct keep/clear reservation. Persistent session closes and next execute rejects as closed. Broken exception `__str__` on conversion/transport causes cannot defeat the error envelope; hardening unrelated execution-exception rendering is out of scope. |
| Raw plugin path | Existing Microsandbox/Docker raw AgentExecutionResult tests still publish through the shared preparation policy. Public extension dataclass fields/signatures remain unchanged. |
| Django isolation | Real registered task returns a child-only application value needing initialized Django for conversion; parent remains uninitialized yet receives correct data/text. Use explicit bootstrap evidence to avoid a fixture that initializes Django in the parent and hides the defect. |
| Framing and validation | Independent literal JSON frame and exact sender bytes; reject invalid UTF-8/JSON, wrong root/envelope/output/metadata type, bad status/version, invalid duration, and missing fields. Sender rejects nonfinite outer numbers; receiver has no special duplicate-key or blanket numeric scan. A malicious pickle byte sequence is rejected as JSON without invoking its reconstruction hook. Prepared output text stays opaque on the queue-driven path; test bad metadata JSON only where the log mapping must be restored. |
| Fallback/write boundary | Payload factory failure before write emits exactly one valid fallback and EOF; fallback-encode failure closes through caller cleanup and produces no second frame on the real one-shot path; broken/partial write never invokes fallback. Context exit failure replaces the unsent prepared frame with one execution-error frame. Retain real-pipe write-failure and malformed-frame tests, using an injection double only for the exact partial-write guard. |
| Lifecycle ordering | Retain `tests/core/test_terminal_handoff.py` reducer matrix and real-pipe exit-before-result, EOF-before-result, leaked-sender drain, cancellation, deadline precedence, limits, session invalidation, process reaping, and endpoint-close tests. Change codec fixtures, not the priority oracle or timing budgets. |
| Large outputs | Existing result larger than pipe capacity and `SpillPairModel` agent multi-output with slow Consumer: parent drains while producer writes; no deadlock, lost accepted output, duplicated rows, or changed spill reference. Verify producer closure before absence/count assertions. |
| Durable completion | Real Consumer publishes outbox before terminal broadcast, correct result metrics and reserved disposition; persistent work_item_completed leaves task running. CLI/client/pipeline readers see current output contracts, not private tags. |

Keep existing failures that remain in contract; do not merely delete
`return_unpicklable`/unreadable-pickle tests. Replace their obsolete pickle-only
failure premise with public-conversion and malformed-JSON probes and retain
the bounded-error/reaping assertions. Rewrite the private-payload round-trip
caller in `tests/core/test_agent_runtime.py` when removing
AgentExecutionResult.to_private_payload/from_private_payload, preserving its
agent field/output evidence in prepared-result coverage. Test-audit guidance
applies to test edits.

## Verification and Gates

Plan-only checks (run now):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
bin/check-doc-paths
git diff --check
```

Per-slice implementation checks:

```bash
. ./.envrc
./.venv/bin/python -m pytest -m "" tests/core/test_targets.py tests/core/test_agent_runtime.py tests/core/test_terminal_handoff.py tests/core/test_terminal_handoff_transport.py tests/tasks/test_runner.py tests/tasks/test_agent_execution.py tests/tasks/test_consumer_terminal_events.py tests/commands/test_observation_connections.py
./.venv/bin/python -m pytest -m "" integrations/weft_django/tests/test_weft_django.py extensions/weft_microsandbox/tests/test_microsandbox_agent_runner.py extensions/weft_docker/tests/test_agent_runner.py tests/core/test_public_extension_contracts.py
```

Final implementation gates, at normal repository concurrency:

```bash
. ./.envrc
./.venv/bin/ruff format --check weft tests integrations extensions
./.venv/bin/ruff check .
bin/mypy-check
./.venv/bin/python -m pytest -m "" tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests integrations/weft_django/tests
bin/pytest-pg --all tests/tasks/test_agent_execution.py tests/tasks/test_consumer_terminal_events.py tests/commands/test_observation_connections.py
bin/check-doc-paths
git diff --check
```

Use the repository's existing spec-hygiene and doc-path gates for traceability;
do not invent a backstitch command absent from this checkout. If a required
external service makes a check unavailable, report it as unverified rather
than claiming the suite passed. Windows and Linux CI must cover the changed
pipe/startup/cleanup tests as well as the local macOS run. Do not weaken
concurrency or load as an acceptance workaround [TS-0].

Final runtime observation: submit one structured function result and two
persistent agent turns in an isolated harness; inspect exact outbox rows,
agent metadata, work-item/terminal events and cleanup. Repeat with failing
conversion and prove no partial successful output. These use existing
helpers and are integration evidence, not a new diagnostic command.

## Theory Fit

- **Verdict:** Fits with strain: explicit inert data at the worker boundary
  improves process separation, but an internal prepared carrier adds a concept
  to preserve existing output semantics. Keep it private and owned once.
- **Rests on:** [WT-2a.2] queues remain durable truth; [WT-2a.5] process isolation;
  [WT-2a.6] immutable events between owners; [WT-4.2] one resource owner and
  existing primitives; [WT-4.4] shared conversion code and one current contract.
- **Refuses:** [ALT-WT-010] a second store/control plane and [ALT-WT-012]
  permanent compatibility readers. No pickle fallback, serializer registry,
  child publication, or guessed transport limit.
- **Predicts:** A value with usable output conversion but broken pickle hooks
  succeeds without parent application imports; breaking prepared-text handling
  changes exact outbox literals; malformed frames and conversion failures
  still settle and reap under existing bounds.
- **Fits worst:** [WT-4.4] "As few concepts as possible": the prepared value
  must earn its place by preventing parent application reconstruction and
  decode/re-encode corruption, not expand into a general serialization system.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

The reviewed spec delta is now applied with its owning code. No lifecycle
reducer, queue authority, or public extension fields changed. One implementation
clarification: only the context/preparation catch rethrows transport errors;
the inner target-only catch still classifies a user-raised transport exception
as an execution error. A regression verifies this distinction.

## Review Log

Author family: OpenAI Codex.
Reviewer family: Anthropic Claude, Tier 1; actual model `claude-opus-5-5[1m]`.
Plan Review Verdict: PASS (2026-11-13); non-blocking findings dispositioned below.
This is plan approval, not a Final Completion Review of implementation.

Reviewed baseline: repository `bd92b9c9caa47514919b2c4f88e7478c8d97b497` plus
plan content SHA-256 `6d2acd51a6696147208491d70f937d1be9e32224cf3b3d3f2b979f4bef22287c`.
The complete plan was embedded verbatim in the review prompt, with the exact
spec delta, source baseline, accepted risks, refusal constraints, and file list.

Invocation through `skills/call-agent/SKILL.md`, Claude CLI 2.1.293:

```bash
claude -p "$(cat "$REVIEW_PROMPT_FILE")" --safe-mode --permission-mode plan --tools "Read,Grep,Glob" --allowedTools "Read,Grep,Glob" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --output-format stream-json --verbose < /dev/null
```

The driver supplied an absolute temporary prompt file via an argv list, captured
stdout/stderr separately, and enforced a 540-second process timeout. Verified
init inventory: exactly Glob/Grep/Read, empty MCP inventory, permissionMode plan.
Real Read tool results succeeded. Process exit 0; final result success,
is_error false, nonempty review, permission_denials empty; stderr empty.
Containment was verified restricted tool surface, not an OS sandbox claim.
Review elapsed 146 seconds. The reviewer read the named causal path, tests,
program theory and relevant spec anchors; limits are recorded verbatim below.

| ID | Verbatim finding headline | Disposition | Operative change / evidence |
| --- | --- | --- | --- |
| F1 | Parent-side validation goes further than the trust model needs and costs memory on large outputs. | Modify | Removed duplicate-key hooks, blanket outer numeric scanning, parsing of output text, and repeated metadata schema validation. Retained metadata parse/root check because publication needs the actual mapping, and duration's existing field predicate. Wire/codec section, spec delta, and Framing test row now agree. |
| F2 | Direct-call projection is unscoped and would run on every queue-driven item. | Accept | Projection is gated by `_direct_work_waiting` and occurs before publication. Normal queue work neither parses output text nor constructs a discarded agent projection. |
| F3 | The plan states the one-shot worker's new shape but doesn't specify it. | Modify | Explicitly stage the complete frame inside the active runtime context, then write it outside execution/context catches. Context exit failures replace the unsent frame with an execute error; this preserves the baseline restoration-failure classification. Prepare/write helpers share one existing framing owner; callers still own close. Added real one-shot fallback failure and context-exit tests. |
| F4 | Checking agent identity fields in the parent turns producer bugs into `transport_failed`. | Accept | Child preparation uses exactly the existing session identity predicates. Invalid identity is result_serialization failure. Parent only restores the metadata mapping; one-shot predicate alignment is explicit in the difference register and spec delta. |
| F5 | The Consumer change can be smaller than the plan describes. | Accept | Pass prepared `.text` into existing `_emit_single_output`; leave `_prepare_output` unchanged. Prepare successful raw plugin values once before output/log use. |
| F6 | Endpoint ownership wording conflicts with the transport module. | Accept | Caller `finally` owns close; transport raises without taking endpoint lifetime. Updated operative design and delta. |
| F7 | The scope of "broken exception `__str__`" is unclear. | Accept | Test/hardening is limited to conversion and transport cause formatting. Unrelated execution-error rendering remains outside this change. |
| F8 | "Parent must neither import that class nor call its hooks" has no stated proof. | Accept | Function-local class plus child-PID hook evidence provides a falsifiable producer/receiver proof. |
| F9 | `to_private_payload` has a test caller. | Accept | Verified the assertion in `tests/core/test_agent_runtime.py`; name its rewrite/removal with the private methods while preserving independent runtime/output assertions. Added file to task and targeted gate. |

No standing invariant was reopened. Scope, endpoint/queue authority, and
lifecycle invariants are unchanged by these dispositions. Per the review
runbook, the PASS review's non-blocking corrections receive local verification,
not a second round asking the reviewer to re-approve its own findings.


### Theory Fit

Completed-work synthesis of the Tier-1 review and accepted F1 repair:

- **Verdict:** Fits with strain. Prepared text preserves public output while
  removing application object reconstruction. The cancellation repair restores
  reducer authority; the carrier remains a private implementation concept.
- **Rests on:** [WT-2a.2] queue truth, [WT-2a.5] child process isolation,
  [WT-2a.6] data between owners, [WT-4.2] one resource owner, and [EXEC.10]
  reducer ordering. Parent publication and cleanup ownership are unchanged.
- **Refuses:** [ALT-WT-010] a second state store and [ALT-WT-012] compatibility
  readers. No legacy pickle decoder, child queue publication, or configurable
  serializer was introduced.
- **Predicts:** Usable unpicklable values succeed; output text stays exact;
  malformed frames fail through transport; accepted same-turn cancellation
  wins over a ready success. Real-process and real-pipe regressions fire these
  predictions, including verified RED/GREEN cancellation evidence.
- **Fits worst:** [WT-4.4] as few concepts as possible. The tagged carrier is
  required for opaque publication plus private direct projection; redundant
  Consumer adapters and obsolete private payload methods were removed.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
| --- | --- | --- | --- | --- |
| 2026-11-13 | Reviewed plan SHA-256 above | Design, spec delta, tasks, test matrix | Dispositioned F1-F9; reduced parent work, made pre-write/context-exit ordering concrete, retained metadata/publication and endpoint owners. | No: non-blocking PASS corrections within reviewed scope; local consistency/gates required. |
| 2026-11-13 | Same reviewed baseline, post-disposition reread | Duration ownership | Corrected the persistent duration statement to match Consumer._run_task_for_reactor; no new clock/field. Added firing assertions for conditional projection and child identity diagnostics. | No: preserves the reviewed duration/lifecycle boundary; verified directly in owning code. |
| 2026-11-13 | Completed-slice review R1 | Consumer helper ownership and [AR-5] scope | Adopted F3/F4: directly consume prepared metadata, remove redundant conversion, and explicitly retain the raw-plugin publication-failure path. | No scope expansion; non-blocking corrections verified locally. Blocking F1 receives scoped round 2. |

## Fresh-Eyes Review

Author pass completed 2026-11-13 before external review. Findings and fixes:

| ID | Finding and risk | Disposition and operative correction |
| --- | --- | --- |
| S1 | Preparing session result mappings before the send guard lets conversion errors bypass the promised fallback. | Accept: the sender takes a payload factory; preparation and JSON encoding share the pre-write guard. |
| S2 | Parsing prepared JSON and then encoding it again loses duplicate-key text; changing every result to plain text loses direct-call type distinctions. | Accept: tagged prepared text is the publication authority; direct-call projection is separate and precedes durable success. |
| S3 | Moving a send inside the runtime context could let its exception reach an execution catch that sends again. | Accept: keep transport exceptions outside that handler; a failed write never enters the execution-error send path. |
| S4 | Post-disposition reread found that the duration sentence incorrectly treated persistent duration as child-reported. | Accept: verified `Consumer._run_task_for_reactor`; retain its elapsed session.execute measurement, and keep worker duration only for one-shot responses. |

The whole-plan pass checked metadata fallback, plugin compatibility, output
cardinality, runtime context lifetime, and rollback. Remaining risk: the private
carrier crosses multiple adapters and needs the explicit real-process matrix.
The Tier-1 review above has now completed. After dispositions, the author
rechecked the whole operative plan for stale instructions: output text remains
opaque, metadata is parsed only for its log mapping, raw plugins prepare once,
and stage-before-write preserves context-exit failure handling. These author
checks do not substitute for completed-work review during implementation.

## Execution Log

Implementation authorized by the user on 2026-11-13. Three native OpenAI
implementation slices owned prepared results/Consumer, real agent/Django probes,
and spec promotion plus runner probes. The primary agent owned transport and
protocol adapters. The pre-existing `skills/call-agent/SKILL.md` edit is outside
this work and was preserved.

RED evidence (real spawned processes, default repository concurrency):

- `test_host_runner_accepts_child_local_json_value`: `assert outcome.status ==
  "ok"` failed with `Can't pickle local object ... LocalMapping`; transcript
  `/tmp/weft-function-json-red.log`.
- Agent one-shot/per-message/per-task: expected completed/running but got failed
  with `agent fixture values must not be pickled`; transcript
  `/tmp/weft-agent-handoff-red.log`. These traversed the unchanged pickle owner;
  the independently prepared Consumer edits were already present but had not
  altered this failure boundary.
- Isolated Django parent: subprocess exit 1 because `Consumer.run_work_item`
  raised `Can't pickle local object ... ChildResult`; transcript
  `/tmp/weft-django-handoff-red.log`. Parent registry stayed uninitialized.

Initial focused gate: 1,028 passed, 14 skipped, four failed. All four were new
persistent test filters expecting `work_completed` instead of the specified
`work_item_completed` ([AR-5], [MF-2]). Corrected the test to the exact per-mode
event; eight new agent cases then passed. No product behavior, timeout, or
concurrency was changed to obtain that result.

Implementation includes two bounded exception scopes in the worker and a local
exception-formatting guard. Three complexity warnings were eliminated by
cohesive target dispatch and field-validation helpers. Existing SUP342 grows
from one to two execution/context catches; new SUP379 protects an exception
whose `__str__` fails. Native independent review endorsed the local SUP379
catch over incomplete exception lists or cosmetic logging; Tier-1 review also
receives these exact changes before final adoption.

Final runtime/backend/platform results and completed-work review are recorded
in the closeout below.

Plan-only validation on 2026-11-13: the named plan-metadata/spec-hygiene
command passed (16 tests); `bin/check-doc-paths` reported OK; `git diff --check`
passed. The initial spec-hygiene run caught status wording in the new backlinks;
that wording was removed and the same gate passed. Product implementation and
its runtime/platform/backend gates have not been run for this plan-only task.

## Verbatim Tier-1 Review

> # Review: JSON result handoff plan (`docs/plans/2026-11-13-json-result-handoff-plan.md`)
>
> **Verdict: PASS.** The design is sound and the lifecycle risks are understood. All findings below are P2/P3 corrections, and most of them *remove* work. None needs a standing invariant or a refused alternative reversed. Before the implementation slice starts, the plan should take on F1–F4.
>
> ## Findings
>
> **F1 [P2] — Parent-side validation goes further than the trust model needs and costs memory on large outputs.**
> - **Where:** "Private response schema and ownership". The plan asks for duplicate-key and nonfinite rejection on the outer document, for validating JSON-form output text "as valid", and for validating the metadata against the full-snapshot or four-field shape.
> - **Why it matters:** The plan itself says the pipe is not a trust boundary ([WT-2a.5], single-operator model). Every inner text is produced by the child's own `json.dumps`, so it cannot be invalid unless Weft has a bug.
>   - Parsing each output in the parent means a full `json.loads` of results that can exceed pipe capacity. That multiplies parent memory on exactly the large-output path the plan says it won't redesign.
>   - Rejecting duplicate keys in Python requires `object_pairs_hook`. That contradicts "no object hooks" in the same section and in the Difference Register.
> - **Suggested disposition:** Keep `allow_nan=False` on the **encode** side (the sender's own strictness). On receive, validate only:
>   - the object root,
>   - exact envelope keys and discriminants,
>   - field types, with booleans rejected as integers,
>   - output `format`/`text` types.
>
>   Drop the duplicate-key and nonfinite checks on the outer document, and drop the inner-text parse. Change the Framing row in the test matrix to match.
>
> **F2 [P2] — Direct-call projection is unscoped and would run on every queue-driven item.**
> - **Where:** `weft/core/tasks/consumer.py:351-355` (`_commit_work_outcome` returns the value on every path). It is stored only when `_direct_work_waiting` is set (`consumer.py:615-616`). The plan says projection "must be computed and validated before durable success publication".
> - **Why it matters:** Done unconditionally, every queue-driven result pays for a `json.loads` plus building an `AgentExecutionResult` that is then thrown away. A full-tree search found no production callers of `run_work_item`; only tests use it.
> - **Suggested disposition:** Compute the projection before `_finalize_message`, and only when `_direct_work_waiting` is set. Say so in the plan and in the CC-3.5 delta sentence. You could also note that `json.loads` of the child's own `json.dumps` text cannot fail in practice, so the "cannot fail after publication" guarantee is about where the call sits, not a new validation step.
>
> **F3 [P2] — The plan states the one-shot worker's new shape but doesn't specify it.**
> - **Where:** `weft/core/runners/host.py:170-257`. Today the send happens after `_worker_runtime_context` exits, and outside the execution `try/except Exception`.
> - **Why it matters:** The plan requires three things at once:
>   1. conversion runs inside the runtime context;
>   2. conversion and encoding run inside the send helper's factory guard;
>   3. send exceptions never reach the execution handler.
>
>   Taken together, the send call has to move into the `with` block, and that block currently sits *inside* the execution `try`. `TerminalHandoffTransportError` subclasses `RuntimeError`, so a naive move gets caught by `except Exception` and re-sent. That is exactly author finding S3. Entering the context can also fail (e.g., `chdir`), and that failure is classified as `execute` today.
> - **Suggested disposition:** Add one sentence of structure:
>   - enter the context;
>   - use a narrow execution `try` around the target call and around context-entry failure, keeping today's classification;
>   - capture `end` before conversion;
>   - call `send_terminal_payload(factory)` inside the context but after the execution `except`.
>
>   Add a test that a fallback-encode failure produces no second frame on the one-shot path. The session path (`host.py:352-364`) already has the right shape.
>
> **F4 [P2] — Checking agent identity fields in the parent turns producer bugs into `transport_failed`.**
> - **Where:** "Validate agent metadata text as an object with the required runtime/model/output_mode/status identity fields…" and "invalid required identity fields remain explicit failures".
> - **Why it matters:**
>   - Today the one-shot path (`consumer.py:1049-1077`) never fails on identity fields. Only the session parser (`runtime.py:103-117`) checks them.
>   - Putting the check in the parent's receive step reports an adapter returning a bad `runtime` as an opaque transport failure. It loses the bounded `result_serialization` diagnostics.
>   - Any check stricter than what the child preparation guarantees could reject results that succeed today.
> - **Suggested disposition:** Run the identity check in the **child** preparation, using exactly the existing `from_private_payload` predicates (`runtime.py:103-117`), so a failure becomes the `result_serialization` fallback. The parent then checks only that the snapshot text is a string. Parsing it with `json.loads` is already needed to build the `agent_execution` dict.
>
> **F5 [P3] — The Consumer change can be smaller than the plan describes.**
> - **Where:** `consumer.py:954-992`.
> - **Why it matters:** `serialize_result(str)` returns the string unchanged (`targets.py:144-145`). If `_emit_result` simply passes each `PreparedOutput.text` to the existing `_emit_single_output`, the output bytes, spill, and stream behaviour are already preserved. Double-quoting cannot happen. No change to `_prepare_output` is needed.
> - **Suggested disposition:** Say that `_emit_result` unpacks prepared outputs and passes their text, and that `_prepare_output` stays unchanged. Drop "accepts an already prepared text item".
>
> **F6 [P3] — Endpoint ownership wording conflicts with the transport module.**
> - **Where:** "raise `TerminalHandoffTransportError`, close the endpoint". The module docstring at `terminal_handoff_transport.py:3-4` says the helpers "do not own endpoint lifetime". Callers already close the endpoint in `finally` (`host.py:256-257`, `host.py:400-403`).
> - **Suggested disposition:** Replace with "raise; the caller's existing `finally` closes".
>
> **F7 [P3] — The scope of "broken exception `__str__`" is unclear.**
> - **Where:** The test matrix's Fatal preparation row, and the hardening of `bounded_terminal_handoff_cause`.
> - **Why it matters:** The execution-error paths also call `str(exc)`, at `host.py:209` and `host.py:343`. If the row covers execution exceptions, those calls need the same guard. If it doesn't, say so, so nobody writes a test that can't pass.
> - **Suggested disposition:** Limit the row to conversion and transport causes, or list the two execution sites as in scope.
>
> **F8 [P3] — "Parent must neither import that class nor call its hooks" has no stated proof.**
> - **Where:** The test matrix's Function conversion row.
> - **Suggested disposition:** Have the hook record `os.getpid()` (or write a marker file) and assert that only the child PID appears. Prefer a class defined inside the target function, which the parent cannot import by construction.
>
> **F9 [P3] — `to_private_payload` has a test caller.**
> - **Where:** `tests/core/test_agent_runtime.py:259-260`.
> - **Why it matters:** The plan's conditional removal ("if full-tree search confirms…") will find this caller.
> - **Suggested disposition:** Name it as a test that gets deleted or rewritten together with the methods, so it doesn't block removal.
>
> ## Observations (not actionable this pass)
> - **Django connections:** `run_registered_task` calls `close_old_connections()` in `finally` before the child converts the result (`worker.py:76-79`). A model `__str__` that lazily touches the database will reopen a connection that nothing closes until the child exits. This is harmless for a one-shot worker. Recommending explicit data stays the right guidance.
> - **Protocol version bump:** Bumping `AGENT_SESSION_PROTOCOL_VERSION` is cheap. It is redundant with the codec change, since an old/new mismatch already fails as a JSON decode error. Keep or drop it; either is fine.
> - **Theory ID:** `[WT-4.4]` isn't written as a literal anchor. It resolves to the section-4.x principle "As few concepts as possible" (`docs/program-theory.md:487`), which is acceptable.
>
> ## Theory Fit audit
> - **Rests on:** [WT-2a.2], [WT-2a.5], [WT-2a.6] and [WT-2a.9]/[ALT-WT-012] all hold. There is no legacy pickle decoder, no negotiation, and the rollout is one coordinated slice.
> - **Refuses:** [ALT-WT-010] holds (no new store or lane). [ALT-WT-017] holds: the plan says not to lower concurrency.
> - **Lifecycle:** [EXEC.5]–[EXEC.10] and [CC-3.5] ordering are preserved. The reducer, the drain/EOF/exit separation, and the "one fallback before write, never after" rule are untouched. Malformed frames still go to `transport_failed` at `host.py:645-660` and `sessions.py:646-658`.
> - **"Fits worst" is honest:** F1, F2 and F5 are what keep the prepared carrier from growing into the general serialization system the plan warns against.
> - **Predictions are falsifiable:** I checked the baseline premise. An unpicklable value hits the pickle fallback at `terminal_handoff_transport.py:57-69` and becomes an error outcome, so the baseline RED is a real assertion failure. The outbox-literal checks would catch any decode/re-encode.
>
> ## Verification limits
> - I used only native reads. I ran no tests, did not execute the plan's checks, and did not run git.
> - Not inspected: the Docker/Microsandbox runner internals beyond their test assertions; `tests/helpers/leaked_sender.py`; spec text beyond the anchors cited.
> - Output-byte equivalence is argued from `serialize_result` and the `_emit_single_output` source, not observed.
>
> ## Verdict derivation
> - **Q1 — could I implement it confidently as written?** Yes. F3 is the only real ambiguity, and the plan's own invariants pin down the correct shape.
> - **Q2 — would it meaningfully degrade robustness?** Only if F1, F2 and F4 are followed literally: parent memory on large outputs, and a new failure class for identity fields. Each fix is a deletion or a move into the child. None of it needs reconsidering a standing invariant.
>
> **PASS**


## Completed-Slice Review (2026-11-13)

Tier-1 Anthropic Claude `claude-opus-5-5[1m]`, via the same restricted CLI
invocation above. Exact code/spec delta and complete plan embedded verbatim;
native Read/Grep/Glob results succeeded. Init tools exactly those three, no
MCP servers, permissionMode plan. Exit 0, success result, is_error false,
permission_denials empty, stderr empty; elapsed 243 seconds. Raw evidence:
`/var/folders/m_/2tncpj593tj8s_jdbdhj8g5m0000gn/T/weft-json-completed-review-lxzih35q`.

This review's F IDs are local to this round, independent of the earlier plan
review's F1-F9. Initial verdict: `blocker: F1`. The author verified every claim
against the current code before disposition.

| ID | Disposition | Change and verification |
| --- | --- | --- |
| F1 [P1] | Accept | The decoder had reused the loop's final `outcome` variable. A real-pipe ready-success plus accepted-cancel probe failed exactly `ok != cancelled`, after proving stop occurred (`/tmp/weft-json-cancel-red.log`). Renamed decoded evidence to `parsed_outcome`; the reducer alone sets final outcome. The same regression passes in the 418-test correction gate. Scoped Tier-1 round 2 is required. |
| F2 [P3] | Accept | `make_result_response` now requires prepared kind=agent inside the child factory guard; malformed runtime results become result_serialization failures. The wrong-result-type session probe passes. |
| F3 [P3] | Modify documentation | Clarified [AR-5] and the operative delta: raw non-host plugin conversion errors retain Consumer's existing publication-failure path; bounded response diagnostics belong to host children. No new Consumer exception policy. |
| F4 [P3] | Accept | Deleted unused callable fixture, redundant prepared-result adaptation, dead metadata wrapper and its superseded test, stale no-cover annotation, unreachable startup-message fallback. Shared helper policy and real agent metadata fallback probes remain. |
| F5 [P3] | Accept | Added a valid JSON object with an invalid outcome envelope to the real adapter test, proving parse failure becomes transport_failed. |
| F6 [P3] | Accept | SUP342 names its new entry/exit proof and reviewed second catch; SUP379 records independent endorsement. Registry index and its exact authorization inventory were synchronized. |

The reviewer endorsed both narrow suppressions after considering consolidation,
exception narrowing, and cosmetic logging. No standing invariant was reopened.
Post-disposition source checks: Ruff formatting/lint pass; mypy passes all 533
files; correction gate 418 passed, 12 explicit provider/PG skips. An initial
correction run exposed a synthetic malformed-result worker relying on an empty
successful helper response; it now constructs the malformed wire explicitly.
A markdown pipe in a registry test name was also removed to keep the row parseable.

First broad gate: 7,498 passed, 68 skipped, six failures. Two private runtime
identity assertions needed prepared projection and the suppression authorization
inventory needed the new group; those change-caused failures pass in the
correction gate. Three remaining failures were reproduced on unchanged HEAD
in isolated archive `/tmp/weft-json-baseline.K2bkvY`:

- SQLite fork provenance is sensitive to prior optional `simplebroker_pg`
  import in the pytest worker: both modes fail at the same module-provenance
  comparison with a preload, pass without it. Baseline log:
  `/tmp/weft-json-baseline-pg-preimport.log` (2 failed); no production failure.
- Django `inherited-settings` fixture cleanup fails when integration test
  collection supplies Django settings/module paths. Pytest introspection of a
  module-global LazySettings object binds inherited paths before fixture setup.
  Baseline command collected tests plus the Django integration module and
  selected the fork/cleanup probes: 1 failed, 5 passed with the same root-path
  mismatch; without integration collection all six pass. Baseline log:
  `/tmp/weft-json-baseline-django-collected.log`.

These pre-existing test isolation defects remain outside this JSON handoff slice;
no expectations, concurrency, or budgets were weakened. PostgreSQL gate passed
85 tests with 11 opt-in live-provider skips. Final broad rerun and scoped review are recorded in the closeout below.

### Completed-slice review, verbatim

> **Verdict: blocker: F1.** It's a one-line rename that is easy to prove with a test. Everything else is P3.
>
> ## Actionable findings
>
> ### F1 [P1] One-shot handoff can return success after a same-turn cancel was accepted
> - **Where:** `weft/core/runners/host.py:677`, in `HostTaskRunner._run_one_shot_terminal_handoff`.
> - **What happens:** The delta added `outcome = parse_outcome_payload(payload)`. But `outcome` is also the loop's exit variable (`while outcome is None:` at `:635`, declared at `:632`). Normally only the reducer's `return_*` branches set it (`:809`–`:893`).
> - **How it fails:** Suppose a frame arrives in the same turn as `cancel_requested`.
>   - The one-shot order ranks `cancel_requested` above `outcome_received` (`_constants.py:540-549`), so the reducer picks `stop_for_cancel`.
>   - The adapter calls `_stop_process` and `continue`s (`:801`).
>   - The `while` condition is now false, so the loop ends and `:915` returns the parsed `ok` outcome.
>   - The accepted cancel is discarded. The same thing happens in the `draining` state with cancel plus an outcome.
>   - This breaks [CC-3.5] and [EXEC.10] and the standing terminal-priority constraint. In this case, results are published for a cancelled task.
> - **Baseline:** Before the delta, the code only built `TerminalHandoffEvent(outcome=payload)` and never assigned `outcome`. The reducer then reached `return_cancelled` through `channel_sealed` or `drain_expired`. The session adapter (`sessions.py:650-668`) does not have this problem.
> - **Suggested disposition: Accept.** Rename the local, e.g. `parsed = parse_outcome_payload(payload)` and `outcome=parsed`. This needs no other change and no reducer edit.
> - **Test oracle:**
>   - Use a real spawn `Pipe` and write one frame from `make_outcome_payload(RunnerOutcome(status="ok", …))`.
>   - Use the existing fake alive `Process` pattern (`test_runner.py:5083`), with `_stop_process` monkeypatched to mark the process dead.
>   - Call `_run_one_shot_terminal_handoff(..., cancel_requested=lambda: True)`.
>   - Assert `status == "cancelled"`. The current code returns `"ok"`.
>   - Existing cancel tests can't catch this because they use an `EmptyReceiver`.
>
> ### F2 [P3] A non-agent persistent session result becomes an opaque `transport_failed`
> - **Where:** `make_result_response` in `agent_session_protocol.py:158-176` and `parse_result_response` at `:207-209`.
> - **What happens:** If a session adapter returns something other than an `AgentExecutionResult`, the child prepares it as `kind="value"` and sends `ok`. The parent then raises `ValueError` and records `transport_failed`.
> - **Baseline:** `.to_private_payload()` raised `AttributeError` in the child, which produced a bounded execute-phase error. This is the producer-bug-into-transport case that plan F4 removed for identity checks.
> - **Suggested disposition: Modify.** For `status="ok"`, reject `kind != "agent"` in the child, inside `make_result_response`. The factory guard then produces the `result_serialization` fallback. Keep the parent check.
> - **Test oracle:** Reuse `test_agent_worker_invalid_identity_emits_serialization_error` with a session whose `execute` returns `"text"`. Expect `diagnostics.phase == "result_serialization"`.
>
> ### F3 [P3] Raw-plugin identity failure in the Consumer is unbounded
> - **Where:** `consumer.py:348` → `prepare_agent_execution`.
> - **What happens:** A raw non-host `AgentExecutionResult` with an invalid `runtime`/`model`/`output_mode`/`status` now raises `TypeError`. It escapes `_handle_active_work_result` (`:626` re-raises on the queue path). Before the delta, the Consumer published it.
> - **Spec mismatch:** The [AR-5] delta says non-host results "use the same policy in Consumer". Here that produces a raised exception, not a bounded `result_serialization` failure.
> - **Real-world reach:** Docker and Microsandbox go through `build_provider_cli_execution_result` with fixed identity, so in practice this can't be reached. Raw-path cyclic outputs already failed the same way before the delta.
> - **Suggested disposition: Accept as documented.** Narrow the [AR-5] sentence to "host children produce a bounded error; Consumer raw-plugin failures follow the existing publication-failure path". Don't add new Consumer error handling.
>
> ### F4 [P3] Leftover dead or redundant code
> - **Suggested disposition: Accept** (deletions only).
> - `tests/tasks/sample_targets.py::return_unpicklable` has no callers left; its docstring was edited but nothing uses it. Delete it.
> - `Consumer._emit_result` calls `prepare_task_result` again (`:969`), but its only caller already passes a prepared value. Type the parameter as `PreparedTaskResult` and drop the call.
> - The `AgentExecutionResult` branch of `_build_agent_execution_payload` is reached only from `test_agent_execution.py:107`. `test_targets.py::test_public_output_cycle_is_fatal_but_agent_metadata_cycle_falls_back` now proves the same fallback. Either point that test at `prepare_agent_execution` and inline `prepared.agent_execution`, or keep it per the plan; it's harmless either way.
> - `terminal_handoff_transport.py:79`: `# pragma: no cover - fixed envelopes` is now stale, because `test_function_worker_failed_fallback_closes_pipe_without_frame` covers that line.
> - `startup_error_message`'s fallback string (`agent_session_protocol.py:144-145`) can't be reached now that `response_type` requires a nonempty error.
>
> ### F5 [P3] The adapter-level "valid JSON, invalid envelope" path has no end-to-end proof
> - **What's missing:** `test_host_malformed_terminal_frame_is_transport_failure` uses `b"42"` and `b'{"type":'`. Both fail inside `receive_terminal_payload`. The new `parse_outcome_payload` → `transport_failed` branch at `host.py:678` is only unit-tested through the parser.
> - **Suggested disposition: Accept.** Add a parametrized frame such as `b'{"type":"outcome"}'` and assert `handoff_event == "transport_failed"`.
>
> ### F6 [P3] Registry row SUP342 is out of date
> - **Where:** The SUP342 row in `docs/ruff-suppression-registry.md`.
> - **What's wrong:**
>   - The proof column still describes only the original catch. It doesn't cite `test_function_worker_context_failure_replaces_unsent_result[entry|exit]`, which is what fires the new second directive.
>   - The approval column still reads "owner approved 2026-08-07" for one directive.
> - **Suggested disposition: Modify.** Cite the context-failure test, and record this review and the owner's approval for going from one directive to two.
>
> ## Suppression judgment
> - **SUP342 (one → two directives):** Endorse.
>   - **Inner catch (target only):** this is what keeps a user-raised `TerminalHandoffTransportError` classified as an execution error, and the regression test proves it.
>   - **Middle catch:** it covers identity install and context entry/exit, and sits behind `except TerminalHandoffTransportError: raise`.
>   - **Why not merge them:** a single catch would either reclassify user transport errors or let a failed fallback re-enter execution handling. It would also re-prepare a frame, which the plan forbids.
>   - `prepare_frame` can only raise `TerminalHandoffTransportError` or a `BaseException`, because `prepare_terminal_payload` absorbs everything else. So the ordering is sound.
> - **SUP379:** Endorse.
>   - An application `__str__` can raise anything, so no narrower exception tuple covers it.
>   - The fallback, `type(exc).__name__`, can't fail.
>   - Logging would add another operation that can fail.
>   - Both `test_terminal_preparation_bounds_an_unprintable_conversion_error` and the `return_unprintable_result` probe fire it.
>   - Once dispositioned, update the "review pending" text in the row.
>
> ## Checks that passed
> These match the plan:
> - **Context lifetime:** conversion runs inside the runtime context and the write happens after it exits. A context-exit failure discards the unsent frame and prepares one execute-error frame. A test observes the env/cwd ordering.
> - **Single write:** `send_prepared_terminal_payload` never retries, and the partial-write test confirms it. In the session path, conversion and the fallback share the factory guard, and `if not original_sent: break` closes the session.
> - **Producer exit vs EOF:** the reducer and the drain logic are untouched (F1 aside).
> - **Duration:** one-shot duration is captured before conversion, and the persistent duration clock stays in the Consumer.
> - **Projection gating:** projection is guarded by `_direct_work_waiting` and runs before `_emit_result` and `_finalize_message`. The forbidden-hook test makes this falsifiable.
> - **Exact text:** `_prepare_output` is unchanged, and `serialize_result(str)` is the identity, so text isn't quoted twice. Duplicate-key and NaN literals are pinned.
> - **Metadata detachment:** the round-trip happens in the child, and the fallback keeps four fields. Moving the identity check into the child matches plan F4.
> - **Malformed input:** the root, envelope, and record shapes are checked, booleans are rejected as integers, and the duration predicate is enforced. The pickle-hook test proves the receiver never reconstructs objects.
> - **Removed tests:** the pickle-only fixtures were replaced with real conversion and malformed-JSON probes. Lost: the CLI end-to-end "channel failed" category. A JSON sender can't produce it without injection; renderer coverage stays at `tests/commands/test_run.py:271`, plus the runner-level tests.
>
> ## Theory fit
> - **Verdict:** Fits with strain, as the plan said, once F1 is fixed. With F1 in place, the delta breaks [EXEC.10] priority, so it doesn't fit as it stands.
> - **Rests on:**
>   - [WT-2a.2]: the parent still publishes, and the pipe carries evidence only.
>   - [WT-2a.5]: application objects stay in the child (Django isolation is proven by `ready_after is False`).
>   - [WT-2a.9] / [ALT-WT-012]: no pickle decoder, the version bumps, and old and new endpoints can't be mixed.
> - **Refuses:** [ALT-WT-010] (no new lane or registry) and [ALT-WT-012] (no compatibility reader). Both hold.
> - **Predicts:** Unpicklable values that convert cleanly succeed in all four modes; the RED evidence above is credible. Changing prepared text would break the literal outbox checks. Malformed frames still end in `transport_failed`.
> - **Fits worst:** [WT-4.4], "As few concepts as possible". The prepared carrier stays private and has a single owner; F4 removes the remaining redundant re-preparation.
>
> ## Observations (not actionable this pass)
> - JSON nested inside JSON with `ensure_ascii` makes the in-memory frame bigger: about 2× for quote-heavy JSON text and up to 6× per character for non-ASCII. That's within the accepted whole-payload buffering risk.
> - `test_function_worker_failed_fallback_closes_pipe_without_frame` patches the global `json.dumps` through `transport_module.json`. It's acceptable in-process, but it would affect any concurrent code in the same test process.
> - The protocol version bump is redundant with the codec change, as the plan review already noted. Keeping it is fine.
>
> ## Verification limits
> I used only Read, Grep, and Glob. I ran no tests, CI, mypy, or git, and I didn't create the plan file because you asked for no file writes. I found F1 by reading the code, not by running anything; I traced it through the transition table and the event order. I inspected the Docker and Microsandbox runners only where they build agent results. I read the Django test from the delta and didn't run it.


## Final Completion Review and Verification

Reviewer family: Anthropic Claude, Tier 1; completed-slice review plus scoped F1 PASS.

Final Completion Review Verdict: APPROVE

Implementation complete in the uncommitted worktree. This verdict concerns the
JSON handoff delta, not a claim that all repository tests or remote CI passed.
Final reviewed source/spec worktree identifier: base
`bd92b9c9caa47514919b2c4f88e7478c8d97b497` plus scoped diff SHA-256
`cce48daa0f397914a35e7f4f13f98a0d36fb082b8864727dd7e4d5ed01cb0b81`.

Tier-1 scoped round 2: Anthropic Claude `claude-opus-5-5[1m]`, PASS on accepted
blocking F1 and defects introduced by its repair. Verified actual native reads,
exactly Read/Grep/Glob tools, no MCP servers, permissionMode plan, exit 0,
success/is_error=false, no permission denials, empty stderr; 52.47 seconds.
Evidence directory: `/var/folders/m_/2tncpj593tj8s_jdbdhj8g5m0000gn/T/weft-json-final-review-cu5v9pfz`.
No unresolved in-scope findings remain. F2-F6 were closed through the recorded
local corrections; round 2 did not reopen them. Author clarification of one
review narrative detail: on Windows the poll helper normalizes BrokenPipeError
to readiness and receive normalizes it to EOF; accepted cancellation still wins.
This does not alter the review's primary trace or require a code change.

Final checks at normal repository concurrency:

| Check | Result / evidence |
| --- | --- |
| Primary owner/integration gates | 1,034 passed / 14 skipped; 186 passed / 1 skipped. |
| Post-review affected paths | 418 passed / 12 skipped, including same-turn cancellation, malformed envelope, child invalid-result diagnostics, Consumer projection and runtime identity. `/tmp/weft-json-review-fixes-green.log`. |
| Full all-markers core + extensions + Django | 7,503 passed, 68 skipped, 3 failures, all the independently reproduced baseline test-isolation defects above. `/tmp/weft-json-full-final.log`. No JSON handoff regression remains. |
| PostgreSQL planned gate | 84 passed, 11 opt-in live-provider skips. `/tmp/weft-json-pg-final.log`. |
| Mypy | All 533 source files pass. `/tmp/weft-json-mypy-final.log`. |
| Ruff format/lint and suppression index | Pass. SUP342/SUP379 explicit review and authorization inventory synchronized. |
| Plan/spec metadata, doc paths and whitespace | Final closeout gate: 16 tests pass; doc paths and whitespace pass. |
| Linux / Windows | Current tree not executed remotely. `.github/workflows/test.yml` runs changed core/commands/tasks/CLI tests across Ubuntu/macOS/Windows and Python 3.12-3.15. Local evidence is macOS Python 3.14.9 only. |

The final runtime observation requirement is exercised by real Consumer and
spawned-runtime tests: structured function projection/publication, two persistent
agent turns with exact outputs/metadata/events, and fatal conversion with no
successful output and closed session. No new provider network dependency,
worker-count reduction, product deadline increase, or retry masking was used.
The two baseline isolation defects are named follow-ups, not silently accepted
changes to their tests. The pre-existing skill edit remains untouched. No commit,
push, deployment, or CI dispatch was performed.

### Scoped round 2, verbatim

> # Round 2 review: F1 is fixed. Verdict: PASS
>
> ## F1 status: fixed
> - **The code matches the claimed diff.** The live `weft/core/runners/host.py:677` and `:686` match it. The decoded frame is now bound to `parsed_outcome` and goes into the reducer only as `TerminalHandoffEvent(outcome=parsed_outcome)`.
> - **Only the reducer branches set the loop variable now.** The only assignments to `outcome` are the declaration at `:632` and the reducer `return_*` branches at `:809–:867`. Any `continue` that leaves `outcome` unset therefore keeps the loop going, so the `stop_for_*` path at `:801` can no longer exit with the decoded payload.
> - **This restores the pre-delta invariant** that the reducer alone decides the final outcome. No reducer, ordering or timeout code changed. I checked `terminal_handoff.py` and `_constants.py:263–561`.
>
> **Post-fix trace of the regression (`tests/tasks/test_runner.py:5099`):**
> 1. **Turn 1.** The same turn produces `cancel_requested` and `outcome_received`, and the process is still alive. The one-shot order (`_constants.py:540`) ranks cancel first, so the step is `observing → stopping_cancel` with action `stop_for_cancel`. The drain deadline is set, `_stop_process` marks the process dead, and the loop continues with `outcome is None`. The decoded frame is dropped. That is harmless, because `stopping_cancel + outcome_received → return_cancelled` (`:425–431`) anyway. The pre-delta code dropped it the same way.
> 2. **Turn 2.** Cancel is no longer eligible (`accepted_stop` is set). The writer is closed, so the next receive raises EOF (`channel_sealed`), and the dead process produces `producer_exited`. `channel_sealed` ranks above `producer_exited`, so `stopping_cancel + channel_sealed → return_cancelled` (`:439–445`). The result is `status="cancelled"` with `value=None`.
> 3. **Teardown.** The process is not alive, so it gets `join(0.2)` and the cancelled outcome is returned.
>
> **Why the test is a real check:**
> - **It fails on the old code.** Under the old code, turn 1 set `outcome` to the parsed `ok`, `stop_for_cancel` continued, and the loop exited returning `ok`. That matches your RED report (`ok != cancelled` after `process.alive is False` passed).
> - **It uses a real frame.** It sends a real spawn-context `Pipe` frame built with `make_outcome_payload`/`send_terminal_payload`, which closes the `EmptyReceiver` gap from round 1.
> - **It doesn't depend on timing.** The timeout is 5 s, the frame is written before the call, and turn 1 needs no wait.
> - **It should hold on Windows too, by reading only.** There, `poll` raises `BrokenPipeError` after the writer closes (documented at `terminal_handoff_transport.py:115–120`). That becomes `transport_failed`, and `stopping_cancel + transport_failed` also gives `return_cancelled` (`:474–480`).
>
> ## Defects introduced by the fix: none
> - **No unbound use.** `parsed_outcome` is read only inside the `else` branch where it was assigned.
> - **No other shadowing.** No other local in the loop body is named `outcome`.
> - **No change to other paths.** Timeout, limit, drain and protocol-failure behaviour is identical. The fix only removes the early-exit side effect.
> - **The test changes nothing outside itself.** It monkeypatches `_stop_process` on the runner instance only, and `finally` closes both pipe ends (closing the sender twice is harmless).
> - **The session adapter doesn't have this bug.** `sessions.py:662` and `:708` use separate names (`SessionExecutionResult(...)` passed as the event outcome, and `session_outcome`), so the same mistake isn't present there.
>
> ## Observations (no action needed)
> - **Optional extra test.** The draining-state version (producer exits first, then cancel and a ready frame arrive in the same turn) goes through the same fixed lines and isn't pinned separately. It would be a cheap parametrization, but it isn't needed: the root cause is a single assignment and it's now gone.
>
> ## Verification limits
> - I used only Read and Grep, on the live working tree. I ran no tests, mypy, Ruff, git or CI.
> - I accepted your RED/GREEN logs as reported and didn't inspect `/tmp`.
> - I didn't re-check whether the worktree base is exactly `bd92b9c9` beyond what the session context shows.
> - Your full all-markers and PG reruns are still in progress, so I'm not claiming the suite is green.
> - I didn't reopen F2–F6.
>
> **Verdict: PASS**


Closeout metadata reconciliation: the first documentation-only closeout check
caught an index row still marked draft and the completion review's Theory Fit
account outside the canonical Review Log section. The index and review metadata
were synchronized and the same 16-test gate was rerun; runtime code was unchanged.
