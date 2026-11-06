# Command Stdout Preservation

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3]; docs/specifications/06-Resource_Management.md [RM-5.2]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2.2]; docs/specifications/12-Pipeline_Composition_and_UX.md [PL-4.1]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class: 5, risky. Changes command result values and CLI framing across the runner,
queues, result readers and pipelines. Promotion strategy B: spec and implementation
change atomically. This is a new plan; completed output-fidelity plans stay immutable.

## Goal

Preserve command stdout regardless of exit status: no stripping, newline insertion,
JSON type inference, or newline normalization. `echo hello` emits `hello\n`;
`printf hello` emits `hello`. Diagnostics remain on stderr and failures remain
nonzero. Function and agent result values retain their existing rendering.

## Scope decision

The owner explicitly chose the least surprising Unix-tool behavior, then selected
"Preserve text exactly, including CRLF; keep this change focused."
`RunnerOutcome.stdout` remains text under the configured codec/error policy.
Arbitrary binary stdout is outside this change, not a deferred implementation item.
The exact delta and independent review gate implementation of this text slice.

## Source documents and baseline

Spec baseline: `5661fd1816d9bec356db5d937c4d6743d0492ff5`, plus explicitly excluded
concurrent Manager/custody/governance changes. Relevant guidance: program theory
[WT-2a.2], [WT-2a.7], [WT-2a.9], [WT-2a.10], [WT-4], [WT-6], [WT-7];
agent-context decision hierarchy [DOM-15], engineering principles, writing-plans,
hardening-plans, testing-patterns and review-loops-and-agent-bootstrap runbooks.
The completed result-output-fidelity plan is historical context, not current
implementation authority for this follow-up.

## Context and ownership

- `weft/core/runners/subprocess_runner.py` and `weft/core/runners/host.py` own capture: success currently strips
  stdout; `_OutputStreamState` normalizes CRLF/bare CR through its decoder.
- `weft/core/tasks/consumer.py` and `weft/core/tasks/interactive.py` own command output publication. Captured success
  currently publishes ordinary values; failures use validated `failed_output`.
  `_emit_live_stream_chunk` adds `result_transform: strip` to stdout finals.
- `weft/core/outbox.py` owns shared decoding. Ordinary JSON-looking stdout can
  become a number, boolean or structured value. Stream origin alone cannot identify
  command text because function results can also be streamed.
- `weft/commands/_result_wait.py`, `result.py`, `events.py` and
  `weft/core/task_evidence.py` own collection and projections. `types.py` and
  `run.py` carry public result metadata.
- `weft/commands/_output.py`, `weft/commands/_streaming.py`, `weft/cli/run.py` and `weft/cli/app.py` own rendering.
  Command origin must survive to these boundaries; function strings must not lose
  their existing presentation newline.
- `weft/core/tasks/pipeline.py` owns zero-copy handoff. `weft/core/targets.py`
  owns work-message decoding, also used by `weft/core/tasks/interactive.py`.
  Command transport must unwrap there without rewriting the queue handoff.

Implementation constraints: move the existing pure spill-shape recognizer from
`commands/_output.py` into the shared core decoder owner; reuse it for successful
transport validation and delivery recognition. Keep host/root/filesystem checks in
commands. Do not duplicate structural validators or import commands from core.
Use the actual work result's live-streaming flag; a requested stream with a backend
that supplies only captured output still needs explicit command text transport.
Carry stream origin to the delivery callback, including partial frames and EOF;
final-value metadata alone is insufficient. UTF-8 command text delivery uses the
binary stdout sink to avoid Windows text-mode newline conversion; ordinary value
rendering keeps its existing injected emitter. Do not add a StringIO-only fallback.

Comprehension checks: Why is removing `.strip()` insufficient? Ordinary JSON
interpretation and CLI newline insertion remain. Why not infer origin from exit
code zero at stream EOF? Physical EOF can arrive before the process exits and
cannot assert successful completion.

## Invariants and difference register

| Boundary | Required action |
| --- | --- |
| Successful command stdout | Preserve text instead of stripping or inferring JSON types |
| Failed command stdout | Preserve existing failure truth, size checks and spill ownership |
| Empty stdout | Emit no text bytes; structured command result must remain an empty string |
| Windows stdin text | Retire automatic LF-to-CRLF substitution in supervised and host interactive pipe writers; keep supplied text unchanged |
| Command stream EOF | Preserve text/origin; never invent outcome or completion evidence |
| Function/agent strings | Preserve current result decoding and presentation |
| Persistent command aggregates | Deliver ordered stdout elements without invented separators |
| Pipelines | Retire implicit command JSON parsing; preserve zero-copy move and deliver raw text. Callers parse JSON explicitly |
| Spill references | Keep caller ownership, size/hash verification and host/root recognition |
| STOP/KILL/timeout | Preserve priority and exactly-once reserved policy application |
| Liveness | Output origin never proves process exit or grants dispatch |

No queue, timer, dependency, replay path, MonitorStore dependency or new CLI flag.
Do not rename a durable transport merely for aesthetic uniformity: prefer retaining
`failed_output` and adding a success-only `command_output` variant over requiring
an old-outbox migration. The final design must justify any alternative explicitly.

## Proposed Spec Delta

Text-slice promotion strategy B, atomic spec/code. Insertions below supplement
current contracts; replacements explicitly identify the old span. Required code
mapping/backlinks accompany each promotion. Existing failure/control ownership,
size accounting, spill lifecycle and reserved policies remain in force.

Captured publication uses `outcome.stdout or ""`, never a potentially transformed
command `outcome.value`. Factor one encoded-envelope preparation/write helper for
success and failure; do not duplicate the wire-size fallback. Actual live callbacks
choose live frames. A backend with no callbacks uses the captured envelope even if
streaming was requested; no base64 fallback is needed to pretend live delivery.
Host interactive stdout calls the fresh `TextIOWrapper.reconfigure(newline="")`
before its reader thread starts; stdin also sets newline="" to avoid Windows retranslation; stderr keeps its settings.
Origin must survive `_result_stream_payload`, `_task_result_event`,
`_CollectedResult.record_output`, the separate realtime projection and CLI stream
rebuild. Partial-frame origin is preserved without recording a final result.

### [MF-2] — replace the entire bullet beginning "a command that exits non-zero"

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- Captured command stdout is text, preserved without stripping or JSON interpretation. A non-live-streaming successful command publishes exactly one `command_output` envelope, including for empty stdout: `{"type": "command_output", "result": <text or large_output reference>}`. Its exact keys are `type` and `result`; inline `result` is a string and spill references follow [MF-2.1]. The shared decoder marks this as command stdout without inventing a return code or changing ordinary completion eligibility. A command that exits nonzero publishes one existing `failed_output` envelope, including for empty stdout: `{"type": "failed_output", "return_code": N, "error": <string>, "result": <text or large_output reference>}`. Its existing strict shape and nonzero integer return-code validation remain. A failed row is task-owned outcome evidence: it settles failed with its error and return code even before the separate terminal broadcast, and never proves process death. Both variants use the same size/spill preparation and preserve whitespace and newline characters. Measure the actual UTF-8 encoded envelope against the resolved broker maximum before its single write attempt; oversized inline framing forces the existing spill path before writing. No guessed reserve, post-write retry or silent diagnostic truncation is introduced. Failure-path stdout publication errors are logged without replacing the original terminal command failure; success-path publication errors retain existing failure behavior. Malformed reserved transport fails readers and is unavailable evidence to background observation/retirement. Live-streaming commands do not publish a second copy of their stdout. Shared decoding carries `command_stdout` independently of failure metadata through collectors, events, CLI and client adapters. Neither origin nor EOF proves task completion or process death. Pipeline edges reject failed transport before handoff or override; successful transport is moved unchanged and unwrapped by the shared work-message decoder at the downstream inbox boundary. Any top-level object with type `command_output` or `failed_output` is reserved in an outbox. The inbox decoder unwraps only an exactly valid success wrapper through the shared structural validator; malformed or nonmatching inbox objects remain ordinary input data and cannot introduce a new pre-work exception. Existing `failed_output` records remain readable under their current contract. Edge refusal does not wait for a stage-terminal event.
```

Clause audit: retain control priority, reserved policy, failures never settle as
completed, bounded wire-size measurement, publication error handling, diagnostics,
live no-duplication, malformed background handling, shared decoding and prehandoff
failure rejection. Intentionally change only empty-failure omission (now an empty
string row), successful text preservation/framing and explicit origin. No lifecycle
state is inferred from the new success envelope.

### [MF-3] — replace "The existing stdout result transform and physical-EOF ordering remain unchanged."

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
Command stdout stream frames carry `command_stdout: true`. Their decoded text is not stripped and their final marker adds no output newline. Origin is independent of outcome: physical EOF carries no inferred successful return code. Logical-final and physical-EOF ordering remain unchanged.
```

### [RM-5.2] — replace "Ordinary codec error-handler behavior and newline translation are unchanged."

Replacement (`docs/specifications/06-Resource_Management.md`):

```text
Command stdout decoding preserves CRLF, bare CR and LF, including across split reads. This applies to supervised capture and host interactive sessions. Supervised and host interactive stdin writers encode supplied text without platform newline substitution, so preserved CRLF is not doubled in a command pipeline. The configured text codec, error handling, input budgets and backpressure remain unchanged; stderr diagnostic decoding retains its current policy. This is a text-preservation contract, not a promise to transport arbitrary undecodable bytes losslessly.
```

### [CLI-1.1.1] — replace the paragraph beginning "For a failed command, text mode"

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
For command stdout, text-mode waiting `weft run` preserves the captured text without stripping, newline conversion or an added newline, regardless of exit status. Inline text, retained spill content and live chunks follow the same rule. JSON preserves command stdout as a string, including empty strings and JSON-looking text; spilled values remain references. Failed commands retain the error on stderr and exit 1. Function and agent value presentation is unchanged.
```

### [CLI-1.2.2] — replace the bullet beginning "a `failed_output` row delivers"

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
- Single-task text results deliver command stdout exactly as captured text, with no added newline or separators between an ordered aggregate's command outputs. Failure metadata remains separate: nonzero command outcomes print their error on stderr and exit 1. JSON preserves nested command text or spill references without exposing transport envelopes. `--all` retains its existing inspection presentation (task prefix and row newline in text mode, so embedded command newlines may display blank lines): failed items carry failure metadata, their text diagnostics go to stderr, and any failed item makes the command exit 1. Function and agent value rendering is unchanged.
```

### [PY-2] — insert beside the existing public result fields

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```text
- `TaskResult` and `RunExecutionResult` carry `command_stdout: bool = False`. It identifies captured command text for presentation without asserting success, completion or process death. Shared result/event projections preserve this origin separately from `return_code`. Inline command values are strings, including empty, whitespace-only and JSON-looking text; spill references remain references. The existing `stdout` and `stderr` fields remain projections from dict-shaped result values; command content stays in `value` or `result_value`. The boolean is origin metadata, not another content field. Function and agent values retain their current decoding and presentation.
```

### [PL-4.1] — insert after the existing prehandoff failure rule

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
Successful command output keeps its transport envelope during zero-copy handoff. The downstream task's shared work-message decoder unwraps only exactly valid `command_output` transport without parsing its nested text as JSON; malformed or nonmatching inbox objects retain ordinary-input semantics. Thus `1\n` is a string downstream, not integer `1`; callers wanting structured input parse explicitly. Spill references remain references. Override input remains authoritative after the edge has rejected failed transport. Existing `failed_output` rejection and reserved policies are unchanged.
```

Additional exact RM-5.2 wording edit: replace "Universal-newline decoding may"
with "Newline recognition may" in the bounded-decoder paragraph; the fixed CR
carry accounting applies even with translation disabled. Keep the codec accounting,
decoder buffer limit and callback cleanup clauses unchanged.

## Tasks and stop gates

1. Review the text-slice exact delta; preserve the owner-selected text-only scope. Complete cross-family review. Save
   the author WT-7 account before an independent family sees theory and delta only.
   Stop if the implementation requires another result lane or guessed completion.
2. Extend real-process and broker-backed regression cases first. Prove RED for
   trailing/no newline, whitespace, empty text, CRLF/bare CR, JSON-looking text,
   both outcomes, streaming, spills and background reads. Keep function/agent
   preservation controls. Tests reuse existing harnesses; no broker/process mocks
   or test-only production seams. Stop on any falsified protocol premise.
3. Promote spec/code together. Reuse spill preparation and the shared decoder.
   Carry explicit command-origin metadata independently of terminal outcome.
   Check real command-to-command and command-to-function pipeline inputs, including
   `1\n`, without dequeue/re-enqueue handoff. Scope extras require a recorded
   delta/re-review, not a silent workaround.
4. Run completed-work review, fresh independent WT-7 account and theory audit;
   verify gates on an isolated frozen tree. Record causal repairs and any limits.
   Complete plan/index only when work and gates finish; leave unrelated edits out.

## Verification and gates

Existing test owners to extend: `tests/core/test_subprocess_runner.py`, `tests/tasks/test_runner.py`,
`tests/tasks/test_task_execution.py`, `tests/commands/test_result.py`,
`tests/commands/test_realtime_events.py`, `tests/tasks/test_pipeline_runtime.py`,
`tests/cli/test_output_fidelity.py`, and existing CLI run/result tests.
Concrete RED oracles: update the existing real-process split-read newline test
`test_stream_reader_normalizes_crlf_split_across_chunks` to expect raw stdout
characters (rename to match). Extend `test_interactive_session_collects_immediate_exit_stream_tail`
with CRLF/bare-CR stdout and retain stderr/EOF controls. Extend the existing
`test_failed_command_scalar_fidelity_and_wire_fit` matrix to both exit outcomes
and no-newline, repeated-newline, whitespace, empty, JSON-looking and CRLF text.
Capture CLI stdout as bytes in these tests: `subprocess.run(text=True)` would
normalize the very CRLF characters being verified. JSON assertions decode UTF-8
only after byte capture. Existing spill and wire-limit cases must still pass.
Exercise live/late result reads and actual pipeline downstream input at their
existing owner boundaries. Keep direct function-string framing controls so a
blanket `nl=False` cannot pass unnoticed. Prove stream EOF while the command is
still alive does not create successful completion evidence.

New tests require a distinct contract, credible failure and demonstrated RED/GREEN;
prefer parameterized existing cases over duplicate integration scaffolding.

Use `. ./.envrc` and repo `.venv` binaries. Run focused tests on SQLite and
`bin/pytest-pg --all`, full default/all SQLite tests and full PostgreSQL tests,
full mypy, Ruff/format, specs, `bin/check-doc-paths`, and `git diff --check`.
Check extension/integration roots separately because default testpaths excludes them.
Normal worker settings, no new skips, no timeout inflation. Do not edit acceptance
source while suites run. Failed gates require a causal diagnosis, not blind repeats.

## Rollout and rollback

Update producers/readers together. Python and JSON consumers now receive command
stdout as text, including its trailing newline; callers wanting parsed JSON must
parse explicitly. Pipeline downstream inputs follow the same contract. Retained
failure rows must not become successful ordinary data after an upgrade. Revert
spec/code together; caller-owned spills stay retained. Historical bare output rows remain generic values as written; missing whitespace
and origin cannot be reconstructed. No legacy detection, inferred command origin
or compatibility-only decoder is introduced. No data deletion or live outbox
migration is authorized by this plan. No push or release is requested.

## Deviation log

No undeclared deviations. The owner resolved the text-only scope. The exact delta
and the later input-boundary amendment received independent review before their
runtime changes. The Windows stdin amendment and test-oracle corrections are
recorded below; lifecycle, budgets and codec/error policies remain unchanged.

## Review log

Author research: confirmed capture stripping, universal-newline conversion, JSON
coercion, and scalar/list rendering are distinct owners. Native read-only research
agreed command origin cannot be inferred safely from an EOF return code. Author fresh-eyes pass: retain the existing failure variant to avoid a gratuitous
durable-format migration; represent command origin explicitly rather than guessing
a successful outcome at EOF. The owner selected this text-only scope. Independent review follows; no runtime implementation has begun.


## Author theory-fit account (text slice, before independent account)

Verdict: fits with strain. [WT-2a.10] requires Unix predictability: the producer
owns its newline, and a wrapper preserves it. [WT-2a.2] and [WT-4] put durable
origin in existing queue transport and shared interpretation in the core decoder.
Refuse another result queue, guessed completion at EOF, or a compatibility decoder
([ALT-WT-002], [ALT-WT-012], [ALT-WT-020]). Keeping the existing failure variant
is preserving the current contract, not supporting two versions of one contract.
Prediction: echo and printf differ only by their own emitted newline under both
outcomes, JSON-looking output remains text, and pipelines receive the same text.
Worst fit: another reserved transport shape and presentation metadata widen the
schema. They preserve a real distinction between command text and function values;
origin alone never grants lifecycle authority. Arbitrary binary fidelity remains
outside the owner-selected scope and unclaimed under the SDK text contract.


### Proposed-delta independent fit and comparison

Independent account: `/tmp/weft-stdout-prefit.out`, from theory and fixed proposed
spec delta alone after the author account was saved. Verdict: fits with strain.
Agreement: command-owned newlines, explicit origin separate from outcome, shared
decoding, no new queue/timer, measured wire size, retained failure semantics.
Divergences: pipeline implicit parsing is intentionally retired and is recorded in
the difference register (a register was outside the isolated delta-only input).
Historical bare rows cannot recover lost text or origin; they keep the generic
value contract without a legacy decoder. No automatic theory edit follows these
contested classifications. Tighten wording that task-owned failure rows supply
outcome evidence, not death proof. Public origin metadata is justified by the two
otherwise indistinguishable strings requiring different presentation.

### Independent proposed-delta account (verbatim)

# Theory-fit account: command-stdout text-preservation delta

**What this covers:** exact preservation of command stdout as text, judged from the spec-delta text only. Binary transport is not claimed, and nothing here depends on implementation, plans, logs or other accounts.

## 1. Verdict

**Fits with strain.**

## 2. Sections the change rests on

- **[WT-2a.10] A Unix citizen.** Piped data stays data, and the exit code carries the outcome. Unstripped stdout, no added newline and no JSON guessing make `weft run` behave like the command it wraps.
- **[WT-2a.12] / "It is just a message".** `command_output` is an ordinary envelope on ordinary queues. It needs no side channel.
- **[WT-2a.13] Independent lifetimes; death needs proof.** "Neither origin nor EOF proves task completion or process death" and "physical EOF carries no inferred successful return code" apply this rule directly.
- **[WT-4.4] Shared function, shared code; The client mirrors the CLI.** One shared decoder carries `command_stdout` through collectors, events, status, the CLI and `TaskResult`/`RunExecutionResult`.
- **[WT-4.4] Measure; do not bake in inefficiency.** The real UTF-8 envelope size is measured against the broker maximum, and there is no guessed reserve.
- **[WT-2a.8] SimpleBroker owns queue mechanics.** The size limit comes from the broker. Weft only chooses between inline and spill.
- **[WT-2a.7] / reserved policies.** The delta leaves these unchanged.

## 3. What it refuses, and the entry that refuses it

- **Guessed reserves.** It refuses a guessed framing reserve. [ALT-WT-001] states this rule for resources: measure, don't charge guesses.
- **Retry and truncation.** It refuses post-write retry and silent truncation, under "No capability diminution".
- **Inferring outcomes from EOF or origin.** It refuses to infer success or death from EOF or from where output came from, under [ALT-WT-015] ("A terminal event alone never proves process exit").
- **Dual decoders.** It keeps `failed_output` in its current strict shape and adds no second decoder for it, consistent with [ALT-WT-012].
- **Binary transport.** The delta disclaims it ("not a promise to transport arbitrary undecodable bytes"), so it adds no new codec concept. That fits "As few concepts as possible".

## 4. Falsifiable predictions

Any one of these failing proves the fit claim wrong:

- **Byte-identical stdout.** `weft run -- printf 'a\r\nb\r'` in text mode gives the same bytes as running the command directly, and exits 0. This holds when the CRLF is split across reads, and in host interactive sessions.
- **Empty failure.** A command with empty stdout and a nonzero exit publishes exactly one `failed_output` with `"result": ""`. Its error goes to stderr and the CLI exits 1.
- **Silent success.** A command that succeeds with no output publishes exactly one `command_output` with `"result": ""`, and no success return code is invented.
- **Pipeline handoff.** A pipeline stage running `echo 1` hands the next stage the string `"1\n"`, not the integer `1`.
- **Spill on first write.** An envelope that goes over the broker maximum only because of framing bytes spills on its first and only write.
- **No duplicate in live mode.** In live-streaming mode the outbox holds no second copy of stdout.
- **Monitor-independent.** All of this behaves the same whether `TaskMonitor` is running or not.

## 5. Worst fit and tensions

**Worst sentence:** "Thus `1\n` is a string downstream, not integer `1`; callers wanting structured input parse explicitly."

- **Capability loss without a difference register.** Pipelines that relied on implicit JSON parsing of command stdout lose that behaviour. "No capability diminution" requires an explicit difference register, and the delta names none. The change can be defended under [WT-2a.10], because data stays data. But it needs a register entry, not just the clause audit's "intentionally change".

**Second strain, [WT-2a.9] / [ALT-WT-012].** `command_output` becomes "reserved transport in both outboxes and inboxes". The delta does not say what happens to durable success rows written before it in the old bare stripped form. Either the shared decoder still reads them, which is a quiet dual reader, or old rows need a bounded forward migration, which the delta doesn't state. As written, the single-current-contract rule can't be checked.

**Third strain, [WT-2a.13].** "A failed row settles failed … even without terminal proof" can be read as an outcome claim made without proof. It holds only because the task owns the row, which makes it task-owned evidence of the outcome and says nothing about process death. The delta should say that outright.

**Mild concept growth.** `command_stdout: bool` adds an origin flag to the public result. It is orthogonal to `return_code`, but it is still a new concept on the use surface. That's acceptable only because one shared decoder owns it.


### Plan review round 1 and dispositions

Reviewed baseline: `5661fd18` with the fixed proposal embedded in
`/tmp/weft-stdout-plan-review-prompt.txt`. Independent Claude family verdict BLOCKED
(`/tmp/weft-stdout-plan-review.out`). No runtime implementation began before review.

| ID | Suggested issue | Disposition |
| --- | --- | --- |
| 1 | Windows newline retranslation | Accept: binary UTF-8 stdout sink for command text, including live chunks. Preserve function emitters. |
| 2 | Test helpers normalize/strip output | Accept: exact byte capture via subprocess, portable child buffer writes, not run_cli/text=True. |
| 3 | Malformed inbox wrapper escapes lifecycle | Accept narrow recognition option: unwrap only exactly valid shapes; otherwise ordinary data. Outbox validation stays strict. Test both boundaries. |
| 4 | Requested streaming without callbacks | Clarify captured envelope uses actual live flag; decline base64 fallback, which adds no live capability and retains split-codepoint corruption risk. Test backend without callbacks. |
| 5 | Choose outcome.stdout | Accept, including None-to-empty SDK normalization. |
| 6 | Interactive newline method | Accept explicit method, prefer fresh TextIOWrapper.reconfigure over another wrapper/ownership layer. |
| 7 | Flag lost in projections/list rendering | Accept named owners and command aggregate concatenation; bulk inspection keeps its existing row presentation, including embedded newlines. |
| 8 | Boolean vs stdout field naming | Retain explicit command_stdout origin; document existing stdout/stderr projections and separate content field. |
| 9 | Duplicate write helper | Accept one encoded-envelope sizing/spill/write helper. |
| 10 | Decoder wording / translate stderr too | Update recognition/carry wording; retain stderr policy to avoid broadening the owner-selected stdout change. |
| 11 | Clause audit omitted refusal wait / reservation | Restore exact stage-event independence and unconditional outbox namespace reservation; inbox recognition intentionally narrower. |
| 12 | Empty-failure null -> empty string | Intentional, in register and changed assertions. |

Simpler JSON-string-only success was considered and refused: it preserves text type
but loses durable origin needed to distinguish command rendering from a Python
function's string value, and cannot identify a wrapped spill reference without
another metadata mechanism. No inferred origin from historical rows is added.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
| --- | --- | --- | --- | --- |
| 2026-11-05 | 5661fd18 proposal round 1 | Scope, delta, boundaries | Owner selects text only; byte-write/test capture, exact-shape inbox recognition, actual live flag, projection owners and preserved clauses clarified | Yes, scoped IDs 1–12 |


### Plan scoped follow-up

`/tmp/weft-stdout-plan-scoped.out`: no blocker on all 12 dispositions, reviewed
against baseline 5661fd18. Accepted nonblocking precision: interactive partial/final
frame producers must both mark origin; flush text stdout before binary writes and
use the same UTF-8 replacement policy as spills. Independent byte-identity claims
are limited to text encoded as UTF-8 (or ASCII); configured non-UTF-8 child codecs
preserve text, not their original encoded bytes. The owner explicitly selected
text fidelity and excluded arbitrary binary transport. Runtime implementation starts
only after this review closed, in `/tmp/weft-stdout-work`, excluding unrelated edits.


## Implementation evidence

Capture owner RED: `/tmp/weft-stdout-capture-red.log` had three exact-newline
failures and one unchanged invalid-codec control pass. Supervised split-read CRLF
and bare CR, and host interactive stdout, normalized before the repair.
GREEN: `/tmp/weft-stdout-capture-green.log` passed four cases. Both POSIX and
Windows stdout adapters now disable translation; stderr keeps the original policy.
Successful `RunnerOutcome.value` is raw stdout. Host interactive readers reconfigure
the fresh stdout TextIOWrapper before starting. Scoped mypy passed four files.
Full capture-owner modules: initial five failures were old newline/strip expectations,
not timing failures. Updated only those contract oracles (including the reference
reader newline policy); `/tmp/weft-stdout-capture-all2.log` passed 292, skipped 10
platform-specific cases. No timeout/worker settings changed.

Pipeline/input RED: six exact wrapper assertions failed before input unwrapping;
six real command-to-command/function cases failed before publication preserved text
(`/tmp/weft-stdout-pipeline-red.log`). GREEN: 170 owner tests pass
(`/tmp/weft-stdout-input-pipeline-green.log`), including empty, `1\n`, JSON-looking
CRLF input, unchanged queue body/message ID, and prior failure/override/reserved
policy tests. No `pipeline.py` edit was necessary. The initial function oracle
used a string that generic function decoding intentionally parses as JSON; a
structured payload echo observes the input without changing function behavior.

Transport owner: decoder eight RED cases established missing success recognition
and command origin. Twelve byte-captured CLI cases cover captured/live delivery,
JSON/text projection, and empty/JSON-scalar/CRLF data. The owner updates the existing
empty-failure JSON oracle from None to empty string. A spill closed-pipe check's
expected bytes also gain the producer's formerly stripped newline; its exit 1,
no traceback/ignored flush, retained spill and incomplete-output oracles stay intact.


### Stable implementation evidence and completed author fit

Owned combined reader/render/Consumer modules: 487 passed
(`/tmp/weft-stdout-owned-final.log`). After removing an unused import, final
adapter cases passed 17 (`/tmp/weft-stdout-adapter-stable.log`). Pure capture-owner
modules passed 292 with 10 platform skips; pipeline/input owner files passed 170.
Ruff, format (642 files), full mypy (496 sources) and whitespace checks passed
on the resulting source. The no-callback test parameter uses the existing controlled
runner-API dependency and a real broker: it proves Consumer publication chooses the
actual non-live path despite requested streaming, not remote SDK negotiation.
There is no new process/broker mock or production-only testing seam.

Author completed fit (saved before independent implementation account):
1. Verdict: fits with strain.
2. Grounds: [WT-2a.2], [WT-2a.7], [WT-2a.9], [WT-2a.10], [WT-2a.13] and
   [WT-4.4]. Existing queues carry durable command origin. A shared core predicate
   validates success transport in both roles, while inbox recognition remains
   tolerant of ordinary user data. Rendering uses a complete binary UTF-8 write
   so newline characters survive the platform text layer. Capture preserves stdout
   characters; codec and stderr policies stay unchanged.
3. Refusal: no alternate result queue, timer, MonitorStore truth, guessed EOF outcome,
   old-format detector or implicit replay ([ALT-WT-002], [ALT-WT-012],
   [ALT-WT-020]). Current failure transport remains current, not a compatibility
   version. Generic function values retain their own contract.
4. Prediction: echo and printf differ only by the newline they produce; CRLF/bare CR,
   whitespace, empty and JSON-looking command text survive capture, storage,
   pipeline handoff and text rendering. Early stream EOF does not create a return
   code or completion proof. These are text claims: UTF-8 delivery does not promise
   a child codec's original byte encoding or arbitrary undecodable binary output.
5. Worst fit: another reserved shape and public origin bit add schema surface, and
   implicit pipeline JSON conversion is intentionally removed. The former preserves
   a real distinction between command stdout and function values; the latter
   replaces a surprising lossy coercion with explicit user parsing. Neither origin
   nor successful stdout content changes lifecycle authority. Strain remains recorded
   rather than changing the theory to bless the implementation.

### Theory-versus-code audit (touched claims)

Unchanged claims carry forward from the completed result-output-fidelity plan at
5661fd18. This slice re-audits only touched boundaries; native Windows execution is
not claimed by local macOS results.

| Claim | Owning contract and code | Evidence/status |
| --- | --- | --- |
| WT-2a.10 Unix text behavior | MF-2, MF-3, RM-5.2, CLI-1.1.1/1.2.2; runner capture, command transport and binary renderer | Exact byte-captured UTF-8 CLI cases and raw split-read/interactive tests pass; other child encodings preserve text only |
| WT-2a.2 queue facts / WT-2a.9 one current contract | MF-2, PY-2; shared predicates and origin metadata | Decoder/inbox RED/GREEN, reader projection cases; existing failure records supported, no historical-origin inference |
| WT-2a.7 custody | PL-4.1, MF-2; unchanged edge move and reserved disposition | Real handoff keeps body/message ID; existing failure/policy controls pass |
| WT-2a.13 independent lifetime | MF-3; EOF/origin carry no outcome | Protocol cases assert no return code on partial/final command stream markers; existing lifecycle gates carried forward |
| WT-4.4 shared code and measured limits | MF-2, MF-2.1; shared shape recognizer, write preparation and write-all sink | Both command variants exercise actual 4 KiB broker wire limit; partial FileIO writes preserve full payload |


### Completed-work review and input-boundary amendment

Frozen implementation review: PASS (`/tmp/weft-stdout-final-review.out`), with a
Windows pipeline test finding and bounded P2 recommendations. The first complete
SQLite run returned five old-contract oracle failures, 6755 passes and 60 skips
(`/tmp/weft-stdout-frozen-all.log`). Those failures are separate from the Windows
finding: command response newline, three runner-parity raw-JSON expectations and
one long-session command transcript. Three test files were corrected in a separate
final tree, preserving the review snapshot. The focused five cases pass and all
three owner modules (44 unique cases) pass with lifecycle/iterator/parity assertions
retained. No runtime defect was inferred from those stale assertions.

Scope amendment before input implementation: the Windows `_StdinStreamState`
currently replaces every LF with `os.linesep`, including an LF already preceded by
CR. Host interactive stdin's TextIOWrapper has the same default translation.
Preserving command stdout therefore exposes CRLF doubling at the next command's
stdin. A platform-specific expected-value relaxation would preserve that defect;
this plan instead removes rewriting at the two Weft-owned input writers. This is
necessary for the accepted command-to-command text contract, remains text-only,
and adds no option or transport shape. `read_limited_stdin` already reads the
binary buffer and decodes UTF-8 without newline normalization; the CLI needs no
input change. Stderr and codec/error policies are unchanged.

Input amendment implementation owners:
- `subprocess_runner.py::_StdinStreamState._encode_next`: encode the supplied
  slice directly instead of replacing LF with platform line separators.
- `host.py::HostTaskRunner.start_session`: set fresh stdin TextIOWrapper
  `newline=""` before use, beside the stdout preservation setup.
- `tests/core/test_subprocess_runner.py::test_large_input_preserves_text_encoding_and_stream_separation`:
  the reference writer uses newline="". Parameterize the existing real-process
  roundtrip with a controlled `os.linesep` LF/CRLF value, proving the old substitution
  fails even on macOS. Keep codec replacement, stderr hash and cleanup assertions.
- `tests/tasks/test_runner.py`: a real interactive-session echo verifies exact
  text received on stdin, with event/pipe synchronization and existing cleanup.
- Real pipeline command/function tests retain the same exact text on every platform.

This revises the earlier author-selected "stdin unchanged" implementation constraint;
it does not change a theory invariant, reserved policy, codec, input limit, control
state or process ownership. Review this delta before changing the input writers.

| Date | Baseline | Section | Change and reason | Re-review required? |
| --- | --- | --- | --- | --- |
| 2026-11-05 | frozen stdout implementation at 5661fd18 | RM-5.2 and Windows stdin | Preserve supplied pipeline text at both input writers instead of allowing CRLF doubling; only text conversion changes | Yes, input amendment before code |

Completed-work dispositions: terminal status/code remain the only outcome source.
A causal mutation proves known command origin is needed to expose an existing
terminal code without losing streamed stdout. Unused evidence metadata and partial
decoded objects were removed; callbacks/events carry partial origin and final
values retain their origin. Failed captured byte-level CRLF/bare-CR coverage and
real early-EOF process synchronization were added. Duplicated test scaffolding was
consolidated. Implementation mappings sit beside their contracts. Optional
cosmetic dictionary/local-variable rewrites were declined.


### Final review dispositions and input proof

Input amendment review `/tmp/weft-stdout-input-plan-review.out`: no blocker before
input code changes. It confirmed the two writers and the existing raw CLI reader.
Input RED `/tmp/weft-stdout-input-red.log`: two CRLF-doubling failures and two
controls passed. After removing substitution and configuring fresh host stdin,
`/tmp/weft-stdout-input-green.log` passes four. The host test configures an actual
Popen text pipe's public newline policy to CRLF before session setup, rather than
using a fake process or a new production seam. Thus both real stdin writers have
portable failing-first evidence, even without native Windows execution. Capture/input
owner modules pass 295 with 10 platform skips; full type/lint checks pass.

Completed-work P1 Windows pipeline finding: fixed at the input writers under the
reviewed amendment; exact pipeline expectations are retained, not made lossy.
P2-1 suggested removing known command origin from terminal code projection:
rejected with causal proof. Ignoring it makes actual background failed-stream
single-result delivery lose stdout (b'' versus b' \r\nCR\r'),
`/tmp/weft-stdout-origin-argument-disabled.log`; restored control passes. Terminal
status must still be failed and the code is taken only from that terminal fact.
Origin identifies the returned text's kind; it never creates an outcome or code.
P2-2 unused snapshot metadata removed, along with the unsupported status-origin
claim. P2-3 partial decoded objects removed: callbacks and TaskEvents already carry
partial origin. Final decoded values retain origin; duplicate folding is gone.
P2-4 captured CLI matrix is consolidated and captures bytes for both outcomes,
including CRLF/bare CR. A real socket-barrier process closes OS stdout while staying
alive; the observer consumes EOF and waits until the actual exit-7 terminal fact.
Premature-completion mutation fails with a completed TaskResult before release
(`/tmp/weft-stdout-early-eof-completion-red.log`); restored controls pass. Initial
prototypes closed only Python's standard wrapper, whose closefd=False left OS fd1
open; those timeouts were test-authoring errors, not production defects. No timing
budget was inflated. P2-5 mappings moved beside RM-5.2 and the new PY-2 field.
P2-6 optional dictionary/local-variable/append style rewrites declined as unnecessary
scope. Real print producers' expectations use os.linesep; explicit-buffer data and
fake-runner strings remain exact, and stderr expectations retain their unchanged LF
policy. The small wire-limit fixture uses explicit LF bytes so its 3000<4096 premise
holds on Windows too. All portability-focused cases pass.

Mutation evidence: disabling complete writes truncates the real FileIO sink to seven
bytes (`/tmp/weft-stdout-write-mutation.log`); using requested instead of actual
streaming capability emits base64 frames and fails the exact wrapper oracle
(`/tmp/weft-stdout-captured-mutation.log`). Restoring both passes three controls.
Actual Unix CLI probes: echo emitted b'hello\n'; printf emitted b'hello', both exit0
(`/tmp/weft-stdout-unix-probes.log`).

Final author fit reaffirmed before the next independent account: fits with strain;
all five parts above remain. Windows stdin preservation strengthens WT-2a.10's
piped-data rule by removing a platform rewrite, without changing codecs, limits or
lifecycle ownership. Removing unused metadata/partial objects reduces concept
surface. The marker remains necessary for distinguishing stdout from function
values. UTF-8 delivery, including the existing replacement policy for unencodable
surrogates, is not arbitrary byte-encoding fidelity. No automatic theory revision.


### Final frozen revision fit reaffirmation

The final revision retains the completed author account's five parts and verdict
(fits with strain). Full input/capture modules pass 295 with 10 platform skips.
Known-origin terminal-code projection has actual failure/restore evidence; unused
metadata and partial decoded objects are absent. Callback bytes and final value
metadata serve distinct phases. Command-text delivery and ordinary value formatting
serve different semantic kinds and share their rendering owner; their two sink
policies are not duplicate implementations of one responsibility. Inbox recognition
is a declared current transport protocol, like the existing work-start sentinel,
not a hidden authority or new side channel. Historical bare values remain generic
values without inferred origin. UTF-8 scalar text and newline fidelity are claimed;
unencodable surrogate replacement and non-UTF-8 source byte encoding remain outside
any arbitrary-binary guarantee. The independent account may still classify these
as strain; no theory amendment is inferred from a disagreement.


### Final independent fit and comparison

Independent final-revision account (`/tmp/weft-stdout-final2-fit.out`) follows the
saved author reaffirmation and used only theory plus the exact final diff. Verdict:
fits with strain, matching the author. Both accounts endorse raw text, preserved
pipe input, zero-copy handoff, no guessed EOF outcome, shared validation/writing,
and a coordinated contract change. No theory edit follows automatically.

Contested classifications:
- The implicit-JSON difference register exists in this plan and was reviewed before
  implementation. It is outside the isolated theory+diff input by design. The
  caller can still parse JSON explicitly; retiring the implicit conversion is an
  owner-selected default/API change with migration notes, not an unrecorded loss.
- Valid-only inbox artifact recognition is declared in the current protocol and
  shares the existing work-sentinel precedent. It grants no authority or side
  channel. The namespace cost remains a strain, not a silent sanitizer boundary.
- Historical bare rows retain their generic value contract. No legacy detector,
  dual version decoder or inferred origin was added. A new producer has one success
  shape; the unchanged failure shape is a different current semantic variant.
- Prediction P4's claim that success/failure envelopes have identical size/spill
  thresholds is agent reasoning, not an implementation requirement. Failure carries
  extra code/error fields. Both measure their own actual wire size through one
  algorithm; the 3000-newline/4096-byte sample spills in both cases, but their exact
  thresholds need not match. Padding a guessed reserve would contradict the plan.
- P5 cannot see manual probe evidence under its isolated inputs. The actual CLI
  black-box capture and stdin roundtrip both returned hex 200d0a43520d4c460a exactly
  and exit0 (`/tmp/weft-stdout-final-blackbox.log`), on macOS. Native Windows is
  unmeasured locally; real portable newline-policy profiles and CI-capable tests
  are not reported as a native Windows run.
- Stdout text and stderr diagnostics intentionally retain distinct policies;
  preserving stderr behavior is a scope constraint. UTF-8 replacement of invalid
  scalar data and other original byte encodings are not binary fidelity claims.

Final-revision independent account, verbatim:

**Verdict: fits with strain.** The change makes Weft behave more like a Unix tool. The strain comes from three places: it removes a capability without saying so as a loss, it reserves a shape on every inbox, and it leaves two record shapes in place.

**1. Sections it rests on**
- **[WT-2a.10] A Unix citizen.** Piped stdin now reaches the task as data. The Windows LF→CRLF rewrite is gone from `_StdinStreamState`, and host session pipes use `newline=""`. Stdout returns without stripping, newline conversion or an added newline. The exit code still carries the outcome.
- **[WT-2a.13] Death needs proof.** "Neither origin nor EOF proves task completion or process death." Physical EOF no longer implies a successful return code. The socket-barrier test checks that a waiter does not settle after stdout EOF while the child is still alive.
- **[WT-4.1] "It is just a message."** `command_output` is an ordinary outbox row. The pipeline edge moves it unchanged, with the same message ID. Only the downstream inbox decoder unwraps it.
- **[WT-4.4] "Shared function, shared code."** `_publish_command_output` now writes both envelopes. `is_large_output_reference` moves to `weft.core.outbox` so CLI delivery and the inbox share one spill check.
- **[WT-4.4] "One path on all platforms."** The Windows-only stdin newline branch is removed rather than given a sibling branch.
- **[WT-2a.9] One current contract.** It is a single coordinated break with a CHANGELOG entry.

**2. What it refuses, and the entry that refuses it**
- **[ALT-WT-014] and [WT-2a.11].** The inbox decoder checks shape only. Malformed or non-matching `command_output` data stays ordinary input; there is no new rejection or sanitizer. The CLI's exact-text writer is in the same spirit: no escaping, only UTF-8 with `errors="replace"`.
- **[ALT-WT-012].** No dual decoder is added. Old `failed_output` rows stay readable because that is still the current shape. Old stripped success rows are not migrated, and the CHANGELOG says so.
- **[ALT-WT-002] and [ALT-WT-021].** Newline preservation is done by turning off decoder translation, not with a drain or poll timer.
- **[WT-5] boundary.** It does not promise arbitrary-binary transport or lossless delivery of the original non-UTF-8 bytes. Within the stated scope (UTF-8 text and newline preservation, including Weft-owned pipe inputs), it makes no claim beyond the configured codec.

**3. Falsifiable predictions**
- **P1.** On Windows, take a two-stage pipeline: a command writes `a\r\n`, and the next command echoes stdin to stdout. The final `weft result` stdout bytes are exactly `61 0d 0a`. A `0d 0d 0a` disproves the stdin claim.
- **P2.** A successful command that prints `1\n` gives a downstream function the string `"1\n"`, never the integer `1`. `weft run --json` shows `"result": "1\n"`.
- **P3.** A command closes stdout, waits, then exits 7. No waiter, whether CLI, client or event stream, reports `completed` or `return_code` 0 in between.
- **P4.** For the same input, the success and failure envelopes are the same size and spill at the same point. Writing `\n`×3000 with a 4096-byte broker limit spills for exit 0 and exit 11 alike.
- **P5.** Probe gap: [WT-2a.10] is behavioural, so the audit row needs a release-time black-box probe, such as a byte-exact CRLF round-trip through `weft run`. The diff adds tests but no probe, so the row stays "unmeasured" until one exists.

**4. Tensions**
- **The inbox reservation crosses an owner boundary.** `decode_work_message` unwraps an exactly valid `{"type":"command_output","result":…}` in *any* inbox, including one an operator wrote by hand. The spec calls the shape "reserved in an outbox", yet the decoder acts on inbox data. This is consistent with [WT-2a.11] (trusted by placement), but a user's literal payload is silently transformed.
- **Mixed shapes during migration.** Old success rows (stripped plain strings) decode with `command_stdout=False`, so the same command type renders under two text policies until those rows are consumed. That is bounded, but it sits awkwardly with "exactly one current shape".
- **Stdout and stderr follow different policies.** Stdout is untranslated; stderr keeps universal-newline decoding. That is defensible (stderr is diagnostic), but it is a second decoding policy inside one runner.

**5. Worst-fitting sentence**
[WT-4.4] "No capability diminution: Simplifications must preserve observable capabilities and latency bounds; each hardening plan maintains an explicit difference register."

The change removes implicit JSON interpretation of command stdout. Downstream function stages that used to receive structured values (`1`, `{"key":1}`) now get strings and must parse them explicitly. The CHANGELOG states this as a behaviour change, but nothing in the diff records it as a lost capability in a difference register. Whether a fidelity correction counts as a "simplification" under this rule is unclear in the theory text. That makes this my best candidate for a **document deficiency**: [WT-4.4] does not say how a correctness fix that removes an implicit convenience should be weighed.


### Final completed-work scoped review and gates

`/tmp/weft-stdout-final-scoped-review.out`: no blocker. The reviewer confirmed the
input amendment and native-text test oracles, metadata/partial-object removals,
byte-capture matrix, real early EOF proof and implementation mappings. They withdrew
the proposed known-origin argument removal after verifying the actual background
path lacks a TaskSpec and would otherwise drop stdout. Actual terminal status/code
remain mandatory. No reconsideration of a theory invariant was requested.

Final frozen source manifest: 650 files, unchanged throughout acceptance. Full
SQLite all-tests run: 6778 passed, 60 skipped in 254.28 s
(`/tmp/weft-stdout-final2-all.log`). Default SQLite run: 6774 passed, 49 skipped in
238.41 s (`/tmp/weft-stdout-final2-fast.log`). Spec suite: 165 passed in 3.22 s;
full mypy 496 sources, Ruff, format 642 files, doc-path and whitespace checks pass.
Platform-specific/physical-PostgreSQL skips remain their existing scope. No new
skip, worker reduction or timeout inflation was used. PostgreSQL and separate
integration/extension-root results are recorded at closeout.


## Completion

Final PostgreSQL main suite: 6726 passed, 40 expected skips in 441.45 s
(`/tmp/weft-stdout-final2-pg.log`). Separate integration/extension roots:
SQLite 385 passed, 5 skipped in 9.33 s; PostgreSQL 386 passed, 4 skipped in
14.59 s (`/tmp/weft-stdout-final2-extensions-{sqlite,pg}.log`). The four real
Microsandbox cases remain opt-in, and SQLite's extra skip is PostgreSQL-only.
All main and supplemental results use normal repository workers and existing
failure bounds. The 650-file final manifest stayed unchanged across acceptance.
Native Windows execution is not claimed; exact portable TextIO translation profiles
and the shared Win32 adapter code are covered as described above.

The plan's spec, code, tests, user documentation and status-index closure are
complete. Final review: no blocker. Independent fit: fits with strain, with
contested classifications and their evidence retained. No program theory was
silently amended. No unresolved implementation item or new deferred binary work
is created. Codec/error policy and stderr diagnostics remain their existing contract.
Rollback is the coordinated code/spec revert documented above; source text already
lost by earlier versions cannot be reconstructed. Land by explicit file-list staging
from the isolated final tree and only this plan's completed index row. Exclude the
unrelated Manager, custody, governance and plan edits. No push or deployment.
