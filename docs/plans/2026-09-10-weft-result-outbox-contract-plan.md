# Weft Result Outbox Contract Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-1.2.2]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/07-System_Invariants.md [OBS.14]
Superseded by: none

Class: 5 — changes normative text in [MF-5] (the shared-wait-helper rule),
[CLI-1.2.2] (`weft result` semantics, the new `--nowait` flag, the exit-2
rule), and [PY-2] (`TaskResult` ownership and the nothing-available outcome
for `Task.result()`). Risky triggers also fire —
"a public contract, CLI shape … or result payload is changing" and the reader
sits on the durable spine (`Consumer → T{tid}.outbox → weft.log.tasks →
result waiter`) — so the `hardening-plans.md` checklist applies and review
comes before implementation. Plan type: implementation with spec revision.
Promotion strategy: **B (atomic)** for all three spec files — see
§"Proposed Spec Delta" for why. Supersedes
[2026-09-08-persistent-result-output-ids-plan.md](./2026-09-08-persistent-result-output-ids-plan.md)
(its design — membership IDs on a sixth task-local queue — was rejected by the
owner; its verified code facts are reused here and re-verified at this
baseline). Inherited from the superseded plan: nothing decided-but-unbuilt;
its deviation log is empty and its `T{tid}.results` queue was never created.

## 1. Goal

`weft result TID` today is a *status* wait that tries to guess which outbox
rows belong to which completed work item. It compares outbox row timestamps
against `work_item_completed` / `work_completed` event timestamps
(`_resolve_persistent_result_boundary`), falls back to "the latest visible
row", and then to a 0.5 s quiet period
(`WEFT_COMPLETED_RESULT_GRACE_SECONDS`). The inference is wrong under load:
reproduced through the public `await_task_result` path at `178e3a34` with a
persistent task and three inputs `b1`, `b2`, `b3` (three
`work_item_completed` events), three successive calls returned
`('completed', 'b1')`, then `('completed', ['b2', 'b3'])`, then
`CommandTimeoutError` — the second call merged two items. Re-reproduced by
the round-1 Claude reviewer against current main (`7792178a`) on a real
temporary broker with the same shape: the merge is deterministic whenever two
or more items are queued before a read, and the cause is the latest-visible
fallback in `_resolve_persistent_result_boundary`
(`weft/commands/result.py:418-421`).

Owner decision (Van, 2026-09-10): stop inferring. Queues are truth. `weft
result` becomes an **outbox read**: it blocks until one result is available or
the task is terminal, returns exactly one `T{tid}.outbox` row (consumed), and
reports absence rather than reconstructing failure detail. Task failure is
status, not output — `weft task status TID` owns it. `weft run` keeps its
status-driven exit codes and is untouched.

## 2. Source Documents

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] `:842-845` (the
  shared wait-helper / grace bullet), `:846-847` (the persistent-boundary
  bullet), `:850-852` (the emitted-result bullet), `:853-855` (the
  materialization bullet). [MF-2] `:134-135` (persistent tasks emit
  `work_item_completed` per message, `work_completed` only at terminal
  finish) — **no delta**; producer behavior is unchanged, only the reader
  stops treating those events as boundaries. The `### Current Consumer and
  CLI Behavior` subsection at `:1172-1178` — which sits **above** the
  `### Large-Output Reference Format [MF-2.1]` heading at `:1180` — carries
  the explicit sentence "`weft result` does not currently auto-dereference
  large-output references" (`:1178`); the reference format itself is
  [MF-2.1] `:1180-1234`. **No delta to either.** See Decided Owner
  Question 1.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.2] `:475-498`
  (implementation mapping `:477-479`, "Current behavior" bullets
  `:483-498`).
- `docs/specifications/14-Python_API_Surfaces.md` [PY-2] `:90-95`
  (`TaskResult` ownership), `:83-86` (`RunSession.wait`), `:165` (return
  matrix row for `root result`).
- `docs/specifications/07-System_Invariants.md` [OBS.14] `:544-547` (claimed
  outbox residue is recovery evidence, not decoded result evidence) —
  **no delta**; the `_claimed_result_blockage` path is preserved verbatim.
  A grep of 07 for `result`, `outbox`, and `grace` found no other section
  that constrains the *CLI result reader*. [EXEC.6] `:246-247` and [EXEC.7]
  `:248-249` were raised in round-1 review (Codex 4) as additional
  constraints; the owner declined that reading — see §4 "Why no terminal
  drain window" — because both govern the private runner → Consumer terminal
  handoff, not the CLI reading `T{tid}.outbox`. **No delta to either.**
- Superseded plan
  [2026-09-08-persistent-result-output-ids-plan.md](./2026-09-08-persistent-result-output-ids-plan.md)
  §1-§3: reuse its verified inventory of `weft/commands/result.py`,
  `weft/commands/_result_wait.py`, and the `consumer.py` write order.
  **Do not implement its design.**
- Format exemplar:
  [2026-08-31-collation-store-toggle-removal-plan.md](./2026-08-31-collation-store-toggle-removal-plan.md).
- Guidance: `CLAUDE.md` §1.1 and §4;
  `docs/agent-context/runbooks/writing-plans.md`;
  `docs/agent-context/runbooks/hardening-plans.md`;
  `docs/agent-context/decision-hierarchy.md` [DOM-15];
  `docs/agent-context/runbooks/adversarial-acceptance-probes.md`.

## 3. Context and Key Files

### Current structure — the reader

Three functions own the wait today, all in `weft/commands/` (anchors re-read
at `7792178a`):

- `result.py::await_task_result` `:730-806` — the public entry. Calls
  `_await_result_materialization` `:177-338` (resolve the outbox/ctrl_out
  names from the taskspec payload, an event's embedded `taskspec`, live queue
  activity, or the pipeline naming; it *also* returns any terminal state it
  already observed — `terminal_status`, `terminal_error_message`,
  `terminal_event_payload`, `terminal_event_timestamp` — and the advanced log
  cursor `log_last_timestamp`, built at `:260-322`), then
  `_claimed_result_blockage` `:80-101` ([OBS.14]), then subtracts elapsed
  materialization time from the caller's timeout at `:778-782`, then
  `_await_single_result` `:784`.
- `result.py::_await_single_result` `:423-727` — branches on
  `_is_persistent_task` `:350-357`. Non-persistent delegates to
  `await_one_shot_result` `:452-465`. The persistent branch `:468-727` is the
  inference machinery: `first_pending_timestamp`, `latest_pending_timestamp`,
  `pending_quiet_since`, `boundary_timestamp`, `boundary_seen_at`,
  `pending_completion_timestamps`, `_resolve_persistent_result_boundary`
  `:407-420`, `_latest_visible_outbox_timestamp` `:391-404`,
  `_drain_outbox_until_timestamp` `:360-388`,
  `drain_available_outbox_values` at `:637`, and six
  `WEFT_COMPLETED_RESULT_GRACE_SECONDS` clamps at `:555`, `:627`, `:635`,
  `:684`, `:699`, `:710`.

  **Both branches stay.** This is the round-1 structural correction (Codex 1
  / Claude 1, accepted by the owner 2026-09-10): three surfaces still reach
  `_await_single_result` after `weft result` and `Task.result()` move off it,
  and the persistent branch is the only path that does not drain-and-merge
  the whole outbox. Deleting it would silently change those surfaces —
  a diminution with no register row.
- `_result_wait.py::await_one_shot_result` `:116-352` — status-driven:
  drains the whole outbox each turn (`drain_available_outbox_values`
  `:205-209`), polls the log and ctrl_out for terminal proof, and applies its
  own grace clamps (`:266`, `:295`, `:320`, `:331`). It can also complete
  from one visible result after the 0.5 s grace **without** terminal proof
  (`:290-305`). **This function stays exactly as it is.**

Callers of `await_task_result` (verified by grep at `7792178a`):

| Caller | Surface | After this change |
|---|---|---|
| `result.py::cmd_result` `:1032` | `weft result TID` | **switches** to the new reader |
| `client/_task.py::Task.result` `:51` | `weft.client` public API | **switches** to the new reader |
| `run.py::RunSession.wait` `:426-442` (call at `:430`) | `RunSession.wait()` [PY-2] | **unchanged** (status-driven, via `_await_single_result`) |
| `events.py:323` (reached from `tasks.py:1941` and `client/_task.py:90`) | `follow_task_events` synthetic result event | **unchanged** (via `_await_single_result`) |
| `result.py::_result_task_event_stream` `:917` | `weft result --stream` tail | **unchanged** (via `_await_single_result`) |

`RunSession.wait()` never reaches the persistent branch in practice, because
`weft run` refuses `--wait` for persistent TaskSpecs
(`run.py:1292-1295`, `RunUsageError("--wait is not supported for persistent
TaskSpecs; use --no-wait.")`). `follow_task_events` and `--stream` **do**
reach it for persistent tasks, and that is the reason the branch is retained.

`weft run`'s inline wait does not go through `await_task_result` at all:
`run.py::_wait_for_task_completion` `:585-610` calls `await_one_shot_result`
directly. `weft run`'s exit codes are rendered in `weft/cli/run.py:78-122`
(0 completed, 124 timeout, 1 otherwise).

### Current structure — decoding

`weft/core/outbox.py::process_outbox_message` `:37-91` is already the
producer-declared reassembler; `weft/commands/_streaming.py:82-95` re-exports
it as `process_outbox_message`. A `{"type":"stream", …}` row returns
`(False, None)` until the frame carrying `"final": true`, at which point it
returns `(True, DecodedOutboxValue("".join(stream_buffer)))`. Any other row
returns `(True, DecodedOutboxValue(<decoded>))` — one row, one value. A spill
reference row (`{"type":"large_output", …}`, built by
`base.py::_spill_large_output` `:626-653` at `consumer.py:904` and written at
`consumer.py:905`) is therefore already exactly one complete result: **there
is no dereferencing reader anywhere in `weft/`** (verified by grep for
`large_output`: `consumer.py:904` and `base.py:626,636` are writer sites
only), matching the `### Current Consumer and CLI Behavior` sentence at
`05-Message_Flow_and_State.md:1178`. See Decided Owner Question 1.

`result.py::_read_outbox_task_result` `:810-861` (the `--all` reader) already
implements the peek-then-consume pattern this plan needs: `peek_generator`
with timestamps, decode into a local buffer, and `read_one(exact_timestamp=…)`
only over the **complete prefix** (`:840-844`). Reuse that shape; do not
invent another. Two differences the new reader must have: it stops at the
**first** complete value instead of aggregating every complete value in the
prefix (`_read_outbox_task_result` calls `aggregate_public_outputs` over all
of them at `:842`), and it treats a `read_one(exact_timestamp=…)` returning
`None` as "another reader claimed that row" rather than ignoring it
(`_read_outbox_task_result` discards the return value).

**SimpleBroker claim contract (verified):**
`Queue.read_one(exact_timestamp=…, with_timestamps=…, order=…)` is the claim
operation — "exactly-once delivery semantics: the message is committed before
being returned" — and returns `None` when "queue is empty or message not
found". A peeked row can therefore be gone by the time it is claimed. That is
the basis of the concurrent-reader rule in §4.

### Files to modify

- `weft/commands/_streaming.py` — one new helper, `read_next_outbox_result`.
- `weft/commands/result.py` — new `await_next_task_output`; extend
  `_validate_public_result_request` `:934-955` and `cmd_result` `:976-1043`
  with `nowait`; extend `_require_available_result` `:967-973` with the
  `"empty"` case; extend `__all__` `:1046`. **Nothing is deleted from the
  existing wait path** — see task 5.
- `weft/commands/_result_wait.py` — **no change** (it is `weft run`'s waiter
  and the one-shot branch of the retained `_await_single_result`). Its
  `WEFT_COMPLETED_RESULT_GRACE_SECONDS` import stays.
- `weft/_constants.py` — **no change**.
  `WEFT_COMPLETED_RESULT_GRACE_SECONDS` (`:1934`, value `0.5`) keeps three
  live consumers in `weft/commands/events.py` (`:321`, `:596`, `:629`), four
  in `_result_wait.py` (`:266`, `:295`, `:320`, `:331`), and six in the
  retained persistent branch of `result.py`; it is **not** deleted, and
  `tests/system/test_constants.py:329` stays green. (The superseded plan's
  claim that events.py uses it is confirmed.)
- `weft/client/_task.py` — `Task.result()` `:51-52` routes through the new
  reader and the shared translation helper.
- `weft/cli/app.py` — `--nowait` option on `result_command` `:2140-2261`.
  `_command_error_code` `:68-83` is **unchanged**: `TaskNotFound → 2`,
  `CommandTimeoutError → 124` already exist. `_command_exit` `:85-89` renders
  a typed error as one plain-text stderr line and exits — **not** JSON; that
  is the mechanism behind register row 11.
- Specs: `05-Message_Flow_and_State.md`, `10-CLI_Interface.md`,
  `14-Python_API_Surfaces.md` (delta below, plus each file's
  `## Related Plans` section at `:1542`, `:1032`, `:282`).
- `CHANGELOG.md` (Unreleased → Changed).
- Tests to write or update: `tests/commands/test_result.py`,
  `tests/cli/test_cli_result.py`, `tests/core/test_client.py`.
- **Unchanged-and-must-stay-green pins (do not edit these files):**
  `tests/cli/test_cli_run.py`, `tests/commands/test_run.py`,
  `tests/commands/test_run_public.py`, `tests/cli/test_cli_result_all.py`,
  `tests/cli/test_result_claimed_json.py` (`:43` monkeypatches
  `result_cmd._await_single_result` by name — see the forbid-inlining rule in
  task 5), `tests/commands/test_observation_connections.py:166-235` (its
  `persistent=True` parametrization calls
  `result_cmd._await_single_result(..., taskspec_payload={"spec":
  {"persistent": True}})` at `:218-226`; PostgreSQL-only, skipped on SQLite —
  run it under the postgres backend or state plainly that it was skipped),
  `tests/core/test_ops_shared.py`, `tests/system/test_constants.py`, and
  `integrations/weft_django/tests/test_weft_django.py` (register row 12).

### Read first (with comprehension questions)

Read: [MF-5] `:795-865`; [CLI-1.2.2] `:475-498`; [PY-2] `:83-95` and `:165`;
`weft/commands/result.py` in full; `weft/commands/_result_wait.py:116-352`;
`weft/core/outbox.py:37-101`; `weft/commands/_streaming.py:82-121`;
`weft/commands/run.py:426-442`, `:585-610`, and `:1292-1295`;
`weft/cli/app.py:68-89`, `:273-313`, `:2140-2261`; `weft/cli/run.py:78-122`;
`weft/core/tasks/consumer.py:285-295` and `:840-865`.

Answer before editing:

1. Which function does `weft run` use to wait, and does this plan touch it?
   (Answer: `_result_wait.py::await_one_shot_result`, reached from
   `run.py:603` and from `RunSession.wait()` via `await_task_result` →
   `_await_single_result`. No.)
2. Which single function already turns a stream-frame sequence into one
   value, and what does it return for a non-final frame? (Answer:
   `weft/core/outbox.py::process_outbox_message`; `(False, None)`.)
3. Why must the new reader *peek* before it consumes? (Answer: a partial
   stream item whose `final` frame has not been written yet must stay in the
   outbox for the next call. Reading it into a discarded local buffer would
   lose output — a diminution.)
4. What does `_claimed_result_blockage` prove, and which invariant forbids
   consuming that row? (Answer: `claimed_result_without_terminal` recovery
   evidence; [OBS.14].)
5. Who still calls `_await_single_result` after this change, and what happens
   to a persistent task's outbox if its persistent branch is deleted?
   (Answer: `RunSession.wait()`, `follow_task_events`, and
   `weft result --stream`'s tail, all through `await_task_result`. Deleting
   the branch routes them to `await_one_shot_result`, which drains and merges
   the entire visible outbox each turn (`_result_wait.py:205-209`) and can
   raise `CommandTimeoutError` with rows already consumed (`:213-218`,
   `:343-352`) — output loss. So it stays.)
6. Why can the reader publish exactly-once semantics without a lease?
   (Answer: it cannot, across concurrent readers. `read_one(exact_timestamp=…)`
   is the claim; `None` means another reader took the row. The rule is one
   `weft result` reader per task — see §4 "Concurrent readers".)
7. Why does the reader need no grace window between terminal proof and the
   outbox row? (Answer: `consumer.py:291` writes the outbox row via
   `_emit_result` **before** `_report_state_change` emits
   `work_item_completed` `:846` / `work_completed` `:860`, on the same broker.
   Task-authored terminal proof therefore implies row visibility.)

## 4. Invariants and Constraints

**Must not change:**

- **`weft run` exit codes and wait semantics.** `await_one_shot_result`,
  `_wait_for_task_completion`, `RunSession.wait()`, and
  `weft/cli/run.py:80-121` are untouched. A `weft run` of a failing command
  still exits 1 and still prints the task's error.
- **`weft result --all` and `--peek`.** `_collect_all_task_results` and
  `_read_outbox_task_result` are untouched; `--peek` keeps requiring `--all`
  (Open Owner Question 2) and keeps not consuming.
- **`weft result --stream`.** `_result_task_event_stream` keeps calling
  `await_task_result` (the status-driven waiter), so its tail check —
  "raise `TaskNotFound` iff `status == "missing"`" — behaves exactly as
  today. This is the reason the new reader is a *separate function* rather
  than a mode flag on `await_task_result`: a completed task with an empty
  outbox must not start raising `TaskNotFound` out of `--stream`.
- **The shared status-driven waiter and both its branches.**
  `await_task_result` `:730-806` and `_await_single_result` `:423-727`
  (one-shot **and** persistent) are kept for their three remaining callers.
  The persistent branch keeps its boundary resolution, quiet period, and
  grace clamps; `ResultMaterialization.batch_boundary_timestamps` `:75` and
  the `initial_batch_boundary_timestamps` keyword `:438` keep their
  collection sites (`:264-269`) and their call site (`:796`). Moving
  `--stream` and `follow_task_events` onto the one-row rule is a **separate
  decision**, not part of this plan (register row N).
- **`_await_single_result` must not be inlined into `await_task_result`.**
  `tests/cli/test_result_claimed_json.py:43` monkeypatches
  `result_cmd._await_single_result` by name to assert the claimed-residue
  path never waits, and
  `tests/commands/test_observation_connections.py:218` calls it directly.
  Inlining silently disarms the first test and breaks the second.
- **[OBS.14] claimed-residue recovery.** `_claimed_result_blockage` runs
  before the read, unchanged, and still returns `status="failed"` with
  `reconciliation` (exit 1, `--json` shape per [CLI-1.2.2] `:493-496`).
- **Materialization.** `_await_result_materialization` keeps its current
  contract, including "no evidence + no `--timeout` ⇒ return `None` ⇒
  `status="missing"` ⇒ exit 2" and "custom `io.outputs.outbox` names are
  resolved before reading". Blocking-by-default applies to the *result
  surface*, not to proving the TID exists; without this, an unknown TID would
  hang forever, which is a diminution against today's prompt exit 2.
- **Non-consumption of what is not returned.** The reader consumes exactly
  the rows whose decoded value it returns. Non-final stream frames, foreign
  rows written after the returned item, and claimed rows are left in place.
  This holds on every exit path: the `--nowait` path, the deadline path, and
  the terminal-empty path all consume nothing when they return nothing.
- **One drain path in the *new* reader.** `await_next_task_output` has **no**
  `persistent` branch and never calls `_is_persistent_task`. One-shot and
  persistent tasks are read identically, because the row-level contract is
  identical. `_is_persistent_task` itself is **retained** — the shared waiter
  still dispatches on it.
- **`--timeout 0` is a zero-second wait, not `--nowait`.** Both are
  non-blocking, but they are different requests and have different exit
  codes: `--timeout 0` expiring with nothing available is 124 (an expired
  caller timeout, today's behavior at `test_result.py:791-803`), while
  `--nowait` with nothing available is 2. Do not collapse them.
- TID format/immutability, forward-only state transitions, reserved-queue
  policy, `spec`/`io` immutability, `weft.state.*` runtime-only queues, and
  spawn-context behavior: not touched by this plan at all. No producer file
  (`consumer.py`, `base.py`, `interactive.py`) is edited.
- **No new queue, no new suffix, no new persisted format, no membership or
  ID scheme.** The superseded plan's `T{tid}.results` is not created.

**Hidden couplings, named before decomposition:**

- `await_task_result` has five callers (table in §3); three of them are
  *not* `weft result`. Changing the function in place — or deleting either
  branch beneath it — would silently change `RunSession.wait()`,
  `follow_task_events`, and `--stream`.
- `WEFT_COMPLETED_RESULT_GRACE_SECONDS` is shared with `events.py`, with
  `_result_wait.py`, and with the retained persistent branch; deleting the
  constant would break the realtime surface and
  `tests/core/test_ops_shared.py:279`.
- `drain_ctrl_out_stream_messages` (`_result_wait.py:82-113`) **consumes**
  non-terminal ctrl_out rows while rendering them, and retains terminal
  envelopes. The new reader must reuse it (not re-implement it) so live
  ctrl_out stream rendering and terminal-proof retention stay identical, and
  must call it **before** the outbox read on each turn — the same order the
  retained persistent branch uses (`result.py:508-527` drains ctrl_out, then
  peeks the outbox at `:529`). Reversing the order would drop or delay
  ctrl_out chunks on the turn that finds a result.
- Pipeline TIDs: `ctrl_out_for_wait` is `None` when
  `is_pipeline_taskspec_payload(...)` is true (`result.py:451-453`). The new
  reader must keep that, or it will consume pipeline control traffic.
- `_render_task_result` (`cli/app.py:273-313`) maps `status == "timeout"` to
  exit 124. After this change a terminal-timeout task with an empty outbox
  raises `TaskNotFound` before reaching the renderer — see register row 4.
- `_command_exit` (`cli/app.py:85-89`) renders **any** typed error as one
  plain-text stderr line. A `TaskNotFound` therefore bypasses the JSON
  renderer even under `--json` — register row 11.
- `weft.client.Task.result()` has external consumers:
  `integrations/weft_django/weft_django/client.py:55`, `:58`, `:98-99`,
  `:139-140`, `:180`, and `:611-612` all reach it. They receive
  `TaskResult(status="missing")` today and will receive a raised
  `TaskNotFound` — register row 12.

**Concurrent readers (documented limitation, not a mechanism).** The reader
claims each decoded row with `read_one(exact_timestamp=<peeked id>)`. When
that returns `None`, another reader claimed the row: the reader must **not**
return a value it did not claim — it continues to the next row and, for a
multi-row stream item, discards the partial buffer for the lost row and
resumes reassembly from the next claimable row. Consequence: **concurrent
`weft result` readers on a streaming task are unsupported and frames may
split across readers.** No claim, lease, or reservation mechanism is added
(owner decision, 2026-09-10, on Codex finding 2). This is stated as a rule in
both [CLI-1.2.2] and [MF-5] and pinned by a two-reader test for the
single-row case only.

**Why no terminal drain window (Codex finding 4, declined).** The reader
returns `status="empty"` after one final non-blocking read once terminal proof
is seen, with **no** bounded drain. The reason is a write-ordering fact:
`consumer.py:291` writes the outbox row (`_emit_result` `:875` →
`_emit_single_output` `:883-909`, writing at `:905` or `:908`) **before**
`_report_state_change` emits `work_item_completed` (`:846`) or
`work_completed` (`:860`), and both go to the same broker. Task-authored
terminal proof therefore implies the row is already visible. The only
manager-authored terminal proof this reader accepts is the `wrapper_lost`
envelope (`_result_wait.py:73-79`), which means the producer is gone and no
row will arrive. [EXEC.6] and [EXEC.7] govern the private runner → Consumer
terminal handoff (the producer side), not the CLI reading `T{tid}.outbox`;
they are not in this reader's scope. Owner decision, 2026-09-10 — do not
reopen. **Verification obligation:** the "terminal + one row" acceptance cell
(register row 3) and the "terminal + empty → 2" cell together pin both
directions of this ordering.

**Error-path priorities:** a `BrokerError` during the exact-ID read
propagates as `CommandExecutionError` (fatal, exit 1). A malformed outbox row
is decoded as a plain string by `decode_result_payload` (best-effort, as
today). Rendering a ctrl_out stream chunk is best-effort and must not
downgrade a successful read.

**Rollback:** revert the single atomic change. No persisted format, queue
name, or event payload is altered, so an old binary and a new binary read the
same queues correctly; the only cross-version difference is how many rows one
call consumes. Rollout order does not matter. **No one-way door.**

**Review gates:** no new execution path (`weft run`'s waiter is untouched and
no second producer is added); no new dependency; no new exception class; no
drive-by refactor of `_result_wait.py`; independent review before
implementation (Class 5 + risky trigger).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `e158d982` — docs/specifications/05-Message_Flow_and_State.md,
  10-CLI_Interface.md, 14-Python_API_Surfaces.md, 07-System_Invariants.md at
  plan authoring time (2026-09-10). Verified byte-identical at `7792178a`
  (`git diff e158d982..7792178a --` over those four files is empty), so the
  spec baseline holds on current main.
- Code anchor baseline: **`7792178a`** (current main at revision round 1).
  `weft/commands/result.py` and `weft/commands/_result_wait.py` changed
  between `178e3a34` and `e158d982`; every code line number in this plan was
  re-read at `7792178a`.
- Promotion strategy B: the promotion baseline identifier is the landing
  commit of tasks 3-7, recorded at closeout.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | B | [MF-5] `:842-845` replace (1 bullet → 3); `:846-847` **kept unchanged**; `:850-852` replace; `:853-855` replace |
| docs/specifications/10-CLI_Interface.md | B | [CLI-1.2.2] `:477-479` mapping replace; `:483-485` replace (2 bullets → 5) |
| docs/specifications/14-Python_API_Surfaces.md | B | [PY-2] `:90-95` `TaskResult` bullet replace |

**Why B (atomic), not A:** `weft/commands/result.py`'s module docstring
already cites [CLI-1.2.2] and [PY-2], and the new reader is named in the
[CLI-1.2.2] mapping and the [PY-2] ownership bullet. Strategy A (text now,
link claims later) would leave the repo holding a mapping claim to a symbol
that does not exist yet (`SPEC_MAPPING_RECIPROCAL_MISSING` /
`SPEC_SECTION_MISSING` debt) between slices. Requirement text, mapping
claims, code, and reciprocal backlinks land together.

**[MF-5] `:846-847` is NOT touched.** "persistent result waits treat both
`work_item_completed` and `work_completed` as completion boundaries for the
same task" remains true of the retained persistent branch of
`_await_single_result`, which `follow_task_events` and `weft result --stream`
still use. Deleting that bullet would leave shipped behavior unspecified.

### [MF-5] `:842-845` — replace the one bullet "`weft result` and `weft run` share the same wait helper path: … and do one last non-blocking outbox drain before returning `completed`"

> - `weft result TID` (without `--stream`) is an outbox read, not a status
>   wait. It blocks until one result is available in the task's outbox or the
>   task reaches a terminal status, and it has no default timeout. It must not
>   infer a result boundary from event timestamps, from a quiet period, or
>   from a completion-event grace window: the queue is the only authority. No
>   grace window is required, because the task writes its outbox row before it
>   publishes the corresponding completion event on the same broker. Task
>   failure is reported by status surfaces, not by `weft result` — when the
>   task is terminal and no result is available, the reader reports the
>   absence with the terminal status named and the caller uses
>   `weft task status TID` for the failure detail.
> - one result is one `T{tid}.outbox` row, claimed by the call that returns
>   it; successive calls return successive rows. The reader joins several rows
>   into one result only for an encoding the producer declares in the rows
>   themselves: a stream-frame sequence is reassembled through the frame
>   marked `final`. A task that emits several independent outputs, including
>   an agent task with several outputs, publishes one row per output and
>   callers loop. A stream item whose `final` frame is not yet visible stays
>   unclaimed. Concurrent `weft result` readers on one task are unsupported:
>   each row is claimed exactly once, so a multi-row stream item may split
>   across readers.
> - `weft run`, `RunSession.wait()`, `weft result --stream`, and the realtime
>   task-event surfaces are unchanged by that read: they keep the shared
>   status-driven wait helper, its terminal-state interpretation, its
>   completion grace rules, and their existing exit codes.

### [MF-5] `:850-852` — replace "when a one-shot streamed result has already been emitted to the caller, shared result waiters retain that emitted-result fact as completion evidence so a delayed terminal log cannot turn visible output into a later timeout"

> - when a one-shot streamed result has already been emitted to the caller,
>   the shared status-driven completion waiter behind `weft run`,
>   `RunSession.wait()`, and `weft result --stream` retains that
>   emitted-result fact as completion evidence so a delayed terminal log
>   cannot turn visible output into a later timeout

### [MF-5] `:853-855` — replace "`weft result` first waits for taskspec metadata or outbox/control queue names to materialize when those surfaces are not yet visible, then falls back to the shared completion wait"

> - `weft result` first resolves taskspec metadata or outbox/control queue
>   names, waiting for them to materialize within an explicit `--timeout` when
>   those surfaces are not yet visible, and then reads the result surface.
>   When no evidence of the TID is visible at all and no explicit timeout was
>   supplied, the command reports the task as absent instead of blocking.

### [CLI-1.2.2] `:477-479` — replace the implementation-mapping block

> _Implementation mapping_: `weft/commands/result.py::cmd_result`,
> `weft/commands/result.py::await_next_task_output` (the `weft result TID` /
> `weft.client.Task.result()` outbox read),
> `weft/commands/result.py::_collect_all_task_results` (`--all`), and
> `weft/commands/result.py::await_task_result` with
> `weft/commands/result.py::_await_single_result` (the status-driven wait that
> `--stream`, `RunSession.wait()`, and the realtime surfaces keep using).

### [CLI-1.2.2] `:483-485` — replace the first two "Current behavior" bullets ("`weft result TID` waits for or reads the next completed result for a task" and the `--stream` bullet)

> - `weft result TID` blocks until one result is available in the task's
>   outbox or the task is terminal, and claims exactly the row — or the
>   producer-declared multi-row stream item — it returns. It has no default
>   timeout. Repeated calls return successive results; a task that produced
>   several outputs needs several calls. Concurrent `weft result` readers on
>   one task are unsupported: each row is claimed exactly once, so a
>   multi-row stream item may split across readers.
> - `weft result TID` exits 2 when no result is available: an unknown TID, an
>   outbox that was never written, or a terminal task whose outbox is empty.
>   In the terminal case it writes one line to stderr naming the terminal
>   status and pointing at `weft task status TID`; `weft result` does not
>   print the task's error text, and `--json` does not carry the absence as a
>   result document. Exit 2 is SimpleBroker's empty-queue convention and
>   Weft's existing not-found code.
> - `weft result TID --nowait` does not block: it returns the first available
>   result or exits 2. It cannot be combined with `--timeout`, `--all`, or
>   `--stream`.
> - `weft result TID --timeout N` bounds the whole call, including any wait
>   for the result surface to materialize; expiry with nothing available exits
>   124. `--timeout 0` is a zero-second wait, so it also exits 124 rather than
>   2 — use `--nowait` for the not-found outcome.
> - `weft result TID --stream` follows unread outbox stream chunks for that
>   one task while still using the shared status-driven wait, including the
>   same task-log completion and grace rules; the single-row read contract
>   above does not apply to it.

### [PY-2] `:90-95` — replace the `TaskResult` bullet

> - `TaskResult` retains its result fields and carries optional
>   `reconciliation: dict[str, Any] | None = None` so the CLI can render the
>   existing claimed-result metadata contract [CLI-1.2.2]. `weft result` and
>   `weft.client.Task.result()` are outbox reads owned by
>   `weft/commands/result.py::await_next_task_output`: they return one result
>   per call and raise `TaskNotFound` when none is available, which the CLI
>   renders with exit code 2. Within that owner, `status="missing"` means the
>   TID itself is not visible and `status="empty"` means the task is terminal
>   with no result row; both translate to `TaskNotFound`.
>   `weft/commands/result.py::await_task_result` remains the status-driven
>   wait behind `RunSession.wait()`, `weft result --stream`, and the realtime
>   event surfaces, and its `RunExecutionResult` contract is unchanged. The CLI renders that evidence
>   without another lifecycle probe. Implementation plans:
>   [Dead generation retirement](../plans/2026-08-31-dead-generation-retirement-plan.md),
>   [Weft result outbox contract](../plans/2026-09-10-weft-result-outbox-contract-plan.md).

### Backlinks (same slice)

Append this plan to `## Related Plans` in
`05-Message_Flow_and_State.md:1542`, `10-CLI_Interface.md:1032`, and
`14-Python_API_Surfaces.md:282`.

## Observable-Difference Register

Legend: **A** owner-decided change · **C** no-loss change · **N** no
observable difference · **D** diminution (none permitted).

| # | Observable change | Class | Verification against shipping default |
|---|---|---|---|
| 1 | `weft result TID` on a terminal task with an empty outbox exits 2 with a one-line stderr status hint, instead of exit 1 with the task's error text | A (owner, 2026-09-10; item 7 of the decided contract) | Failure detail is unchanged and still reachable through `weft task status TID` / `weft result --json` is no longer the carrier. Firing tests: task 6 cells "terminal+empty → 2" and "`weft task status` still names the error". CHANGELOG. |
| 2 | A task that wrote several independent outbox rows returns one per `weft result` call; the old call merged them into a list | A (owner; item 7) | No output is lost — successive calls return successive rows, and nothing is consumed that is not returned. Firing tests: "persistent three inputs read back one per call", "one-shot two rows → two calls". CHANGELOG. |
| 3 | `weft result TID` on a **failed** task that *did* write output returns that output and exits 0 (today: prints the error, exit 1) | A (derived from item 1 — `weft result` is outbox-only) | Strictly more output than today; the failure verdict moves to `weft task status`. Firing test: task 6 cell "failed task with one outbox row". Covered by the CHANGELOG line. |
| 4 | `weft result TID` on a task whose *task* timed out with an empty outbox exits 2, not 124 | A (derived from item 1) | 124 now means only "the caller's `--timeout` expired". The terminal status `timeout` is named in the stderr line and unchanged in `weft task status`. Firing tests: "terminal timeout + empty → 2" and "`--timeout` expiry → 124". |
| 5 | `weft.client.Task.result()` raises `TaskNotFound` for the nothing-available outcome instead of returning `TaskResult(status="missing")` | A (owner; item 6 — "raise the existing typed not-found error that maps to exit 2") | Reuses `TaskNotFound`; no new exception class. `Task.result()` and `cmd_result` now share one translation helper, so the CLI and client agree. Firing tests in `tests/core/test_client.py`. |
| 6 | New `weft result --nowait` flag | C (additive) | No existing invocation changes. Firing tests: "`--nowait` empty → 2", "`--nowait` with a row → 0", "`--nowait --timeout` → usage error, exit 2". |
| 7 | A partial (non-final) stream item is no longer consumed by a `weft result` call that returns nothing | C (defect fix; today's drain discards those frames) | Today `weft result TID --timeout 0` on a partial stream consumes the frames and then raises (`tests/commands/test_result.py:932`). Firing test: "partial stream, `--nowait` → 2 and the frames are still in the outbox". |
| 8 | `weft run` (all forms), `weft result --all`, `--peek`, `--stream`, `--error`, `RunSession.wait()`, `follow_task_events`, and `--json` **for every case that still returns a `TaskResult`** (success, claimed residue, `--all`) | N | None of the code behind them is edited; `await_task_result` and both branches of `_await_single_result` are retained. The task-2 characterization pins are recorded before and rerun after, byte-identical. Scoped in round 1: terminal-empty `--json` moved to row 11 (Codex 6 / Claude 3). |
| 9 | `WEFT_COMPLETED_RESULT_GRACE_SECONDS` behavior on `weft run` and the realtime events surface | N | The constant and both consumers are untouched; `tests/system/test_constants.py:329` and `tests/core/test_ops_shared.py:279` stay green unmodified. |
| 10 | Claimed-residue recovery (`claimed_result_without_terminal`) | N | `_claimed_result_blockage` runs before the read, unchanged; `tests/cli/test_result_claimed_json.py` stays green unmodified ([OBS.14]). |
| 11 | `weft result TID --json` on a terminal task with an empty outbox emits one plain-text stderr line and exit 2 instead of a JSON result document with the task's error | A (consequence of row 1; recorded separately in round 1) | Mechanism: `cmd_result` raises `TaskNotFound`, and `cli/app.py::_command_exit` `:85-89` renders any typed error as plain stderr text — this is the same rendering `weft result --json` already produces for an unknown TID today, so the shape is not new, only the case that reaches it. The claimed-residue `--json` contract ([CLI-1.2.2] `:493-496`) is untouched (row 10). Firing test: a CLI cell asserting `--json` + terminal-empty → exit 2, no JSON on stdout, status named on stderr. |
| 12 | `weft.client.Task.result()` consumers in `integrations/weft_django/weft_django/client.py` (`:55`, `:58`, `:98-99`, `:139-140`, `:180`, `:611-612`) see a raised `TaskNotFound` where they saw `TaskResult(status="missing")` | A (consequence of row 5) | Observable change for Django callers: `weft_django.result(tid)` and `Submission.result()` now raise instead of returning a sentinel `TaskResult`. `TaskNotFound` is already exported and already raised by these paths for other absences, so no new exception type reaches Django. Gate: `integrations/weft_django/tests/test_weft_django.py` is added to the final gates in §7; any cell asserting `status == "missing"` is a stop-and-report, not a quiet edit. |
| N | The persistent inference machinery (`_resolve_persistent_result_boundary`, `_latest_visible_outbox_timestamp`, `_drain_outbox_until_timestamp`, the quiet-period/boundary state, `initial_batch_boundary_timestamps`, `ResultMaterialization.batch_boundary_timestamps`, the six grace clamps) is **retained**, not deleted | N (no observable difference; it keeps serving `--stream`, `follow_task_events`, and `RunSession.wait()`) | This is the round-1 structural correction. The batch-merge defect reproduced in §1 therefore **remains reachable through `weft result --stream` and `follow_task_events`** for persistent tasks; only `weft result TID` and `Task.result()` are fixed. Moving those surfaces onto the one-row rule is a separate owner decision and a separate plan — it is not in scope here, and it is not a diminution, because nothing about them changes. Verification: the retained-branch tests (`tests/commands/test_result.py:1453`, `:1517`, `:1632`, `:1799`, `:1844`, `:1891`, `:1937`) stay green **unmodified**. |

No D rows. If implementation produces one, stop and re-plan (owner rule:
no capability diminution beyond rows 1 and 2).

## Open Owner Questions

**None open.** Three items were raised and all three are decided (Van,
2026-09-10). They are kept below for the review trail, not as pending work.
Round-1 review confirmed both recommended defaults against the code
(Claude: "both owner-question defaults match code").

- Q1 decided: keep as-is — the spill reference row is returned as written
  (rationale: the spill file lives on the producing host while the broker
  may be shared across hosts under the PostgreSQL backend; a reader that
  dereferenced "when readable" would be inference; explicit `--deref` is a
  possible separate follow-up).
- Q2 decided: `--peek` unchanged, `--all`-only; extending it to a single
  TID is a one-line follow-up if wanted.
- Q3 decided (raised in round-1 review as Codex 2): concurrent `weft result`
  readers are a **documented limitation**, not a mechanism. No claim, lease,
  or reservation is added; the reader claims with
  `read_one(exact_timestamp=…)` and treats `None` as "another reader took
  it". Stream-frame reassembly requires a single reader per task, stated as a
  rule in [CLI-1.2.2] and [MF-5] and pinned by a two-reader test for the
  single-row case only. See §4 "Concurrent readers".
- The two derived exit-code consequences (terminal `timeout` with an empty
  outbox → 2; a failed task with output → its output and exit 0) are
  accepted as consequences of the contract and stay in the register as A.
- Codex finding 4 (a bounded terminal drain window on the result path) is
  **declined**, with the reason recorded in §4 "Why no terminal drain
  window". Do not reopen.

1. **"a spill reference row is resolved to the full value (as today)".** The
   code does not resolve it today and the spec says so explicitly:
   `05-Message_Flow_and_State.md:1178`, in the `### Current Consumer and CLI
   Behavior` subsection just above the [MF-2.1] heading — "`weft result` does
   not currently auto-dereference
   large-output references". No reader of the spill path exists anywhere in
   `weft/` (grep `large_output`: writer sites only). **Recommended default:**
   keep today's behavior — the `{"type":"large_output", …}` row is *one*
   outbox row and is therefore already one complete result under the new
   contract, returned as the reference envelope. This needs **zero** extra
   code and no [MF-2.1] delta, and it preserves behavior exactly (register
   row: none needed). If the owner meant auto-dereference, that is a new
   capability (read the file, verify `sha256`, decide what happens when the
   path is on another host) and belongs in its own plan; say so and this plan
   is unaffected.
2. **"`--peek` stays orthogonal (same read, non-consuming)".**
   `weft result TID --peek` is a usage error today:
   `_validate_public_result_request` `:948` raises
   `CommandUsageError("peek requires all")`. **Recommended default:** read
   "stays" literally — `--peek` is unchanged and remains an `--all` modifier;
   the "`--peek` non-consuming" acceptance cell is the existing
   `weft result --all --peek` regression, kept green unmodified. Extending
   `--peek` to a single TID is a new CLI capability and, with `--all` out of
   scope, has no home in this plan. If the owner wants it, it is a small
   follow-up: drop the validator line, and pass `peek=True` through to the
   new reader so it decodes without consuming.

## 5. Tasks

Dependency-ordered. Tasks 3-7 land as **one commit** (strategy B); the split
exists so review and per-task verification stay small.

1. **Independent review of this plan and its Proposed Spec Delta** (§8) —
   before any edit. Class 5 + risky trigger means review precedes
   implementation, not just landing.
   - Round 1 is complete and recorded in `## Review Record` (both reviewers
     BLOCKED; every finding dispositioned). This revision answers it.
   - Done when: a scoped round-2 verification confirms the retained-waiter
     correction and the new §4 rules, recorded as a round-2 entry in
     `## Review Record`.

2. **Characterization pins for the surfaces that must not change.**
   - Outcome: a recorded, rerunnable baseline proving `weft run`,
     `--all`/`--peek`, `--stream`, `follow_task_events`, `RunSession.wait()`,
     and the Django client are byte-identical before and after.
   - Files to touch: none in `weft/`. Run and record:
     `tests/cli/test_cli_run.py`, `tests/commands/test_run.py`,
     `tests/commands/test_run_public.py`, `tests/cli/test_cli_result_all.py`,
     `tests/cli/test_result_claimed_json.py`,
     `tests/commands/test_observation_connections.py`,
     `tests/core/test_ops_shared.py`, `tests/system/test_constants.py`,
     `integrations/weft_django/tests/test_weft_django.py`, and the retained
     persistent-branch cells
     `tests/commands/test_result.py::test_await_single_result_persistent_*`
     (`:1453`, `:1517`, `:1632`, `:1799`, `:1844`, `:1891`, `:1937`).
   - Note: `tests/commands/test_observation_connections.py` skips unless the
     backend is PostgreSQL (`:174-175`). Either run it under postgres or
     record plainly that it was skipped — do not report a skip as a pass.
   - Add, in `tests/cli/test_cli_result.py`, one real-CLI cell if it is not
     already covered: `weft run` of a command that exits non-zero exits 1 and
     prints the error; `weft run` of a target that times out exits 124.
     (Register row 8 and the contract's item 5.)
   - Constraints: no `weft manager` is started by hand; use `WeftTestHarness`
     (`tests/helpers/weft_harness.py`) as the existing CLI tests do.
   - Stop if: any of these files needs editing to pass — that means the plan
     is already changing a surface it promised not to.
   - Done when: all the listed files pass unmodified and the run is recorded.

3. **`read_next_outbox_result` — the peek-then-consume reader.**
   - Outcome: one helper that returns the *first complete* result visible in
     an outbox and consumes exactly the rows that produced it, or returns
     `None` and consumes nothing.
   - Files to touch: `weft/commands/_streaming.py`;
     `tests/commands/test_result.py` (the decoder cells live beside the
     existing `process_outbox_message` tests at `:435`).
   - Read first: `weft/commands/result.py::_read_outbox_task_result`
     `:810-861` (the pattern to copy, and the two differences named in §3
     "Current structure — decoding"), `weft/core/outbox.py:37-101`,
     `weft/commands/_streaming.py:82-121`.
   - Approach: `peek_generator(with_timestamps=True)` in order; feed each
     payload to `process_outbox_message` with a **local** `stream_buffer`,
     recording each peeked timestamp; the first time it returns
     `(True, value)`, close the iterator (`closing_queue_iterator`),
     `read_one(exact_timestamp=…)` each recorded timestamp in order, and
     return the value. If the generator is exhausted without a complete
     value, claim nothing and return `None`.
   - **Claim-race handling (Codex 2 / Decided Owner Question 3).** A
     `read_one(exact_timestamp=…)` returning `None` means another reader
     claimed that row. The helper must not return a value it did not fully
     claim. Concretely: if the value came from a single row and that claim
     returns `None`, discard the value, restart the peek from the top of the
     queue, and continue; if the value came from a multi-row stream item and
     any claim returns `None`, discard the partial buffer, restart the peek,
     and resume reassembly from the next claimable row. Restart at most once
     per call — a second loss returns `None` (nothing claimed) and the caller
     waits another turn. Do **not** add a lease, reservation, or claim table.
     The single-reader rule in §4 is the contract; this is only the guard
     that keeps the helper from returning unclaimed output.
   - Reuse: `closing_queue_iterator`, `process_outbox_message`,
     `DecodedOutboxValue`. Do **not** call `drain_available_outbox_values`,
     and do **not** call `aggregate_public_outputs` (this helper returns the
     first complete value, not an aggregate).
   - Forbidden: a batch/item class; a generic envelope validator; any
     timestamp comparison against events; claiming a row whose value is not
     returned; a bulk range read; a claim/lease/reservation mechanism.
   - Tests (real `Queue` via `build_context`, no mocks): a plain row →
     returned and claimed, queue empty; two plain rows → first returned,
     second still present; three stream frames (`final` last) → one joined
     value, all three claimed; two stream frames with no `final` → `None`
     and **both frames still present**; a `large_output` reference
     row → returned as the envelope dict, claimed (Decided Owner Question 1
     default); a non-JSON row → returned as a plain string; a stderr-only
     final stream frame → skipped per `process_outbox_message`'s
     `(False, None)` and nothing claimed; **two readers, one row** → exactly
     one returns the value and the other returns `None`, and the row is gone
     from the queue exactly once (drive this with two sequential helper calls
     against the same real queue plus one interleaved `read_one` claim, not
     with threads — no timing assertions).
   - Stop if: the helper starts needing knowledge of task state, events, or
     persistence — it must be a pure queue function.
   - Done when: those cells pass.

4. **`await_next_task_output` + the shared not-found translation.**
   - Outcome: `weft result` and `Task.result()` share one outbox-only reader
     with the decided blocking, `--nowait`, and exit-2 semantics.
   - Files to touch: `weft/commands/result.py`, `weft/client/_task.py`,
     `weft/cli/app.py`, `tests/commands/test_result.py`,
     `tests/cli/test_cli_result.py`, `tests/core/test_client.py`.
   - Read first: the [MF-5] and [CLI-1.2.2] deltas above;
     `result.py:175-338`, `:730-805`, `:920-960`, `:1000-1046`;
     `_result_wait.py:82-113`.
   - Approach:
     - `await_next_task_output(context, tid, *, timeout, nowait=False,
       show_stderr=False) -> TaskResult`.
     - Materialize with `_await_result_materialization` exactly as
       `await_task_result` does: `timeout=timeout`,
       `wait_without_timeout=False`. `None` → `TaskResult(status="missing",
       error=f"no outbox queue for task {tid}")` when `timeout is None` or
       `nowait`; `CommandTimeoutError` when an explicit `timeout` expired
       (today's `:750-753` behavior, unchanged).
     - Run `_claimed_result_blockage` next, unchanged ([OBS.14]).
     - Open the outbox and (unless the payload is a pipeline payload) the
       ctrl_out and log queues; build one `QueueChangeMonitor` over them.
     - Each turn: (a) `read_next_outbox_result` — a value returns
       `TaskResult(status="completed", value=…)` immediately, before any
       event handling; (b) `drain_ctrl_out_stream_messages` +
       `select_terminal_envelope` + `terminal_status_from_event` for typed
       terminal proof; (c) `poll_log_events` + `terminal_status_from_event`;
       (d) if a terminal status is known, do **one** final
       `read_next_outbox_result` and, if still empty, return
       `TaskResult(status="empty", value=None, error=f"task {tid} is
       {terminal} with no result output; see: weft task status {tid}")`;
       (e) if `nowait`, return `status="empty"` with a
       `no result available` message; (f) deadline → `CommandTimeoutError`
       with today's message text; (g) `monitor.wait(min(poll_interval,
       remaining))` — **no grace clamp, no quiet period**.
     - `_require_available_result` `:955-961` gains the `"empty"` case:
       raise `TaskNotFound(result.error or …)`. `cmd_result` keeps calling
       it; `Task.result()` starts calling it.
     - `_validate_public_result_request`: reject `nowait` with `all`, with
       `stream`, and with a non-`None` `timeout` (`CommandUsageError`, exit
       2). Keep the existing precedence order — the existing precedence test
       at `tests/commands/test_result.py:2008` must stay green, so append
       the new checks after the existing ones.
     - `cmd_result(..., nowait: bool = False)`; `result_command` gains
       `--nowait` with help text "Return immediately instead of waiting for
       a result".
   - Reuse: `_await_result_materialization`, `_claimed_result_blockage`,
     `drain_ctrl_out_stream_messages`, `select_terminal_envelope`,
     `terminal_status_from_event`, `terminal_error_message`,
     `append_public_value`, `QueueChangeMonitor`,
     `effective_result_surface_wait_interval`, `task_evidence.split_stdio`,
     `_require_available_result`.
   - Forbidden: editing `await_task_result`, `_result_wait.py`, or any
     producer file; a new exception class; a `persistent` branch; a mode flag
     on `await_task_result`; peeking or draining the outbox to *decide*
     anything other than "is a complete result visible"; any use of
     `WEFT_COMPLETED_RESULT_GRACE_SECONDS` in `result.py`.
   - Stop if: `--stream` or `RunSession.wait()` needs a change to stay green —
     that means the new reader was wired into `await_task_result` by mistake.
   - Done when: the task-6 cells pass and task 2's pins are still green.

5. **Delete the inference machinery.**
   - Outcome: `result.py` holds no timestamp comparison, quiet period, or
     completion grace.
   - Files to touch: `weft/commands/result.py`.
   - Delete: `_resolve_persistent_result_boundary` `:403-416`,
     `_latest_visible_outbox_timestamp` `:387-400`,
     `_drain_outbox_until_timestamp` `:356-384`, `_is_persistent_task`
     `:344-352`, `ResultMaterialization.batch_boundary_timestamps` `:74` and
     its two collection sites `:255-260`, the
     `initial_batch_boundary_timestamps` keyword `:434` and its call site
     `:794`, the whole persistent branch of `_await_single_result`
     `:464-716`, and the `WEFT_COMPLETED_RESULT_GRACE_SECONDS` and
     `drain_available_outbox_values` imports.
   - Collapse: `_await_single_result` becomes the pipeline-aware call into
     `await_one_shot_result` only. If nothing but `await_task_result` calls
     it, inline it and drop the `RUFF-SUP-109` C901 suppression; re-verify
     `RUFF-SUP-108` on `_await_result_materialization` with
     `ruff check --extend-select RUF100 weft/commands/result.py` and update
     `docs/ruff-suppression-registry.md` + `bin/ruff_suppression_index.py
     --write` if a suppression became unused.
   - Constraints: `WEFT_COMPLETED_RESULT_GRACE_SECONDS` itself is **kept** in
     `_constants.py:1934` (live consumers in `events.py` and
     `_result_wait.py`); do not touch `tests/system/test_constants.py:329`.
   - Stop if: deleting the persistent branch changes a `weft run` test —
     `weft run` never used it (`run.py:603` goes straight to
     `await_one_shot_result`), so a failure there means something else moved.
   - Done when: `grep -n "boundary\|quiet\|GRACE" weft/commands/result.py`
     returns nothing, and mypy + ruff are clean.

6. **Retire and rewrite the result tests; add the acceptance matrix.**
   - Files to touch: `tests/commands/test_result.py`,
     `tests/cli/test_cli_result.py`, `tests/core/test_client.py`.
   - **Retire** (their subject is deleted machinery — record the retirement
     in the CHANGELOG entry):
     `test_await_single_result_persistent_returns_quiet_visible_output_without_boundary`
     `:1517`; `test_await_single_result_reuses_materialized_batch_boundary_state`
     `:1799`; `test_await_single_result_tolerates_materialized_boundary_timestamp_skew`
     `:1844`; `test_await_single_result_tolerates_late_visible_boundary_timestamp_skew`
     `:1891`; `test_await_single_result_tolerates_late_polled_boundary_timestamp_skew`
     `:1937`.
   - **Rewrite to the new contract** (same names, new assertions):
     `test_await_single_result_persistent_returns_one_work_item_batch`
     `:1453` → three inputs, three calls, one row each;
     `test_await_single_result_persistent_stream_mode_keeps_next_batch`
     `:1632` → the next item's frames stay unconsumed;
     `test_cmd_result_reports_failed_task_without_outbox` `:744` → now
     `TaskNotFound` / exit 2 with the status hint;
     `test_await_task_result_returns_terminal_task_timeout_as_result` `:1349`
     → `await_next_task_output` returns `status="empty"`, and the CLI cell
     asserts exit 2 (register row 4);
     `test_await_single_result_aggregates_multiple_outbox_messages` `:1371` →
     two rows now need two calls (register row 2);
     `test_cmd_result_passes_materialized_state_to_result_wait` `:2171` →
     drop the `initial_batch_boundary_timestamps` assertion.
     (`test_public_cmd_result_preserves_terminal_timeout_outcome` `:209`
     monkeypatches `await_task_result`; repoint it at the new reader and keep
     its pass-through assertion.)
   - **Add** the acceptance matrix, all through the **public**
     `await_task_result`-equivalent entry (`await_next_task_output` and
     `cmd_result` / the CLI runner), against a real broker via
     `build_context` / `WeftTestHarness` — the fixtures the existing cells in
     this file already use:

     | Cell | Setup | Expected |
     |---|---|---|
     | one-shot single row | one plain outbox row | value returned, exit 0, outbox empty |
     | one-shot spill | one `large_output` reference row | reference envelope returned, exit 0 (OOQ 1 default) |
     | stream frames to final | 3 frames, `final` last | one joined value, all 3 consumed |
     | persistent three inputs | rows `b1`,`b2`,`b3` + three `work_item_completed` events | three calls → `b1`,`b2`,`b3`; **replaces the reproduced 3-batch merge defect** |
     | persistent, empty, running, `--timeout` | task `running`, no rows | `CommandTimeoutError` → exit 124 |
     | `--nowait` empty | materialized task, no rows | exit 2, nothing consumed |
     | terminal + empty | `work_failed` event, empty outbox | exit 2, stderr names `failed` and `weft task status` |
     | terminal + one row | `work_failed` event, one row | value returned, exit 0 (register row 3) |
     | unknown TID | nothing written | exit 2 (both default and `--nowait`) |
     | unknown TID + `--timeout` | nothing written | exit 124 (unchanged) |
     | partial stream + `--nowait` | 2 non-final frames | exit 2, both frames still visible (register row 7) |
     | `--peek` non-consuming | `weft result --all --peek` | existing regression, unmodified, still green (OOQ 2) |
     | `weft run` failure exit codes | non-zero command; timing-out command | exit 1 with the error; exit 124 (task 2 pin, rerun) |
     | client | `Task.result()` on an unknown TID and on terminal+empty | raises `TaskNotFound` (register row 5) |

   - **Do not mock**: queues, the broker, task lifecycle, terminal
     envelopes, or result delivery. The only acceptable monkeypatch is the
     existing `_WakeMonitor`/`_NoWaitMonitor` pattern used at `:871` and
     `:942` to make a real queue write happen on a wait turn — it substitutes
     scheduling, not queue semantics. Reaching for anything broader is a
     stop-and-re-evaluate moment.
   - **Red first**: the "persistent three inputs" cell and the
     "terminal + empty → 2" cell must be shown failing at `e158d982` before
     task 4's code lands (the first reproduces the merge defect; the second
     currently returns exit 1 with the error text).
   - Done when: `tests/commands/test_result.py`, `tests/cli/test_cli_result.py`,
     and `tests/core/test_client.py` are green and task 2's pins are
     unmodified and green.

7. **Spec promotion (strategy B, same commit as 3-6), backlinks, CHANGELOG.**
   - Apply every block in `## Proposed Spec Delta` verbatim; append this plan
     to the three `## Related Plans` sections; add the plan's row to
     `docs/plans/README.md`.
   - `CHANGELOG.md` → Unreleased → Changed, one entry:
     "`weft result TID` is now an outbox read. It returns one result row per
     call — a task that produced several outputs needs several calls — and
     exits 2 with a status hint when the task is terminal with no result
     output, instead of printing the task's error. `weft result TID --nowait`
     returns immediately. Task failure detail moves to `weft task status TID`.
     `weft run`, `weft result --all`, `--peek`, and `--stream` are unchanged."
   - Verify: `./.venv/bin/python -m pytest tests/specs/ -q`.

8. **Traceability reconciliation.**
   - Grep the specs for `_await_single_result` and
     `_resolve_persistent_result_boundary` and update every
     `_Implementation mapping_` that names a deleted symbol; confirm
     `result.py`'s module docstring still cites [CLI-1.2.2] and [PY-2] and
     add [MF-5]; close the deviation log; record the promotion baseline
     identifier; rerun the final gates from current state.

## 6. Testing Plan

Harness and fixtures: `WeftTestHarness` (`tests/helpers/weft_harness.py`)
for CLI/lifecycle cells; `build_context(spec_context=root)` plus real
`Queue` objects for the queue-semantics cells, matching the existing style of
`tests/commands/test_result.py`. Never start a `weft manager` by hand.

What each group protects:

- The task-3 cells protect the **non-consumption** invariant (nothing is
  consumed unless its value is returned) and the two producer-declared
  reassemblies.
- The task-6 matrix protects the **public contract**: exit codes 0/2/124,
  one-row-per-call, terminal-and-empty, unknown TID, and `--nowait`.
- The task-2 pins protect the **unchanged** surfaces (register rows 8-10);
  they are the evidence that `weft run` exit codes did not move.

Edge cases deliberately in scope: partial stream + `--nowait` (row 7);
terminal-timeout + empty (row 4); failed-with-output (row 3). Deliberately
out of scope: `--all` consume-prefix semantics, the realtime events grace,
PostgreSQL-specific behavior (nothing in this change is backend-sensitive —
it uses `peek_generator` and `read_one(exact_timestamp=…)`, both already
exercised on both backends by `_read_outbox_task_result`).

Anti-flake: no cell asserts on elapsed time. The `--timeout` cells use an
explicit small timeout and assert the exception type, not the duration. No
cell sleeps for a grace window, because the new reader has none.

Runtime observation after the change (hardening §9): on a real persistent
task, `weft queue peek T{tid}.outbox` after N `weft result` calls should show
exactly the un-returned rows, and `weft task ping TID` should show the task
still running with zero residue.

## 7. Verification and Gates

Per task (fast):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/commands/test_result.py -q          # tasks 3-6
./.venv/bin/python -m pytest tests/cli/test_cli_result.py -q           # task 4, 6
./.venv/bin/python -m pytest tests/core/test_client.py -q              # task 4, 6
./.venv/bin/python -m pytest tests/specs/ -q                           # task 7
```

Unchanged-surface pins (tasks 2 and 6, byte-identical files):

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_run.py tests/commands/test_run.py \
  tests/commands/test_run_public.py tests/cli/test_cli_result_all.py \
  tests/cli/test_result_claimed_json.py tests/core/test_ops_shared.py \
  tests/system/test_constants.py -q
```

Final gates (before claiming done):

```bash
./.venv/bin/python -m pytest -m "" -q
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 weft/commands/result.py
```

Rollback: `git revert` the single commit. No format, queue, or event payload
changed, so no data migration and no rollout ordering.

## 8. Independent Review Loop

One round, two reviewers, before implementation (Class 5 + risky trigger):

- **Codex** (different agent family) and **Claude** (same family), one round
  each, findings merged into `## Review Record` with an explicit disposition
  per finding.
- Reviewers read: this plan including `## Proposed Spec Delta` and the named
  promotion strategy; [MF-5] `:795-865`; [CLI-1.2.2] `:475-498`; [PY-2]
  `:83-95`; `weft/commands/result.py`; `weft/commands/_result_wait.py`;
  `weft/commands/run.py:426-441` and `:585-609`; `weft/cli/app.py:68-80`,
  `:273-313`, `:2139-2260`.
- Stance: *Read the plan and its Proposed Spec Delta. Look for errors, bad
  ideas, latent ambiguities, and performative overengineering — recommending
  removal is as valuable as recommending additions. Specifically: (1) name any
  caller of `await_task_result` or `await_one_shot_result` this plan's caller
  table missed, and any surface whose behavior would change without a register
  row; (2) check that the new reader consumes nothing it does not return, on
  every path including the deadline and `--nowait` paths; (3) check the two
  Open Owner Questions against the code and say whether the recommended
  default is right; (4) say whether the exact spec text, as written, would let
  a zero-context engineer implement this correctly.*
- Feedback returns to the author, who updates the plan or records why the
  point is out of scope. A revision that changes invariants, ownership, or
  blast radius re-enters review.

## 9. Out of Scope

- `weft result --all` and its consume-prefix semantics
  (`_collect_all_task_results`, `_read_outbox_task_result`).
- `--peek` beyond `--all` (Open Owner Question 2).
- `weft/commands/events.py`'s use of `WEFT_COMPLETED_RESULT_GRACE_SECONDS`
  and the realtime events surface generally.
- Any membership, output-ID, or row-identity scheme — including the
  superseded plan's `T{tid}.results` queue. Nothing is added to
  `STANDARD_TASK_QUEUE_SUFFIXES`.
- Auto-dereferencing large-output spill references (Open Owner Question 1).
- Producer changes: `consumer.py`, `base.py`, `interactive.py`,
  `pipeline.py`, and the `work_item_completed` / `work_completed` payloads
  are untouched.
- `weft run`, `RunSession`, and `weft task status`.

## 10. Fresh-Eyes Review

Author pass, 2026-09-10, re-read as an engineer who knows Python but not
Weft. Findings, all folded into the text above:

1. **`await_task_result` has five callers, not one.** The first draft changed
   it in place. That would have silently changed `RunSession.wait()`,
   `follow_task_events`, and — worst — `weft result --stream`, whose tail
   check raises `TaskNotFound` on `status == "missing"`: a completed task with
   an empty outbox would have started failing the stream. Fixed by making the
   new contract a separate function and leaving `await_task_result` alone;
   recorded as an invariant and as the reason a mode flag was rejected.
2. **A naive reader loses partial stream output.** Draining rows into a local
   buffer and returning `None` when no `final` frame arrives silently
   discards them — a diminution, and worse under a contract that invites
   repeated calls. Fixed by mandating peek-then-consume and pointing at
   `_read_outbox_task_result` as the existing pattern (register row 7).
3. **"resolved to the full value (as today)" is false of the code.** The spec
   says outright that `weft result` does not dereference spills. Raised as
   Open Owner Question 1 with the behavior-preserving default, which also
   removes the only remaining reason to write reassembly code beyond the
   stream case.
4. **`--peek` does not work with a TID today.** `weft result TID --peek` is a
   usage error. Raised as Open Owner Question 2 with the "unchanged" default,
   so the acceptance cell is the existing `--all --peek` regression rather
   than a new capability smuggled in under "orthogonal".
5. **124 quietly changes meaning.** A task whose *own* timeout fired
   currently exits 124 from `weft result`; under "terminal + empty → 2" it
   becomes 2, and 124 comes to mean only "the caller's `--timeout` expired".
   That is a real user-visible change the contract implies but does not
   spell out — register row 4, with both exit codes pinned by tests.
6. **A failed task with output now exits 0.** Outbox-only means the row wins
   and the verdict moves to `weft task status`. Register row 3, and the
   CHANGELOG line was widened past the owner's wording to cover it.
7. **Blocking-by-default would hang on an unknown TID** if materialization
   also blocked. Today it exits 2 promptly. Pinned as an invariant: blocking
   applies to the *result surface*, not to proving the TID exists.
8. **The grace constant cannot be deleted.** The superseded plan flagged
   `events.py`; verified — three live sites there plus four in
   `_result_wait.py`. Stated as "no `_constants.py` change", so a literal
   implementer does not delete it and break the realtime surface.
9. **`_claimed_result_blockage` is spec-mandated** ([OBS.14], [CLI-1.2.2]
   `:493-496`) and is easy to drop while rewriting the entry point. Named as
   an explicit ordering step inside task 4 and pinned by an untouched test
   file.
10. **Line-number drift.** The brief cited
    `WEFT_COMPLETED_RESULT_GRACE_SECONDS` at `_constants.py:1898`; it is at
    `:1934` at `e158d982`. All line numbers in this plan were re-read at the
    baseline SHA.

## Review Record (append-only)

_(empty — task 1 has not run; this plan is draft / review-pending, not
implementation-ready.)_
