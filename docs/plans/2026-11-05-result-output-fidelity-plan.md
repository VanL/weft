# Result Output Fidelity: Spilled Output Survives Its Reference, Failed Commands Keep Their Stdout

Status: promoted
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-2.1], Large Output Handling, [MF-5] cleanup owners; docs/specifications/02-TaskSpec.md `cleanup_on_exit`, `output_size_limit_mb`; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2.2]; docs/specifications/14-Python_API_Surfaces.md [PY-2] (`outputs_root` in Phase 1; failure metadata in Phase 2)
Superseded by: none

Class: 5, risky. It changes the public result contract of `weft run` and
`weft result`, and the lifecycle of a persisted file (spilled output): who
deletes it and when. Hardening and Tier-1 cross-family review apply. Promotion
strategy B. This plan authorizes no implementation.

## 1. Goal

Two verified defects lose task output silently.

1. **Spilled output is deleted while its reference is still live.**
   - Output above `output_size_limit_mb` (default 10 MB) is written to a
     spill file, and the outbox carries a `large_output` reference to it.
   - `cleanup_on_exit` defaults to true for every task, and task exit deletes
     the spill directory. The reference therefore always dangles under
     defaults: `weft run` exits 0 and prints a reference to a missing file.
   - With cleanup off, nothing ever deletes the file.
2. **A failed command's stdout is discarded.**
   - The subprocess runner captures stdout on a non-zero exit, but the
     Consumer's error branch publishes only an error built from stderr.
   - With `stream_output` the same stdout does reach the outbox as frames, so
     the two modes disagree.

After this plan:
- a spill file is never deleted by Weft, so its reference stays valid
  wherever it is forwarded; retaining or removing spill files is the
  caller's responsibility (owner ruling G2);
- `weft run` and `weft result` deliver spilled content rather than a dangling
  path;
- a failed command's stdout is delivered with a non-zero exit;
- each spill gets its own file, so a persistent task's later spill cannot
  overwrite an earlier, unconsumed one.

The work lands in two phases of this one plan, each with its own spec delta,
review, fit test, owner rulings and commit:

- **Phase 1: spill lifecycle** (G2–G4). Covers §3 A, §3 B and the host and
  outputs-root rules. It fixes the worse defect: `weft run` exits 0 while
  its output is lost.
- **Phase 2: failed-command stdout** (G1, G5). Covers §3 C. It starts only
  after Phase 1 is committed, because a failed command's over-limit stdout
  (probe j) is delivered through Phase 1's delivery helper.

## 2. Source Documents and Verified Premise

Probes ran against a snapshot of the 2026-11-05 worktree, on SQLite, under
load average ~42:

- **20 MB output:** `weft run python -c 'print 20e6 bytes'` exited 0. It
  printed a 1,549-byte `large_output` JSON reference whose
  `.weft/outputs/<tid>/` directory no longer existed. 9 MB returns intact.
- **Failed command, plain run:** `weft run sh -c 'echo OUT; echo ERR >&2;
  exit 3'` printed only `Error executing task: Command exited with 3: ERR`
  (exit 1).
- **Failed command, background task and result:** `task status --json`
  showed `return_code: 4`, error `Command exited with 4: `. `weft result`
  printed the error only; `OUT2` was lost.

Code facts (verified):

- **Spill writer:** `weft/core/tasks/base.py::BaseTask._spill_large_output`
  writes `{outputs}/{tid}/output.dat`, a fixed name per TID. It records the
  directory in `_spilled_output_dirs` and emits an `output_spilled` event.
- **Spill deletion:**
  `weft/core/tasks/base.py::BaseTask._cleanup_spilled_outputs_if_needed`
  deletes those directories when `spec.cleanup_on_exit`. It is called from
  `weft/core/tasks/consumer.py::Consumer._cleanup_task_resources`, which also
  purges start tokens and stream markers.
- **Cleanup defaults:** `DEFAULT_CLEANUP_ON_EXIT` is True
  (`weft/_constants.py`). `weft/commands/run.py` and
  `weft/commands/submission.py` also hard-set it True.
- **No other spill deleter exists.** TaskMonitor's dead-task retention
  deletes `T{tid}.outbox` (`weft/core/monitor/task_monitor.py`, around the
  `standard_dead_task_retention_queue_names` use), but never the spill file.
- **No reader dereferences a spill.** Nothing outside `consumer.py`'s writer
  mentions `large_output`. `weft/cli/app.py::_render_task_result` prints the
  value as written.
- **Failed stdout:** `weft/core/runners/subprocess_runner.py` returns
  `RunnerOutcome(status="error", stdout=..., error="Command exited with N:
  <stderr>")`. The `outcome.status == "error"` branch in
  `Consumer` (around `mark_failed(..., return_code=outcome.returncode)`)
  writes no outbox row.
- **Result consumption:** `weft/commands/result.py::_read_outbox_task_result`
  consumes by exact `read_one(exact_timestamp=...)` unless `peek`.

Governing text:

- **[MF-2.1]** defines the reference envelope and the `{tid}/output.dat`
  path.
- **Large Output Handling** says the `output_spilled` event lets "the spill
  ... be inspected later". It also says "`weft result` does not currently
  auto-dereference large-output references", which is a statement of current
  behavior, not a prohibition.
- **[MF-5]** cleanup owners: "task-owned cleanup may remove spilled output
  when `cleanup_on_exit` is enabled".
- **02-TaskSpec** `cleanup_on_exit`: "Delete task-owned scratch artifacts on
  completion".
- **[MF-3] / [CLI-1.1.1]:** stderr stream frames are diagnostics, not result
  data.

Theory: `docs/program-theory.md`:
- [WT-4] "One owner for each resource". A spill currently has two
  conflicting owners: the exiting task deletes it while the outbox row still
  promises it.
- [WT-2a.2] Queues are the durable truth. A queue row that references a file
  makes that file part of the fact.
- [WT-4.1] consume-to-disappear.
- Owner acceptance rule: every retirable record keeps a named retirement rule.
  For spill files the owner names it explicitly (G2, 2026-11-05): the caller
  retires them. Weft never deletes a spill.

Guidance: `AGENTS.md`, `docs/agent-context/` read order,
`docs/agent-context/runbooks/hardening-plans.md`,
`docs/agent-context/runbooks/testing-patterns.md`,
`docs/agent-context/runbooks/adversarial-acceptance-probes.md` (CLI floors),
`skills/test-audit/SKILL.md`.

## 3. Design

### A. (Phase 1) A spill is result data, and Weft never deletes it

Owner ruling G2 (2026-11-05): no automatic deletion of a spill. Retaining or
removing spill files is the caller's responsibility, and the spec says so.

- **No deleter.** The task-exit deletion is removed:
  `BaseTask._cleanup_spilled_outputs_if_needed`, `_spilled_output_dirs`, and
  every constructor, reset and call site, including both Consumer cleanup
  paths.
  - `cleanup_on_exit` keeps its start-token and stream-marker effects and no
    longer affects spilled output.
  - No TaskMonitor, LivenessMonitor or reader deletes a spill. This adds no
    queue, timer, receipt or liveness probe.
  - The dead-task sweep keeps deleting `T{tid}.outbox` after retention, as
    today; the file outlives it.
- **Why no Weft owner can safely delete one** (recorded so this is not
  re-proposed without new facts):
  - An outbox cannot own the file: pipeline edges move references,
    `--json` and client readers copy them, and custom routes are externally
    owned.
  - No reader knows it holds the last reference.
  - Tasks without a `weft_context` share one temp root
    (`{tempdir}/weft/outputs/`), so a directory scan cannot attribute files
    to a context.
  - The alternative considered was receipt-backed retirement by TaskMonitor
    after proven producer death plus a retention window. It is rejected
    under G2 because of its cost: a queue family, a cleanup slice, host
    liveness probes and a restart-sensitive clock.
- **Guidance to document.** A task whose large output must be kept, shared
  or managed should write it as a side effect to a location its caller or
  reader knows: a file path it was given, a named artifact store, or its own
  output directory. It should then return a small result naming that
  location, rather than rely on the spill path. Spills remain the automatic
  fallback for unexpectedly large results.
- **Per-output names (G4).**
  - Each spill is `{outputs_root}/{tid}/{seq}.dat`, where `seq` is a
    per-process counter starting at 1.
  - Create the file with `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    0o666)`. `O_EXCL` makes creation exclusive, and mode `0o666` lets the
    process umask apply, as [CTX.5] Shared Access requires. Do not use
    `tempfile.mkstemp`: it forces mode `0600`.
  - On `FileExistsError`, increment `seq` and retry. Another process with
    the same TID, or an earlier run's leftover, cannot be overwritten.
  - A persistent task's later spill, an agent's second output, and repeated
    direct `run_work_item()` calls each get their own file.
  - Write the bytes, close the descriptor, then write the outbox reference.
    A failure before the reference is written leaves an unreferenced file.
    That is the same caller-owned residue as any spill, and no extra cleanup
    is attempted (no over-armoring).
- **Single-host scope (owner, 2026-11-05).** Weft does not support multi-host
  contexts. Where a PostgreSQL context is used from several hosts anyway, the
  spec documents that an over-limit result is available only on the host
  where the task ran.
  - The `large_output` reference gains a `hostname` field, the producer's
    `socket.gethostname()`.
  - That lets a reader on another host report the actual cause.
- **Migration boundary.** Old references (`output.dat`, no `hostname`) stay
  readable by their absolute path. No existing file is moved or deleted.

### B. (Phase 1) Text-mode CLI delivers the content

Mode matrix (round 1, finding 9):

| Mode | Large-output reference |
|---|---|
| `weft run` (waiting, text) | content delivered, if the shape, root and host rules above hold |
| `weft result TID` (text) | content delivered, if the shape, root and host rules above hold |
| `--json` on either; `weft result --all` (consume or `--peek`; `--peek` stays `--all`-only); pipelines; Python client | reference returned as written |

What counts as a reference (owner-accepted recommendation, 2026-11-05):

- `large_output` is a reserved transport envelope, like `stream` (and, from
  Phase 2, `failed_output`). Command stdout reaches the outbox as written, and the
  decoder parses JSON. Without this rule, any output shaped like a reference
  would be dereferenced, and the CLI would read whatever local path a task
  printed.
- The text-mode CLI evaluates a value in this order. Each step decides
  before the next runs, and no file is opened before step 4:
  1. **Shape.** The value is a JSON object with `type: "large_output"`, a
     string `path`, a nonnegative integer `size`, a 64-hex-character
     `sha256`, and, if present, a nonempty string `hostname`. Otherwise it is
     an ordinary value: print as written, exit 0.
  2. **Host.** If `hostname` is present and differs from
     `socket.gethostname()`, exit 1 with "output is only available on host
     <hostname> at <path>". The path is never resolved or opened. A legacy
     reference without `hostname` skips this step.
     - Host is checked before location because outputs roots are
       host-local: a real reference from another host never lies under the
       reader's roots, so a location-first order would print it as an
       ordinary value with exit 0.
     - A forged object with a foreign hostname gets this exit-1 error and no
       file read. That is the cost of `large_output` being reserved.
  3. **Location.** Resolve both the candidate `path` and each allowed root
     with `Path.resolve()` before comparing. The comparison is
     `resolved_path.is_relative_to(resolved_root)` with
     `resolved_path != resolved_root`, by path components. The allowed roots
     are the **reading** context's outputs root (`WeftContext.outputs_dir`,
     which honours `--context` and a configured directory name) and
     `Path(tempfile.gettempdir()) / "weft" / "outputs"`. Resolving the roots
     too handles macOS `/var` -> `/private/var` and any other symlinked
     project, context or temp root.
     - A path outside both roots is an ordinary value: print as written,
       exit 0, with no file opened.
  4. **Deliver** (below).
- **Context handoff.** The CLI does not hold the resolved context:
  - `weft/commands/result.py::cmd_result` resolves it with `build_context`;
  - a waiting run keeps it inside `_LiveRunSession`, and stored-spec
    execution may rebind it to another runtime root (`weft/commands/run.py`,
    `_resolve_submission_runtime_root`).

  So the command layer hands it over:
  - `TaskResult` and `RunExecutionResult` (`weft/commands/types.py`) gain
    optional `outputs_root: str | None = None`. The command layer sets it to
    the **actually used** context's resolved `outputs_dir`: the result
    command's resolved context, and the run's context after any stored-spec
    rebinding.
  - `_render_task_result` and `render_run_result` pass `outputs_root` to the
    delivery helper.
  - `weft result --stream` goes through an event, not a `TaskResult`:
    `weft/commands/result.py::_task_result_event` projects a fixed payload,
    and `weft/cli/app.py`'s stream loop rebuilds `TaskResult` from it.
    - Add `outputs_root` to that payload.
    - Pass it into the rebuilt `TaskResult`.
    - Every path that reaches text-mode rendering therefore carries it.
  - No renderer rebuilds a context from CLI arguments.
  - When `outputs_root` is `None` (a result not produced by these command
    paths), only the temp root is allowed.
- **Top-level lists.** Rendering cannot tell a list aggregated from several
  outbox rows (`weft/core/outbox.py::aggregate_public_outputs`) from a list
  one task returned; both are plain lists. The rule therefore applies to
  **any top-level list** with at least one element that passes step 1
  (shape). Nested values are never inspected. When the rule applies,
  text mode renders each element in order on its own line:
  - a reference element goes through steps 1–4;
  - any other element is rendered as a single value is today.

  A list with no shaped element keeps today's JSON-array rendering. The rule
  is a deliberate change: a task-returned list containing a shaped element
  is now printed element by element, not as one JSON array. Steps 2–3 still
  stop a shaped element that names a foreign host or an out-of-root path
  from being read. The CHANGELOG states this.
- Forwarding or copying preserves the reference, and the file is never
  deleted by Weft. It does not grant automatic delivery in another context. Automatic text-mode
  delivery requires the producer's path to lie within the reader's resolved
  allowed roots and to pass the preceding host check. A same-host (or legacy
  unscoped-host) reference outside those roots prints as ordinary output even
  while its file remains valid. A fully shaped foreign-host envelope instead
  reports the host error at step 2, regardless of location. JSON and client
  readers still receive the reference as written.

Delivery:

- Open `path` and stream the bytes in bounded chunks to stdout, verifying
  `size` and `sha256`. Then flush stdout.
- Fidelity is defined against the serialized result the producer encoded:
  the spilled bytes are exactly that UTF-8 encoding. Existing producer
  transformations (stdout whitespace stripping, `serialize_result`) are
  unchanged (round 1, finding 7).
- Any failure exits **1** with an error naming the path, never 0:
  - a missing file;
  - a size mismatch;
  - a digest mismatch;
  - a read error;
  - a failed write or flush, including a closed pipe.

  Bytes already written stay written, and the error says the output is
  incomplete.
- **Delivery is at most once per consuming read** (round 1, finding 8). The
  existing result path claims the outbox row before rendering. A delivery
  failure therefore leaves the row consumed but the file intact. The error
  names the path, so the user can still read it; Weft does not delete it.
  This trade-off is stated in the spec. The row-acknowledgement boundary is
  unchanged.
- Shared path: one capability-layer helper in `weft/commands/` used by both
  `run` and `result` rendering. `weft/cli/app.py` only calls it. `core/`
  does not import it.

### C. (Phase 2) Failed commands deliver captured stdout in a self-describing row

- **Why a plain row would be wrong** (round 1, finding 3): the result
  collector may settle an ordinary output row as `completed` after grace
  without terminal proof (`weft/commands/_result_wait.py`, the
  `_output_fallback` branch). A failed command's stdout written as a plain
  row could then be reported as success if failure publication stalls.
- **Design (recommended, owner decision G5).** When a command exits
  non-zero with non-empty captured stdout, and the task is not
  live-streaming, the Consumer writes one typed outbox row before its
  terminal failure publication:

  ```json
  {"type": "failed_output", "return_code": N, "error": "<error>", "result": <inline text or large_output reference>}
  ```

  - The `result` field goes through the same size check and spill path as
    success.
  - Shared outbox decoding recognizes the reserved `failed_output` envelope. The collector treats it as failure evidence: it
    settles `failed` with that error and return code, never `completed`,
    with or without terminal proof.
  - The existing `result_without_terminal` fallback for ordinary rows is
    unchanged.
  - Empty stdout writes no row.
- **Rendering:**
  - **`weft run` (text):** prints `result`, with the error on stderr; exit
    1.
  - **`weft result` (text):** the same.
  - **`--json` on both:** `{"tid", "status": "failed", "result", "error",
    "return_code"}`.
  - **`result --all`:** reports the item as failed, with its `result` as
    written.
- **One interpretation boundary, all projections.** Extend the existing
  `weft/core/outbox.py` decoder with typed failure metadata, preserving the
  distinction between transport metadata and the nested public result.
  `weft/commands/_result_wait.py` and
  `weft/commands/result.py::_read_outbox_task_result` consume that shared
  interpretation; the latter must stop hard-coding `completed`. Carry optional
  `return_code` through `weft/commands/types.py::TaskResult`, result event
  projection, CLI JSON and client adapters. Search every decoder caller in
  `weft/core/task_evidence.py`, `weft/commands/events.py`, streaming adapters,
  client and integrations. In particular,
  `weft/core/task_evidence.py::peek_final_outbox_evidence` must take status from
  decoded failure metadata instead of constructing `completed`; task-status
  queries and Monitor runtime classification use it even without the waiting
  collector. No renderer independently recognizes wire tags.
  Exact `type: failed_output` plus the declared fields is a reserved transport
  envelope, as `stream` already is; document this restriction and validate it
  at the decoder: require a nonzero integer return code, string error and
  present result; reject malformed reserved envelopes as protocol errors,
  never ordinary successful output. Ordinary values remain ordinary values.
  When one result inspection aggregates prior successful persistent outputs
  and a failed output, preserve public-value order and mark that inspection
  failed with the envelope's error and return code. Do not turn the wrapper
  itself into a list element. Add a decoder contract probe for valid and
  malformed reserved envelopes and a preservation case for ordinary structured output.
- **Pipeline failure is decided before handoff.**
  `PipelineEdgeTask._handle_work_message` must inspect the reserved payload
  through that same decoder before `_handoff_payload` or override-input
  handling. A `failed_output` envelope fails the edge using the existing
  `_fail_edge` path and its declared reserved policy; it is never moved into
  a downstream inbox or the pipeline's public outbox. Do not wait for the
  asynchronous stage-terminal event. Test with stage-terminal publication held
  so the payload necessarily arrives first. Success references still move
  unchanged, and edge override input cannot bypass the failure check.
- stderr stays diagnostic ([MF-3]).
- A live-streaming command already published its stdout frames and writes
  no second copy.
- Function targets and agents are unchanged.

### Invariants and constraints

- Reserved-queue policy, terminal envelopes, states and TaskSpec `io` are
  unchanged.
- No new queue, timer, daemon, liveness probe or dependency.
- Agent multi-output emission ([AR-4], [AR-4.1]) is unchanged. A failed
  command adds at most one `failed_output` row. An earlier draft's "at most
  one row per work item" constraint was wrong and is removed.
- `weft result` keeps its existing collection and acknowledgement semantics
  ([CLI-1.2.2]): block until a row or a terminal state, exit 2 for nothing.
  With terminal proof, one call collects every pending row up to the
  terminal boundary and aggregates them
  (`weft/commands/_result_wait.py`, the deliverable-row selector). Phase 1
  does not change that boundary.
- No over-armoring: a failed spill write is reported through the existing
  emission failure path. There is no publication retry and no residue
  cleanup. The only loop is G4's filename-collision advance, which moves to
  the next number.
- No compatibility shim. Each phase notes its own changes in the CHANGELOG.
  Phase 1:
  - `weft run` prints large output instead of a reference;
  - `large_output` is a reserved envelope, dereferenced only inside an
    outputs root, and it gains `hostname`;
  - over-limit results are available only on the producing host;
  - in text mode, a top-level list containing a reference-shaped element
    prints element by element instead of as one JSON array;
  - `TaskResult` and `RunExecutionResult` gain optional `outputs_root`;
  - spill files are named `{tid}/{seq}.dat`, one per output;
  - **spill files are no longer deleted at task exit, and Weft never deletes
    them**. Under the old default (`cleanup_on_exit=true`) they were deleted
    at exit, which was the bug. Callers own `outputs/` growth, and the
    CHANGELOG recommends the side-effect pattern for large outputs.

  Phase 2:
  - failed commands print their stdout;
  - the new `failed_output` row type exists.
- Windows: write the reference only after the file is closed.

### Hidden couplings

- Pipelines: a stage whose command fails writes a `failed_output` row to its
  outbox. The edge must reject it before handoff, regardless of whether
  the stage-terminal event has arrived. The row is never a successful payload.
  Probe (l2).
- TaskMonitor and LivenessMonitor are unchanged. Neither deletes spills.
- Django and other embedders read references through the client and are
  unchanged. They must tolerate a `failed_output` row, which the client
  surfaces as failure.

### Comprehension questions (answers in the execution log before editing)

1. Why does no reader delete a spill, even after delivering it?
   - *Expected:* a reference can be forwarded by pipeline edges, copied by
     JSON or client readers, or written to custom routes. No reader knows it
     holds the last reference. So Weft deletes no spill at all (G2); the
     caller owns retention.
2. Why can't failed-command stdout simply be appended to the error string?
   - *Expected:* it mixes result data into diagnostics, is unbounded, and
     breaks the stdout/stderr split that [MF-3] and the CLI rely on.

## 4. Owner Decisions

Phase 1's decisions (G2–G4, G6, G7) are all made. Phase 2 needs G1 and G5.


- **G1 (Phase 2) — deliver a failed command's stdout.**
  - *Recommended:* yes, as in §3 C.
  - *Alternative:* leave failures stdout-less and document it.
- **G2 (Phase 1) — spill retirement. Decided (owner, 2026-11-05): no
  automatic deletion.**
  - Weft never deletes a spill, and the caller owns retention.
  - The spec recommends recording large output as a side effect at a location
    the caller or reader knows.
  - *Rejected:*
    - Receipt-backed TaskMonitor retirement after proven producer death plus
      a retention window. Its cost (a queue family, a cleanup slice, host
      liveness probes and a restart-sensitive clock) is not worth it to the
      owner.
    - Tying the file to the producer's outbox, or delete-on-read. Both break
      forwarded and consumed references (review finding 1).
    - Reference counting across queues: no owner can count them.
- **G3 (Phase 1) — CLI dereference. Decided (owner, 2026-11-05): as
  recommended.**
  - Text-mode `run` and single-task `result` deliver
    verified content; every other mode keeps the reference (§3 B matrix).
  - *Alternative:* never dereference. That fixes only the missing file.
- **G4 (Phase 1) — spill file naming. Decided (owner, 2026-11-05):
  `{tid}/{seq}.dat`.**
  - A per-process counter, created with `O_EXCL`; on collision, the next
    number.
  - `{tid}.dat` was rejected: one task can spill more than once (persistent
    work items, agent multi-output, repeated direct calls), and with no
    deletion a reused name is the only way a reference's file is lost.
- **G5 (Phase 2) — failed-output row.**
  - *Recommended:* the typed `failed_output` row, which the collector
    settles as failed with or without terminal proof.
  - *Alternative:* write a plain row only after terminal publication. That
    still races the existing `result_without_terminal` fallback whenever
    terminal publication fails.

- **G6 (Phase 1) — host scope. Decided (owner, 2026-11-05).** Multi-host
  contexts are not supported. Document that an over-limit result is available
  only on the producing host. Add `hostname` to the reference, so text mode
  can report the actual cause after shape recognition and before location
  checking. Fully shaped foreign-host envelopes exit 1 regardless of path;
  same-host or legacy host-unscoped outside-root values remain ordinary under
  G7. This order is stated in §3 B and mirrored in probes (o)/(p).
- **G7 (Phase 1) — reference reservation. Owner-accepted recommendation,
  2026-11-05.** Dereference only a fully shaped `large_output` whose resolved
  path lies under an outputs root. Shape failures print as written. For a
  fully shaped envelope, apply G6's host check first; then a same-host or
  legacy host-unscoped outside-root value prints as written. No file is opened
  until all three checks pass.
- **Structure. Decided (owner, 2026-11-05).** One plan in two phases, rather
  than two plans.

## 5. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 6. Spec Baseline

- Initial draft: `963dc83e` plus the uncommitted spec state of 2026-11-05.
  The round-2 amendment was checked against `b7671da6`; no runtime or
  normative spec change is made by this plan-edit task.
- Implementation baselines, one per phase: record the commit SHA before each
  phase's spec promotion, and re-quote that phase's replaced spans exactly.
  Phase 2's baseline is the Phase 1 commit.
- Plan type: implementation with spec revision, strategy B. Promotion
  baseline identifier: pending.

## 7. Proposed Spec Delta

Promotion strategy B for every block, applied per phase. Each block is labelled
with its phase. Phase 1 blocks use the `Replacement (`<path>`):` label, which
`bin/check-doc-paths` checks for containment once the plan is promoted.
Phase 2 blocks use `Phase 2 text (`<path>`):`, which the gate ignores, so
promoting Phase 1 cannot fail on unapplied Phase 2 text. Phase 2 task 7
relabels them `Replacement` when Phase 2's promotion begins. Phase 1's blocks land with Phase 1's code; Phase 2's blocks land
with Phase 2's code, so the spec never describes an unbuilt phase. Re-quote the
replaced spans at each phase's implementation baseline.

### (Phase 1) Large Output Handling — replace the two bullets "`weft result` reads the outbox payload as written" and "`weft result` does not currently auto-dereference large-output references"

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- `large_output` is a reserved transport envelope. Text-mode readers evaluate a value in order: (1) shape: `type: "large_output"`, a string `path`, a nonnegative integer `size`, a 64-hex-character `sha256` and, if present, a nonempty string `hostname`; otherwise it prints as written; (2) host: a `hostname` other than the reader's host exits 1 naming that host and path, without opening the path; (3) location: the symlink-resolved `path` must lie under the symlink-resolved outputs root of the reading context or under the resolved `{tempdir}/weft/outputs/`, compared by path components; otherwise it prints as written; (4) delivery
- any top-level list result (whether one task's value or several aggregated outputs) with at least one element of reference shape is rendered by text mode element by element in order, each on its own line, with shaped elements evaluated under these rules; nested values are not inspected, and a list with no shaped element keeps its JSON rendering
- text-mode `weft run` and single-task text-mode `weft result` deliver the referenced bytes to stdout, verifying `size` and `sha256` and flushing stdout; a missing file, a mismatch, a read error or a failed write exits 1 with an error naming the path, never 0
- an over-limit result is available only on the host where the task ran. Weft does not support multi-host contexts; a PostgreSQL context used from several hosts gets no cross-host delivery
- forwarding or copying preserves the reference, and the file is never deleted by Weft, but it does not extend the reader's allowed roots. Automatic text delivery is promised only within those roots and after the host check. Same-host or legacy host-unscoped references outside those roots print as ordinary values; fully shaped foreign-host envelopes report the preceding host error regardless of location
- every other reader returns a large-output reference as written: `--json` on either command, `weft result --all` (including `--peek`, which remains `--all`-only), pipelines and the Python client
- delivery is at most once per consuming read: the result row is claimed before delivery, so a failed delivery leaves the row consumed and the spill file in place
- Weft never deletes spill files: not at task exit, not when a reader consumes the reference, and not by any monitor. Retaining or removing them is the caller's responsibility. A task whose large output must be kept or shared should write it as a side effect to a location its caller or reader knows and return a small result naming that location; spills are the automatic fallback for unexpectedly large results
```

### (Phase 1) [MF-2.1] — reference example: replace the JSON example block from `"type": "large_output",` through `"message": "Output too large (12345678 bytes); saved to /tmp/weft/outputs/{tid}/output.dat"`

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
  "type": "large_output",
  "path": "/tmp/weft/outputs/{tid}/{seq}.dat",
  "size": 12345678,
  "size_mb": 11.77,
  "truncated_preview": "<first 1024 bytes decoded as UTF-8 with replacement>",
  "sha256": "<hex-encoded SHA-256 of the raw encoded bytes>",
  "hostname": "<producer socket.gethostname()>",
  "message": "Output too large (12345678 bytes); saved to /tmp/weft/outputs/{tid}/{seq}.dat"
```

### (Phase 1) [MF-2.1] — field table: insert after the `sha256` row

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
| `hostname` | string | Producer host (`socket.gethostname()`); the file is readable only on that host. Absent in references written before this field existed |
```

### (Phase 1) [MF-2.1] — path rules: replace the two bullets from "- If `spec.weft_context` is set" through "(where `{tempdir}` is the platform temporary directory)."

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- If `spec.weft_context` is set, the spill directory is
  `{weft_context}/{WEFT_DIRECTORY_NAME}/outputs/{tid}/`, where
  `WEFT_DIRECTORY_NAME` defaults to `.weft`.
- Otherwise it falls back to `{tempdir}/weft/outputs/{tid}/`
  (where `{tempdir}` is the platform temporary directory).
- Each spill is a separate file `{seq}.dat` in that directory, where `seq`
  is a per-process counter starting at 1. The file is created exclusively,
  with the process umask applied; on collision the next number is used.
```

### (Phase 1) [MF-2.1] — insert after the paragraph ending "file integrity after dereferencing."

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
Spill files are never deleted by Weft. Task exit, `cleanup_on_exit`, result readers, pipelines and monitors leave them in place, so a reference stays valid wherever it is forwarded or copied. Retaining or removing spill files is the caller's responsibility. Multiple outputs, persistent work items and repeated direct calls each get their own `{seq}.dat` and cannot overwrite one another. The spilled bytes are exactly the UTF-8 encoding of the serialized result. The file is written and closed before the outbox reference is written; a failure in between leaves an unreferenced file, which is caller-owned like any spill. The reference's path is local to the producer's host, and the reference carries the producer's `hostname`. References written before `hostname` existed remain readable by path.
```

### (Phase 1) [MF-5] cleanup owners — replace "- task-owned cleanup may remove spilled output when `cleanup_on_exit` is enabled"

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- no owner removes spilled output: `cleanup_on_exit` does not affect it, and spill files are the caller's responsibility under [MF-2.1]
```

### (Phase 2) [MF-2] — insert after the bullet beginning "error, timeout, or external control applies the configured reserved"

Phase 2 text (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- a command that exits non-zero with non-empty captured stdout, and is not live-streaming, writes one `failed_output` row to its outbox before its terminal failure is published: `{"type": "failed_output", "return_code", "error", "result"}`, where `result` passes through the same size check and spill path as a successful result. A result reader settles a `failed_output` row as failed with that error and return code, with or without terminal proof; it never settles it as completed. Empty stdout writes no row. stderr remains diagnostic, and a live-streaming command writes no second copy of its stdout frames. `failed_output` is a reserved, validated transport envelope; shared outbox decoding carries its failure metadata separately from its nested result through waiting collectors, all-results inspection, events, CLI and client adapters. `TaskResult` gains optional `return_code`. Pipeline edges inspect the envelope before any handoff or override-input processing and fail through their existing failure path instead of forwarding it. This rule does not wait for a stage-terminal event.
```

### (Phase 1) 02-TaskSpec `cleanup_on_exit` — replace the comment text "Delete task-owned scratch artifacts on completion."

Replacement (`docs/specifications/02-TaskSpec.md`):

```text
Delete task-owned scratch artifacts on completion; this flag does not affect spilled output, which follows [MF-2.1].
```

### (Phase 1) [CLI-1.2.2] — insert after "- `--json` includes metadata"

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
- in text mode a large-output reference that satisfies the reservation rules of Large Output Handling is delivered as its verified content; `--json` and `--all` return it as written ([MF-2.1])
```

### (Phase 2) [CLI-1.2.2] — insert after the Phase 1 bullet above

Phase 2 text (`docs/specifications/10-CLI_Interface.md`):

```text
- a `failed_output` row prints its `result` to stdout and its error to stderr and exits 1; with `--json` it returns `{"tid", "status": "failed", "result", "error", "return_code"}`
```

### (Phase 1) [CLI-1.1.1] — insert after the paragraph beginning "Private worker-result protocol failures are rendered in user terms."

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
A waiting `weft run` renders results under the same rules as single-task `weft result` ([CLI-1.2.2]): text mode delivers verified large-output content under the reservation and host rules of Large Output Handling; `--json` returns the reference as written.
```

### (Phase 2) [CLI-1.1.1] — append to the Phase 1 paragraph above

Phase 2 text (`docs/specifications/10-CLI_Interface.md`):

```text
For a failed command, text mode prints its captured stdout before exiting 1 with the error on stderr, and `--json` returns `{"tid", "status": "failed", "result", "error", "return_code"}`.
```

Update nearby implementation mappings in 02 (`spec.cleanup_on_exit`), 05
(Large Output Handling) and 10 ([CLI-1.1.1], [CLI-1.2.2]), and the touched
docstrings, in the same change.

### (Phase 1) No queue inventory or cleanup-owner insert

G2 adds no queue and no deleter, so `00-Quick_Reference.md`, [OBS.13.9] and
[LIVENESS.R1]–[LIVENESS.R3] are unchanged.

### (Phase 1) Python result shape — [PY-2] `outputs_root`

In `docs/specifications/14-Python_API_Surfaces.md`, insert a new bullet after
the `TaskResult` bullet that introduces `reconciliation`, which ends with its
"Implementation plan:" line:

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```text
- `TaskResult` and `RunExecutionResult` carry optional `outputs_root: str | None = None`: the resolved outputs root of the context the command layer actually used for that read or run, including after stored-spec runtime-root rebinding. Text-mode CLI delivery uses it, with the temporary root, as the allowed roots under [MF-2.1]; Python callers receive large-output references as written and may ignore it.
```

### (Phase 2) Python result shape — [PY-2] public result types

In `docs/specifications/14-Python_API_Surfaces.md`, extend the public
`TaskResult` contract beside its optional `reconciliation` field:

Phase 2 text (`docs/specifications/14-Python_API_Surfaces.md`):

```text
`TaskResult` additionally carries optional `return_code: int | None = None`. A decoded `failed_output` supplies its nonzero command return code, nested public value and error, and reports failed even without a terminal envelope. Existing result fields, reconciliation semantics and ordinary output decoding remain unchanged. Result-event projections preserve the same failure metadata; they do not expose the transport wrapper as the public value.
```

## 8. Tasks

Both phases use the same probe rules: `WeftTestHarness`, real processes, and
SQLite plus PostgreSQL (`bin/pytest-pg`). No broker or filesystem mocks. RED
cases fail today for the named reason. Preservation controls pass today and
still pass after the change, each with a positive oracle.

### Phase 1 — spill lifecycle (G2–G4, G6, G7)

1. **Review before code.**
   - Run cross-family review of Phase 1 and its Phase 1 delta blocks.
   - Run the [WT-7] two-factor fit test over the Phase 1 blocks only.
   - All Phase 1 decisions are made: G2–G4, G6, G7 and the structure.
   - Format addition: the `hostname` field in `large_output`, and
     `{tid}/{seq}.dat` names. No new queue.
2. **Probes on the real path.**
   - **RED cases:**
     - (a) A 20 MB `weft run`: stdout equals the serialized result's bytes,
       and exit is 0. RED: a reference to a missing file.
     - (b) A `--no-wait` 20 MB task: its spill exists after the task exits,
       and `weft result` delivers it. RED: the file is deleted at exit.
     - (c) Missing file: delete the spill by hand; `weft result` exits 1 and
       names the path.
     - (d1) Isolated size check: keep a real spill's bytes and SHA-256
       unchanged, but change only the outbox reference's declared `size` to
       a different nonnegative integer. Text-mode result exits 1 and names the
       path. Independently verify that the declared digest still matches the
       actual file, so digest verification cannot explain rejection.
       - Sensitivity: removing only size validation must make this case
         deliver successfully with exit 0, failing the expected exit-1 oracle.
     - (d3) Truncation: truncate a real spill without changing its reference.
       Exit 1. This proves corrupt-file rejection, not independent size-check
       sensitivity, because truncation also changes the digest.
     - (d2) Digest check: corrupt one byte without changing the length.
       Exit 1.
       - Sensitivity: skipping SHA-256 must fail this case, and (d1) cannot
         cover it.
     - (f1) A persistent task with two 12 MB work items: each reference
       delivers its own content. RED: the second overwrites the first.
     - (f2) An agent emitting two over-limit outputs in one work item: two
       files, each correct. A waiting `weft run` receives both as one
       aggregated list and prints each output's bytes in order on its own
       line. A single `weft result TID` after completion collects both rows
       in one call and renders them the same way; a second call exits 2.
     - (f4) Collision and naming:
       - pre-create `{tid}/1.dat` with known bytes, then spill: the new file
         is `2.dat`, and `1.dat` is byte-identical;
       - run two processes with the same TID spilling concurrently: both
         get distinct numeric names, and each file holds its own bytes.

       Assert every name matches `^[1-9][0-9]*\.dat$`.
       - Sensitivity: a sequential writer without `O_EXCL`, or UUID naming,
         must fail this case.
     - (f3) Reuse one Consumer for two direct `run_work_item()` calls with
       over-limit output. Both files remain independently correct.
     - (g1) Weft never deletes a spill:
       - run a `--no-wait` 20 MB task to completion;
       - consume its reference with `weft result --json`;
       - let the dead-task sweep retire its queues (cross the configured
         retention through the existing narrow clock seam; do not sleep);
       - restart both monitors.

       The spill file is still present and byte-identical. Check both the
       context root and the tempdir root.
       - RED today: the file is deleted at task exit.
       - Sensitivity: restoring the task-exit deletion must fail this case.
     - (g2) `cleanup_on_exit=false` and `true` leave spill files equally
       present: the flag no longer affects spills.
     - (n) A closed stdout pipe during delivery (`weft result TID | head -c
       1`): exit 1, a reported incomplete-output error, and the spill
       retained.
     - (o) Cross-host reference: rewrite a real reference's `hostname` to
       another value while keeping its path inside an allowed resolved root.
       Text-mode `weft result` exits 1 with an error naming that host and path,
       not "missing file". A legacy reference without
       `hostname` skips the check.
     - (p) Reservation: a task whose stdout is a fully shaped `large_output`
       object pointing at a real file outside both outputs roots prints that
       JSON as written and exits 0. The same object pointing through a
       symlink from inside the outputs root to a file outside it also prints
       as written for a local hostname or a legacy missing hostname. Repeat
       both outside-root cases with a foreign hostname: they exit 1 naming
       host/path and never read the file, because host precedes location.
       Together with (o), these cases fix the precedence at the intersection.
       - Sensitivity: dropping containment must fail the same-host case;
         moving the host check after location must fail the foreign-host case.
     - (p2) Shape rejection: objects with `type: "large_output"` but a
       malformed field print as written, exit 0, and open no file. Cases:
       - `size` true, -1 or "5";
       - `sha256` of 63 characters, or with non-hex characters;
       - `hostname` "" or 123;
       - `path` missing or not a string.
       - Sensitivity: removing shape validation must fail these cases.
     - (q) Symlinked roots and `--context`:
       - Deliver from a context root reached through a symlink, and on macOS
         from the default temp root (`/var` -> `/private/var`). Delivery
         succeeds.
         - Sensitivity: resolving only the candidate path, not the roots,
           must fail this case.
       - Run `weft result TID --context <root>` from a different working
         directory. Delivery succeeds, which proves the reading context is
         threaded to the helper.
       - Run a stored spec whose runtime root differs from the submission
         context (stored-spec rebinding), with a configured
         `WEFT_DIRECTORY_NAME`. A waiting `weft run` delivers.
         - Sensitivity: deriving the root from the CLI `--context` instead of
           the rebound context must fail this case.
       - Run `weft result TID --stream --context <root>` from a different
         working directory for a context-local spill. Delivery succeeds.
         - Sensitivity: dropping `outputs_root` from the result-event
           payload must fail this case (temp-root only would print the
           reference, exit 0).
   - **Preservation controls:**
     - (s) Shared-access file mode (preservation and mutation control: it
       passes today, because `Path.write_bytes` honours the umask): under
       umask `002`, a spill file is created group-readable.
       - Sensitivity: creating it with `tempfile.mkstemp` (mode `0600`) must
         fail this case.
     - (e) With `--json` and with `--all --peek`, the exact reference is
       returned and the file is unchanged (byte-compare).
     - (l1) Preservation, with `cleanup_on_exit=False` so the baseline
       survives today: a pipeline stage that spills, and the downstream stage
       receives the identical reference row.
     - (l1b) RED, new behaviour: under the default flags the file still
       exists after the producer exits.
       - A reader whose allowed roots include the path delivers it.
       - Copy the reference to a second same-host context whose roots
         exclude the path: text mode prints the reference as ordinary output,
         exit 0, and the original file remains intact.

       This distinguishes retained validity from the reader's
       automatic-delivery permission.
     - (m) `cleanup_on_exit` still removes start tokens and stream markers:
       assert the rows are absent after exit, where a control run with
       cleanup off leaves them.
3. **Spill lifecycle.**
   - Files: `weft/core/tasks/base.py`, `weft/core/tasks/consumer.py`.
   - Remove the task-exit deletion: `_spilled_output_dirs`,
     `_cleanup_spilled_outputs_if_needed`, and every constructor, reset and
     call-site remnant, including both Consumer cleanup paths.
     `cleanup_on_exit` keeps only its start-token and stream-marker effects.
   - In `_spill_large_output`:
     - write `{outputs_root}/{tid}/{seq}.dat` with a per-process counter,
       using `os.open(..., O_WRONLY | O_CREAT | O_EXCL, 0o666)`, and
       increment on `FileExistsError`; do not use `tempfile.mkstemp`;
     - close the file before the outbox reference is written;
     - add `hostname` (`socket.gethostname()`) to the reference.
   - Add no queue, receipt, cleanup helper, publication retry or cleanup
     retry. Filename collisions advance the counter (G4); that is the only
     loop. No registry service or
     artifact framework.
4. **CLI delivery.**
   - Files: one helper in `weft/commands/`, plus rendering in
     `weft/cli/app.py` (`_render_task_result`) and the `run` wait path
     (`weft/cli/run.py`).
   - Readers never delete spills.
   - The helper owns the shape, host and outputs-root checks, in §3 B
     order, with both sides resolved. No renderer checks them separately.
   - It also owns per-element rendering of aggregated lists.
   - It takes `outputs_root`, never a context rebuilt from CLI arguments.
   - Also touch `weft/commands/types.py` (add `outputs_root` to `TaskResult`
     and `RunExecutionResult`), `weft/commands/result.py` (set it from
     `cmd_result`'s resolved context) and `weft/commands/run.py` (set it from
     the run's context after stored-spec rebinding), plus
     `_task_result_event`'s payload and the CLI stream loop's `TaskResult`
     rebuild.
   - Pass it through `weft/cli/app.py::_render_task_result` and
     `weft/cli/run.py::render_run_result`.
5. **Document caller-owned retention.**
   - Files:
     - `docs/specifications/05-Message_Flow_and_State.md`, through the §7
       blocks;
     - `README.md`'s large-output guidance;
     - `CHANGELOG.md`.
   - State plainly that Weft never deletes spill files and that retaining or
     removing them is the caller's responsibility.
   - Recommend that a task whose large output must be kept or shared write
     it as a side effect to a location the caller or reader knows, and
     return a small result naming that location.
   - The CHANGELOG states that spills were previously deleted at exit under
     the default and now never are.
   - TaskMonitor, LivenessMonitor and the dead-task policy are unchanged.
6. **Promote and commit Phase 1.**
   - Apply the Phase 1 blocks of §7; update mappings and the CHANGELOG.
   - Run the §9 gates.
   - Run completed-work cross-family review and the fit test on Phase 1's
     final diff.
   - Commit Phase 1. Set plan and index status to `promoted`, and record
     the Phase 1 commit SHA in the execution log.
     - `bin/check-doc-paths` then checks every `Replacement` block. Only
       Phase 1 blocks carry that label, so the gate passes once they are
       applied.

### Phase 2 — failed-command stdout (G1, G5)

Start only after the Phase 1 commit exists (`git log`).

7. **Review before code.**
   - Relabel every `Phase 2 text (`<path>`):` block as `Replacement (`<path>`):`
     only when Phase 2's promotion begins, together with the spec edits.
     Until then the gate must keep passing.
   - Run cross-family review of Phase 2 and its Phase 2 delta blocks, re-quoted
     against the Phase 1 commit.
   - Run the [WT-7] fit test over the Phase 2 blocks.
   - Record rulings G1 and G5. Format addition: `failed_output` outbox rows.
8. **Probes on the real path.**
   - **RED cases:**
     - (h) `weft run sh -c 'echo OUT; exit 3'`: stdout `OUT`, the error on
       stderr, exit 1. RED: no `OUT`.
     - (i) `weft result` of a failed `--no-wait` command prints `OUT2` and
       exits 1. With `--json`: `status: failed`, `result`, `error`,
       `return_code`.
     - (i2) Delayed terminal: hold terminal failure publication with the
       existing narrow test adapter so the `failed_output` row is visible
       first, and let the grace expire. The result settles failed with exit
       1, never completed. Also assert `weft task status --json`, final
       outbox evidence and result-event projection report failure while the
       terminal publication remains held.
       - Sensitivity: treating the row as ordinary output must fail this
         case.
     - (j) A failed command with more than 10 MB of stdout: the row's
       `result` is a reference, delivered and verified.
   - **Preservation controls:**
     - (k) A live-streaming failed command: after producer closure, the
       stdout frames appear exactly once and no `failed_output` row is
       written.
     - (l2) A pipeline stage whose command fails: the `failed_output` row is
       not forwarded as a successful payload. Hold the stage-terminal event
       until the edge has inspected the row; prove the downstream inbox remains
       empty and the edge fails. Repeat with override input configured.
     - (l3) Both `result --all` and `--all --peek` report typed failure with
       nested result and return code; consume/peek retain their exact effects.
       Client and result-event adapters retain the same metadata. Include a
       persistent task with an earlier successful output and a later failed
       command so aggregation cannot erase failure metadata.
9. **Failed stdout.**
   - Files: `weft/core/tasks/consumer.py`, plus the collector in
     `weft/commands/_result_wait.py` for `failed_output` settlement.
   - Reuse the success size/spill helper for `result`. Update shared decoding
     in `weft/core/outbox.py`, `weft/commands/result.py`, public result types and
     every decoder/event/client caller found by repository-wide search.
   - Update `weft/core/tasks/pipeline.py` to reject the failure envelope before
     handoff, including override input; preserve existing failure disposition.
10. **Promote and close.**
    - Apply the Phase 2 blocks of §7; update mappings and the CHANGELOG.
    - Run the §9 gates.
    - Run completed-work cross-family review and the fit test on Phase 2's
      final diff.
    - Commit, and flip the plan and index row to `completed`.

## 9. Verification and Gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks tests/cli/test_cli_result.py tests/cli/test_cli_run.py -q
./bin/pytest-pg tests/tasks tests/cli/test_cli_result.py tests/cli/test_cli_run.py
./.venv/bin/python -m pytest tests
./.venv/bin/python -m pytest -m "" tests
./bin/pytest-pg --all tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
bin/check-doc-paths
git diff --check
```

Run these gates at the end of each phase. Run the focused suites after each
task. Every `weft run` in a probe uses `</dev/null`. Apply the CLI floors in
`adversarial-acceptance-probes.md` to the changed output paths: a closed
stdout pipe (probe n), and a context path containing spaces. No non-UTF-8
spill probe is needed: the writer only produces UTF-8 encoded serialized
results.

## 10. Rollout and Rollback

- Rollback is a code revert.
- Spill files written under the new naming remain readable through their
  reference paths, except where old code spills again. Old cleanup removes
  the whole `{tid}` directory with `shutil.rmtree` when a reverted producer
  spills under the same TID and exits. That deletes numbered files from the
  same TID: in practice a persistent task, or a TID relaunched, that spans
  the revert.
  - Constraint: before reverting Phase 1, stop persistent producers whose
    spills must be kept, or copy those spills out.
- Each phase commits and reverts on its own. Phase 2 reverts without
  touching Phase 1.
- Wire additions:
  - Phase 1 adds the `hostname` field, which old readers ignore.
  - Phase 2 adds the reserved `failed_output` envelope, which needs
    coordinated producer/consumer rollout. TaskSpec is
  unchanged. Old readers can misreport failed envelopes as success; do not
  mix upgraded producers with old readers or pipeline edges.
- Reverting Phase 1 restores task-exit deletion. It cannot reach directories
  of TIDs that never run again under old code; same-TID directories follow
  the constraint above.
- Scripts that parsed the old reference from text-mode `weft run` output
  must switch to `--json`. The CHANGELOG calls this out.

## 11. Out of Scope

- Output from function targets and agent failures.
- Streaming large outputs through the outbox in chunks.
- A Python-client dereference helper.
- `--timeout` validation and the `--pipeline` exit code, which are separate
  CLI edge cases.

## 12. Fresh-Eyes Review

Author pass after drafting:

- **Found the overwrite defect.** The fixed `output.dat` name makes
  persistent spills unsafe once spills outlive the task, so per-output naming
  was added.
- **Limited CLI dereference to text mode**, so JSON consumers and pipelines
  keep a stable reference.
- **Retirement design history.** Earlier revisions retired spills, first
  by TID age and then by receipt-backed TaskMonitor retirement. Owner ruling
  G2 (2026-11-05) replaced both with no automatic deletion.

Current tradeoffs:
- `outputs/` grows until the caller removes spill files, and the docs say
  so;
- references are not delivered across hosts;
- Phase 2's wire-format rollout must be coordinated.

No implementation is authorized yet.

## Review Log

Authorship by revision:

- The initial draft and the round-1 revision were written by Anthropic
  (Claude).
- The round-2 receipt revision was written by OpenAI (Codex). The later
  Anthropic review of it is therefore a valid cross-family Tier-1 pass.
- The 2026-11-05 phase restructure was written by Anthropic (Claude), and its
  review is pending (Phase 1 task 1).

### Round 1 — Tier-1 cross-family

Historical dispositions below describe that revision. Round 2 replaces its
TID-age retirement and work-key naming choices; §§3 and 7 are operative.

- Author family: Anthropic (Claude). Reviewer family: OpenAI.
- Invocation: `codex exec -s read-only -C <repo> -c 'model_reasoning_effort="high"'`
  (codex-cli 0.161.0), using the runbook prompt.
- Reviewer read the plan and §7, the named code paths, theory and
  invariants.
- Verdict: **BLOCK**. Every finding was checked against the source before its
  disposition.

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 1 | P1: producer-outbox retention cannot bound a forwarded reference. Edges move `large_output` rows unchanged; custom routes are externally owned. RECONSIDERATION REQUESTED if producer retention overrides external ownership | Yes (`pipeline.py` exact `move_one`; `pipelines.py` exit edge; 07 OBS.13.9) | Fixed in plan: age-based retirement independent of any queue; readers never delete. Proposed as G2. External ownership of routes is not overridden: the file is retired by TID age, which the spec now states as the reference's validity bound |
| 2 | P1: a reservation timestamp is not unique per output (agents emit several; direct execution has none). RECONSIDERATION REQUESTED for the "one row per work item" constraint | Yes (`Consumer._emit_result` loops over `AgentExecutionResult.outputs`) | Fixed in plan: `{work_key}-{seq}` with exclusive create, and `direct` as the work key. The wrong constraint is removed |
| 3 | P1: a plain failed-stdout row can be settled `completed` by the existing no-terminal fallback | Yes (`_result_wait.py` `_output_fallback` branch) | Fixed in plan: a typed `failed_output` row settles as failed with or without terminal proof; the existing fallback is untouched; probe (i2). Proposed as G5 |
| 4 | P2: the delta omitted the failed JSON contract and was inconsistent about empty stdout | Yes | Fixed in plan: exact JSON for `run` and `result`; empty stdout writes no row |
| 5 | P2: retention must work after the outbox is already consumed; no tempdir test | Yes (cleanup intersects with existing queues) | Fixed in plan: deletion by TID age, independent of the queue; probes (g1) and (g2) |
| 6 | P2: truncation cannot prove the digest check | Yes | Fixed in plan: (d1) size and (d2) same-length corruption; preservation controls labelled with positive oracles |
| 7 | P2: byte fidelity is stated beyond the producer contract (whitespace strip, UTF-8 serialization); the non-UTF-8 probe can't be built | Yes (`subprocess_runner.py` `stdout.strip()`; `_emit_single_output` encodes UTF-8) | Fixed in plan: fidelity is defined against the serialized result; the non-UTF-8 probe is removed |
| 8 | P2: the row is claimed before delivery, so failure-recovery and flush semantics were undefined | Yes (`_result_wait.py` claims before the `TaskResult` returns) | Fixed in plan: stated at-most-once delivery per consuming read, file retained, flush included, closed-pipe probe (n) |
| 9 | P2: §7 contradicted itself on `--peek` | Yes (peek requires `--all`) | Fixed in plan: one mode matrix in §3 B, mirrored in §7 |

At round 1 no second round was run. The later owner-requested review and
revision below supersede that stopping point. G1–G5 remain the recorded
pre-implementation choices. The pre-code [WT-7] fit test runs after the
owner rules on those choices.

### Round 2 — owner-requested revision of findings

OpenAI review against the live result, pipeline and cleanup paths. These are
operative changes in §§3, 7–10, not review-log-only resolutions.

| Finding | Disposition |
|---------|-------------|
| P1 discovery/recovery after queues disappear | Accept: durable per-task receipt queue, exact paths/scoped identities, conservative process-local age, bounded independent discovery and restart probes |
| P1 pipeline failure ordering | Accept: shared decode before edge handoff/override, fail through existing edge path, delayed-event probe |
| P2 omitted result consumers | Accept: shared decoder and all-results/event/client/public-type inventory; explicit optional return code |
| P2 repeated direct-name collision | Accept: generated exclusive names and repeated-direct-call probe |
| P2 ambiguous publication | Accept: receipt-before-content/publication; preserve ambiguous outcomes and fault-inject lost reply |
| Contradictory format/owner/rollback text | Accept: G1–G5, named wire additions, coordinated rollout and honest rollback behavior |

The receipt queue replaces the earlier no-new-queue constraint because a
consumed outbox cannot provide durable discovery. The finite retention promise
for forwarded references remains an explicit G2 owner choice. This revision
is a proposed design amendment, not an assertion of owner approval.

### Revision review — Anthropic independent pass

Reviewer: Claude CLI, read-only `--permission-mode plan --allowedTools
"Read,Grep,Glob"`, against the revised operative plan and live named source.
An initial in-flight invocation was stopped when the author replaced persisted
death anchors with ephemeral clocks to preserve [LIVENESS.R1]; it produced no
verdict and is not counted as a review. The completed independent pass returned
BLOCKED on host scope, with the following exact finding summaries:

> **P1-1 (§3 A receipt schema; §7 queue inventory): receipts have no host scope.**

> **P2-1 (§3 C, shared decoding): the plan misses one consumer.**

> **P2-2 (§3 A retirement): two rules can be removed.**

> **P2-3 (§3 A publication order): what happens after an ambiguous receipt write is undefined.**

> **P2-4 (§3 A vs §7 [MF-5] and 02-TaskSpec): `cleanup_on_exit` keeps a spill role it no longer has.**

| ID | Verified cause and disposition applied to operative body |
|----|----------------------------------------------------------|
| P1-1 | Accept: PID absence on another host cannot prove death. Receipt hostname must match local host before probing or treating file absence as retirement. Foreign-host probe added. |
| P2-1 | Accept: `peek_final_outbox_evidence` separately hard-codes completed. Explicitly update it and cover task status and event projections while failure publication is held. |
| P2-2 | Modify: remove TID-wide identity conflict and extra Manager custody gates. Retire each receipt by its own scoped producer; this grants no task-state, reserved-row or launch-custody authority. Reviewer cited hypothetical same-TID relaunch; this plan authorizes no such relaunch. |
| P2-3 | Modify: an ambiguous receipt write stops emission before any bytes/reference. Do not adopt the suggested unbounded retry. A committed receipt may later remove the empty file; without one it is the documented empty-scratch orphan. |
| P2-4 | Accept: remove obsolete spill tracking, cleanup method and every call/reset site. `cleanup_on_exit` has no spill role; emission-error descriptor/scratch cleanup is unconditional. |

The review confirmed receipt queues are justified, pipeline pre-handoff
rejection is sound, and the planned real-path probes are falsifiable. Its
out-of-scope observation about an existing host-scope gap in LivenessMonitor's
runtime analysis is not changed here. The stale §12 clock wording was corrected,
and persistent-producer retention is now an explicit CHANGELOG item.

Scoped verification below is limited to these accepted finding IDs. No new
implementation or spec promotion is part of this plan revision.

Scoped independent verification returned:

> **Verdict: PASS** on all five accepted findings: P1-1, P2-1, P2-2, P2-3, P2-4. No RECONSIDERATION REQUESTED.

The reviewer checked the hostname convention and live error-propagation path.
Its remaining observation (agent multi-output emission may have already
published earlier outputs when a later write fails) is pre-existing and is not
changed or made atomic by this plan.

### Phase restructure — 2026-11-05, Anthropic (Claude)

Applied from a fresh read of the plan against the code:

- **Single-host scope (G6).** Recorded as decided. The `large_output`
  reference gains `hostname`, and a cross-host text-mode read reports host
  and path. The spec documents availability only on the producing host, and
  no retirement on hosts without the TaskMonitor.
- **Reservation of `large_output` (G7).** Text mode dereferences only a
  fully shaped reference whose resolved path lies under an outputs root;
  anything else prints as written. New probe (p), with a symlink case.
- **Two phases in one plan.** Phase 1 is the spill lifecycle; Phase 2 is
  failed stdout, started after the Phase 1 commit. §7 blocks, §8 tasks and
  probes, the CHANGELOG and rollout are split by phase.
- **Review-log author families** are now stated per revision.

Not yet reviewed. Phase 1 task 1 reviews the Phase 1 portion before code.

### Fresh-read clarification — three accepted findings

Owner requested these corrections after the phase restructure was re-read.
They clarify G6/G7 without changing the accepted root restriction or adding
runtime machinery.

| Finding | Disposition in operative design, spec delta and probes |
|---------|--------------------------------------------------------|
| Host/root precedence | Accept: preserve the current §3 B order, shape then host then resolved location then delivery. Fully shaped foreign-host envelopes exit 1 even outside allowed roots; same-host or legacy host-unscoped outside-root values remain ordinary. G6/G7, §7 and probes (o)/(p) state the same precedence. |
| Forwarding promise too broad | Accept: retention follows the producer's receipt; automatic delivery depends on the reader's allowed roots and host. Probe (l1) includes both an allowed reader and a second same-host context that excludes the producer path. |
| Size-check sensitivity masked by digest | Accept: (d1) changes only declared size while retaining matching bytes/digest. Removing size validation must produce exit 0. Separate (d3) retains truncation coverage without claiming independent size sensitivity; (d2) still isolates digest verification. |

Self-review checked the same shape/host/location ordering in §3 B, G6/G7,
§7 and probes (o)/(p),
and checked that neither isolated integrity probe can be satisfied by the
other check. No implementation or normative spec changed.

### Round 3 — Google family (agy/Argon), Phase 1 priority

- Author of the reviewed revision: Anthropic (Claude) restructure, with the
  OpenAI receipt design beneath it. Reviewer family: Google.
- Invocation: `agy --mode plan --sandbox --model gemini-4-argon-medium
  --output-format json --print-timeout 900s` (agy 1.5.1), with the
  call-agent native-read brief.
- agy 1.5.1 was re-probed first, because the skill was last verified on
  1.5.0:
  - liveness replied `PROBE-OK`;
  - the write probe created no file (plan mode wrote under
    `~/.gemini/antigravity-cli/brain/`), and `git status` was unchanged
    before and after.
- Result: exit 0, `SUCCESS`, a nonempty response and no `denied_actions`.
  Native reads only; no commands run.
- Verdict: **BLOCK**. Every finding was checked against the source:

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 1 | P1: promoting Phase 1 fails `bin/check-doc-paths` on unapplied Phase 2 `Replacement` blocks; [MF-2.1] was edited by prose instruction and omitted `hostname` from its field table; `00-Overview` has no queue inventory | Yes (`_is_plan_promoted` / `_plan_replacement_block_failures` check every block; no `weft.state` inventory in `00-Overview`) | Fixed in plan: Phase 2 blocks labelled `Phase 2 text`, relabelled at Phase 2 promotion (task 7); exact [MF-2.1] example, field-row and path-rule blocks; the inventory row targets the `00-Quick_Reference` Global queues table; companion rules are exact `Replacement` blocks |
| 2 | P1: only the candidate path is resolved, so containment fails on symlinked roots (macOS `/var`); renderers lack the reading context | Yes (`_outputs_base_dir` uses `tempfile.gettempdir()` unresolved; renderers take no context) | Fixed in plan: both sides resolved; the helper takes the reading `WeftContext`, threaded from the commands; probe (q) |
| 3 | P1: TaskMonitor retirement lane, slice order, clock seam, malformed receipts and idle accounting underspecified | Yes (the runtime-cleanup lane requires MonitorStore; `"dead_tid"` final-slice handling; hourly maintenance interval). Its "5-minute observation retention" claim is wrong: the default is 48 h | Fixed in plan: `spill_receipts` final slice in the runtime-cleanup lane with its own readiness; `_monitor_monotonic()`; malformed or out-of-root receipts acknowledged with a warning, never unlinked; age-deferred receipts outside idle accounting; probe (r) |
| 4 | P1: G2–G4 undecided while Task 1 says to record them | Partly | Not a plan defect: Task 1 records the owner's rulings. G2–G4 remain open owner decisions before Phase 1 code |
| 5 | P2: host and location rules contradict each other for a real cross-host reference | Yes | Fixed in plan: shape, then host, then location, then delivery. A concurrent owner-requested clarification made the same correction to probes (o)/(p) independently |
| 6 | P2: per-spill identity registration would violate [OBS.6a]; the receipt queue should be a [QUEUE.7] support route | Yes (`_task_pid` and `_task_pid_create_time` are captured at construction; `_reactor_support_routes`) | Fixed in plan: check the construction-time identity without re-registering; add the route to `_reactor_support_routes()` |
| 7 | P2: Phase 1 text mentions `failed_output`; a multi-output list of references fails the shape check | Yes (`aggregate_public_outputs` returns a list; `_render_task_result` prints lists as JSON) | Fixed in plan: wording corrected; aggregated lists rendered per element; probe (f2) extended |
| 8 | P3: `tempfile.mkstemp` forces mode `0600`, against [CTX.5] Shared Access | Yes | Fixed in plan: `os.open(..., O_EXCL, 0o666)` with a UUID4 name; probe (s) |

The review confirmed the causal premises and the core design: receipt-backed
tracking, host-scoped retirement and verified streaming. No RECONSIDERATION
REQUESTED. A scoped re-check limited to findings 1–3 and 5–8 is the next
review step if the owner wants one before Phase 1 code.

### G2–G4 rulings — 2026-11-05, Anthropic (Claude)

- **G2:** no automatic deletion. Removed:
  - the receipt queue `weft.state.outputs.<tid>`;
  - the TaskMonitor `spill_receipts` slice, its liveness probing, clock and
    idle-accounting rules;
  - the publish-order and ambiguous-outcome rules, the identity check and
    the support-route addition;
  - the companion cleanup-owner and queue-inventory spec inserts;
  - probes (g3), (g4) and (r).
- **Retention probes:** (g1) and (g2) are rewritten to prove that Weft never
  deletes a spill.
- **Documentation:** the spec, README and CHANGELOG document caller-owned
  retention and the side-effect pattern (Phase 1 task 5).
- **G3:** decided as recommended.
- **G4:** `{tid}/{seq}.dat`, with a per-process counter and `O_EXCL`, plus
  the next number on collision.
- **Superseded history:** earlier Review Log rows about receipts and
  retirement (rounds 1–3) are historical; §§3, 7 and 8 are operative.
- **Not yet reviewed.** Phase 1 task 1 reviews the reduced Phase 1.

### Phase 1 scoped reviews — 2026-11-05

**Outside review (owner-supplied).** It endorsed the reduced Phase 1 and the
no-deletion tradeoff, and raised two ambiguities:

| # | Finding | Verified | Disposition |
|---|---|---|---|
| O1 | An aggregated list and a task-returned list are indistinguishable at render time | Yes (`aggregate_public_outputs` returns a plain list) | Fixed in plan: the rule applies to any top-level list with a shaped element; nested values are not inspected. The change to list rendering is stated in §3 B, §7 and the CHANGELOG |
| O2 | "Add no … retry" conflicts with the G4 collision loop | Yes | Fixed in plan: "no publication or cleanup retry; filename collisions advance the counter" |

**Codex scoped review.**
- Reviewer family: OpenAI, reviewing Anthropic's revision.
- Invocation: `codex exec -s read-only -C <repo> -c
  'model_reasoning_effort="high"'`, codex-cli 0.161.0, after a liveness
  probe.
- `git status` was unchanged before and after.
- Verdict: **BLOCK**, on two P1s. It also confirmed:
  - no operative receipt, retention or UUID text remains;
  - all nine Phase 1 Replacement bodies passed an in-memory promotion
    check;
  - the shape → host → location → delivery order is coherent;
  - the `{seq}` algorithm is correct, with no standing-constraint reversal.

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 1 | P1: probe (f2) and "one row per call" contradict the collector, which with terminal proof collects every pending row up to the boundary | Yes (`_result_wait.py` deliverable-row selector) | Fixed in plan: the semantics statement is corrected; (f2) expects one aggregated call, then exit 2 |
| 2 | P1: the CLI does not hold the resolved context (`cmd_result` resolves it; stored-spec runs rebind it) | Yes (`result.py` `build_context`; `run.py` `_resolve_submission_runtime_root`) | Fixed in plan: `TaskResult` and `RunExecutionResult` gain `outputs_root`, set by the command layer from the context actually used; new Phase 1 [PY-2] block; Task 4 file list expanded; probe (q) adds stored-spec rebinding and a configured directory name |
| 3 | P2: rollback can delete Phase 1 files through old `shutil.rmtree` on a same-TID re-spill | Yes (`base.py` `_cleanup_spilled_outputs_if_needed`) | Fixed in plan: the unconditional guarantee is removed, and a pre-revert constraint is stated |
| 4 | P2: no probe enforces collision safety or numeric names | Yes | Fixed in plan: probe (f4) with a pre-existing `1.dat` and concurrent same-TID writers, plus a name regex and sensitivity |
| 5 | P2: shape rejection has no firing probe | Yes | Fixed in plan: probe (p2) with malformed fields, plus sensitivity |
| 6 | P3: (s) passes today; (l1) mixes preservation with new behaviour | Yes (`Path.write_bytes` honours umask) | Fixed in plan: (s) moved to preservation controls; (l1) split into (l1) preservation (`cleanup_on_exit=False`) and (l1b) RED |
| 7 | P3: an insertion anchor was not verbatim (line break after "verify") | Yes | Fixed in plan: the anchor quotes "file integrity after dereferencing." |

**Codex scoped re-check** (same invocation; `git status` unchanged).
Verdict: **BLOCK**. Findings 1, 3–7 and O1 RESOLVED. Two were NOT RESOLVED:

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 2 | P1: `weft result --stream` drops `outputs_root`: `_task_result_event` projects a fixed payload and the CLI stream loop rebuilds `TaskResult` without it, so a context-local spill would print as a reference with exit 0 | Yes (`result.py::_task_result_event`; `app.py` stream loop) | Fixed in plan: both steps carry the field; Task 4 lists them; probe (q) adds a `--stream --context` case and sensitivity |
| O2 | P3: the invariant still said "Nothing retries" | Yes | Fixed in plan: no publication retry or residue cleanup; the only loop is the G4 collision advance |

Codex also confirmed the new [PY-2] anchor is valid and its content covers
rebound contexts.

**Owner acceptance, 2026-11-05.** The owner accepted the reduced Phase 1
review without a further round. The last two fixes (re-check findings 2 and
O2) were verified against the code by the author but not re-reviewed.

Phase 1 task 1's remaining item is the [WT-7] two-factor fit test over the
Phase 1 blocks. It is still to run before Phase 1 code.

## Revision Log

| Date | Baseline | Change | Reason |
|------|----------|--------|--------|
| 2026-11-05 | b7671da6 | Owner accepted the Phase 1 review; [WT-7] fit test remains before code | Owner ruling |
| 2026-11-05 | b7671da6 | `outputs_root` carried through `--stream` (event payload and CLI rebuild) and probe (q) case; invariant retry wording aligned with G4 | Codex scoped re-check findings 2 and O2 |
| 2026-11-05 | b7671da6 | Top-level list rule; collision-loop wording; aggregated `weft result` semantics; `outputs_root` command-layer handoff and Phase 1 [PY-2] block; rollback caveat; probes (f4), (p2), (l1)/(l1b) split, (s) relabel, (q) stored-spec case; verbatim anchor | Outside review O1–O2; codex scoped review 1–7 |
| 2026-11-05 | b7671da6 | G2: no automatic spill deletion (receipts, TaskMonitor slice and their probes and spec inserts removed; caller-owned retention documented); G3 decided; G4 `{tid}/{seq}.dat` | Owner rulings G2–G4 |
| 2026-11-05 | b7671da6 | Round-3 fixes: gate-safe Phase 2 labels; exact [MF-2.1] blocks; inventory targets; resolved roots and context threading; ordered checks; aggregated-list rendering; `spill_receipts` slice spec; identity and support-route corrections; `O_EXCL` file mode; probes (q), (r), (s) and (f2) | agy/Argon BLOCK findings |
| 2026-11-05 | b7671da6 | Explicit shape/host/location precedence; reader-scoped forwarding promise; isolated size-check oracle and separate truncation coverage | Owner requested fixes for the three fresh-read findings |
| 2026-11-05 | b7671da6 | Two phases; G6 host scope (`hostname` in reference, cross-host error, documented single-host limits); G7 outputs-root reservation and probe (p); per-revision author families | Owner rulings on the fresh-read findings |
| 2026-11-05 | b7671da6 | Durable spill receipts and local retention clocks; pre-handoff failure decoding; all-consumer metadata; generated names; ambiguous-publication recovery and rollout | Owner requested addressing round-2 findings |
| 2026-11-05 | 963dc83e + 2026-11-05 worktree specs | Round-1 review revision: age-based spill retirement, per-output names, typed `failed_output` row, mode matrix, at-most-once delivery, split digest/size probes | Codex BLOCK findings 1–9 |
| 2026-11-05 | 963dc83e + 2026-11-05 worktree specs | Initial draft | Owner asked for a plan for the output-fidelity defects found in the re-grade |

## Execution log

- Plan drafting only. No implementation, spec promotion or runtime mutation.
- Owner-requested revision addressed review findings in the operative design,
  proposed spec deltas, tasks, probes and rollout rules. No changes to the
  unrelated launch-claim plan or plan index were made by this revision.
- Validation: `tests/specs/test_plan_metadata.py` (4 passed),
  `bin/check-doc-paths` (OK), tracked and untracked plan whitespace checks.
- Independent review: Anthropic initial BLOCKED; accepted findings revised;
  scoped verification PASS on P1-1 and P2-1 through P2-4. The abandoned earlier
  persisted-anchor draft was never promoted or implemented.

### Implementation start — Phase 1

Baseline: `b7671da6`. User instruction "Please implement per plan" authorizes
both phases, their targeted commits, and the recommended G1/G5 choices for
Phase 2. Unrelated dirty manager/custody/governance work is excluded.

Comprehension: a reader cannot know it holds the last forwarded/copied
reference, so the caller owns spill lifetime and Weft never deletes spills.
Appending failed stdout to error text would mix result data with diagnostics;
it must retain its own public value and nonzero outcome.

Author Phase 1 fit account (recorded before reading the independent account):
- Verdict: fits with strain.
- Grounds: [WT-1] Unix composition; [WT-2] ownership and queue facts;
  [WT-4] one owner, shared function/shared code, no over-armoring.
- Refusal: no MonitorStore result dependency ([ALT-WT-020]), no second
  artifact service or cleanup registry; spill reads use the outbox reference.
- Prediction: retained output remains readable after task and monitor exit;
  ordinary JSON modes retain references, and a closed stdout pipe yields a
  nonzero delivery failure without deleting content.
- Worst fit: queues are the only durable truth, while result bytes live in
  caller-owned files. The queue reference remains the durable result fact;
  Weft does not promote filesystem scanning to lifecycle truth. Caller-owned
  disk growth is the explicit accepted cost, not hidden garbage collection.

RED: repo-managed pytest normal concurrency, `tests/tasks/test_task_execution.py
-k survives_cleanup_on_exit -q`: false cleanup control passed, true cleanup
failed at `output_path.read_bytes()` with FileNotFoundError, proving actual
exit deletion. No timeout/concurrency or acceptance oracle was weakened.

### Independent Phase 1 proposed-delta theory account

# Theory-fit account: Phase 1 output spills (large-output references)

## 1. Verdict

**Fits with strain.**

Most of the change fits the theory:

- The outbox row stays the only durable fact.
- Weft does not gain a retention subsystem, index, or sweeper.
- Text-mode delivery behaves like a Unix tool: bytes go to stdout and failure goes to the exit code.
- The checks on shape and location only recognize Weft's own spill files. They are not a trust boundary.

The strain comes from three places:

- The change keeps a legacy reference shape readable with no end date.
- It adds a new kind of durable artifact. The bytes sit outside every queue and outside the theory's ownership table, and a caller who never asked for the file is made responsible for it.
- The CLI and the Python client now give different answers for the same read.

## 2. Theory sections the change rests on

- **[WT-2a.2] Queues are the only durable truth.** The `large_output` envelope in the outbox is the coordination fact. The `.dat` file is payload, not lifecycle state. The change never asks Weft to read the file to decide status, liveness, or custody. Only text-mode delivery opens it.
- **[WT-2] item 2 / [WT-4.1] Consume-to-disappear.** A consuming read claims the result row before delivery ("at most once per consuming read"), so the queue still empties and vanishes. The change keeps the file out of queue mechanics instead of turning the outbox into a retained table.
- **[WT-2a.7] Exactly once per reservation.** Claim-then-deliver chooses at-most-once delivery and leaves the file as the recovery path. The outcome is explicit, not a guess.
- **[WT-2a.10] A Unix citizen.** The referenced bytes go to stdout and are verified against `size` and `sha256`. Any missing file, mismatch, read error, or failed write "exits 1 … never 0".
- **[WT-2a.11] and [WT-5] trust paragraph.** The checks run in order: shape, then host, then location. A reference that fails shape or location "prints as written"; it is not rejected. This matches "validates shape and recognizes its own artifacts for correctness and portability, never as a trust boundary." The OS umask and filesystem permissions decide who can read a spill.
- **[WT-4.2] One owner for each resource.** Weft names exactly one owner for a spill file, the caller, and no Weft component ever unlinks one: not task exit, not `cleanup_on_exit`, not readers, not monitors.
- **[WT-2a.5] Process isolation and [WT-4.2] Use the primitives you have.** File names come from a per-process counter, and exclusive create (the OS) is the only arbiter of collisions. No shared counter, lock, or broker row coordinates the names.
- **[WT-5] Not a distributed engine.** The spec says plainly "Weft does not support multi-host contexts." The `hostname` field only makes that limit fail loudly when one PostgreSQL context is used from several hosts.
- **[WT-2a.1] / Keep an identity you already have.** The path `{tid}/{seq}.dat` uses the full TID.
- **[WT-4.4] Observable with standard tools.** An operator can inspect spills with `ls` and `sha256sum`. No Weft-only tool is needed.

## 3. What the change refuses, and the matching ALT entries

- **Weft-owned deletion or retention of spills** (delete on consume, delete at exit, monitor pruning). Doing this would need a tracker of which spill each reference points to and when each reader is done. That is a second lifecycle store, which [ALT-WT-010] refuses as a new index or cache subsystem. If TaskMonitor did the pruning, its private state would start to govern a result path, which [ALT-WT-020] refuses.
- **A location check as a security gate.** A reference outside the allowed roots is printed as ordinary data, not refused as dangerous. This is [ALT-WT-014] (no input sanitizer as a security boundary) applied correctly.
- **Cross-host fetch or transport.** No ALT entry covers it directly. It falls under the [WT-5] boundary refusal ("not a distributed workflow engine") and [ALT-WT-010] (no new transport subsystem).
- **Shared-name or overwrite semantics** across outputs, persistent work items, and repeated calls. No ALT entry covers this either. It follows from [WT-2a.5] and the one-owner rule.
- **A gap in the theory.** No [WT-6] entry and no [WT-3] concept row covers out-of-queue payload storage. The refusals above are inferred, not cited. I classify this as a likely **document deficiency**.

## 4. Falsifiable predictions

1. **No overwrites.** Run N concurrent producers that spill into the same `{tid}` directory: multiple outputs, persistent items, and repeated direct calls across processes. There will be exactly N distinct `.dat` files, and every reference's `sha256` will match its own file. One mismatch falsifies exclusive per-output naming.
2. **Weft never deletes.** Trace filesystem syscalls through task exit with `cleanup_on_exit=true`, a consuming text-mode `weft result`, a pipeline forward, and several TaskMonitor cycles. There will be zero unlink calls on any spill path. One Weft-initiated unlink falsifies "caller-owned files never deleted by Weft".
3. **Failure is never exit 0.** Corrupt or truncate the file, or close stdout early. Text-mode `weft run` or `weft result` exits 1 and names the path. A second consuming read finds no row, and the file is still in place.
4. **Foreign host is reported without opening the file.** For a fully shaped envelope whose `hostname` differs from the local host, the trace shows no `open` of `path` before the exit-1 error, whatever the path's location.
5. **Every non-text reader returns the envelope as written.** `--json`, `--all`/`--peek`, pipelines, and the Python client return an envelope byte-identical to the outbox row.
6. **Cost prediction ([WT-4.4] Measure).** Spill files are never deleted and each new process starts its counter at 1. So the first spill from a new process into a directory holding K files makes K+1 exclusive-create attempts. If measurement shows constant cost, then naming is not per-process-counter-plus-probe as specified. If it shows linear growth, a counter-based design has built in an O(K) cost the theory warns against.

## 5. Worst-fitting sentence and the real tension

**Worst-fitting sentence:** "References written before `hostname` existed remain readable by path." (It is repeated as "legacy host-unscoped references … print as ordinary values".)

This is a dual-shape reader with no end. [WT-2a.9] and [ALT-WT-012] allow "at most a bounded forward migration for durable old data" and refuse "legacy decoders, tolerance windows, and dual-shape readers." The reopening condition for [ALT-WT-012] is external users with a published compatibility promise, and the diff does not cite it. Under the [WT-7] log rule this is a transfer failure against [ALT-WT-012], unless the author shows that old references are bounded in time. Even then, the bounded migration would need to be written down; the sentence as worded is open-ended.

**Real tension: who owns the bytes, given the consume-to-disappear model.** The theory's substrate idea is that state disappears as work is consumed ([WT-2] item 2, [ALT-WT-019]), and that each resource has one explicit owner ([WT-4.2]). The change keeps both rules on paper by naming the caller as owner. But spills are "the automatic fallback for unexpectedly large results." The owner it names often does not know a file exists, especially when the reference arrives through a pipeline or the Python client, which never delivers or announces it.

So the one-owner rule is met by declaration, and in practice `{tempdir}/weft/outputs/` grows with no bound. That is the leak an unconsumed table would cause, moved to the filesystem where no queue peek shows it ([WT-7] "Inspection needs Weft-only tooling" stops short of files).

Two smaller strains make it worse:

- [WT-4.4] "The client mirrors the CLI": delivery lives only in the CLI adapter. The Python client gets `outputs_root` and "may ignore it", which is two answers to one read.
- [WT-2a.2]: a result path, text-mode delivery, now depends on durable bytes held outside any queue.

**Suggested classification:**

- **Document deficiency:** the theory has no concept row for caller-owned output artifacts and no ALT entry for spill retention.
- **Change deficiency:** the open-ended legacy-reference sentence, and possibly the probe-from-1 naming cost.

Author comparison/classification: both accounts say fits with strain and
predict no deletion, truthful delivery failure and distinct files. The external
payload and caller-owned-growth tensions agree; these are explicit owner
choices already present in spill storage, not an inferred new lifecycle store.
The independent account proposes a document deficiency for the absent artifact
concept: recorded as a possible document deficiency for owner consideration,
no theory revision is silently made. CLI/client difference is classified as
agent reasoning, contested: text delivery and structured reference projection
are two representations of the same read, not separate coordination facts.
The legacy-hostname concern under [ALT-WT-012] is recorded as contested agent
reasoning: the current envelope schema declares hostname optional for stored
references, uses one shape predicate and has no legacy fallback decoder. The
owner-approved absence rule remains explicit. If this is judged an unbounded
compatibility promise, a separate owner theory decision is needed; this work
makes no such theory claim. The per-process collision O(K) prediction is true
for repeated fresh processes reusing a TID; ordinary immutable one-process task
identity does not repeatedly restart with the same TID, and G4 expressly chose
this naming. Record the tension rather than replacing the owner's choice.

### Phase 1 implementation author fit account

Boundary: Phase 1 runtime/tests/spec diff over `b7671da6`, excluding unrelated
manager/custody changes. Verdict: fits with strain. Grounds: [WT-1], [WT-2a.2],
[WT-2a.10], [WT-4] resource ownership/shared code/OS policy. Refusal: no Monitor
artifact registry, reference counting or MonitorStore dependency ([ALT-WT-020]);
no new cleanup timer or polling loop ([ALT-WT-002]). Prediction: actual CLI
20 MiB output equals serialized bytes with CLI framing; a no-wait task's output
is available after task exit and from result --stream with the actual context;
same-host outside-root objects remain ordinary JSON while foreign-host shapes
fail without a file open; size/digest mutations each independently reject.
Worst fit: caller ownership of automatically generated files can be surprising
and grows disk until caller cleanup. This is expressly the owner's G2 choice;
Weft documents it and recommends explicit side-effect files for managed output.
The current schema's optional hostname and numeric O_EXCL collision scan carry
the same recorded strains as the proposed-delta account, not hidden fallbacks.

### Phase 1 independent implementation theory account

# Two-factor fit account: result output fidelity (Phase 1 output)

**Inputs.** I used only the theory text and the diff in your message. I read no files, called no tools, and saw no plan, log or author account. I didn't write a plan file or call ExitPlanMode, because you said not to call tools.

**Boundary.** Phase 1 output fidelity only. That covers three things:
- **Spill lifetime:** spills are no longer deleted at task exit, and each spill gets its own numbered file.
- **Host-scoped references:** each reference carries the `hostname` of the producing host.
- **Text-mode delivery:** waiting `weft run` and single-task `weft result` stream the spilled bytes to stdout, checking size and SHA-256, using the `outputs_root` carried through the result types.

---

## 1. Verdict: fits with strain

The main move fits the theory. The old `cleanup_on_exit` path deleted a spill at task exit, often before any reader had used the reference, which broke the result. Making spills persistent and numbered, and making text mode print the real bytes, puts results back on the Unix-tool path. A failure now shows up where it happened, as exit 1 with a message naming the path.

The change also removes `_spilled_output_dirs`, an in-memory record that drove deletion. That brings the code closer to "processes carry hints."

Three parts strain the theory:
- **The location check.** A symlink-resolved, path-component check that the file sits under an allowed root works like a path sanitizer.
- **References without `hostname`.** They stay readable by path for good, with no end date. That is a second accepted shape.
- **Nobody in Weft owns spill files.** "The caller's responsibility" is stated, but nothing in Weft holds or tracks the files, so they pile up with no limit.

None of these breaks an identity invariant. They are strain, not misfit.

## 2. Theory sections the change rests on

- **[WT-1]:** "composable Unix tools"; "Failures remain visible where they happened"; "no background magic". Text mode sends the real bytes to stdout, failures exit non-zero, and no hidden reaper deletes files.
- **[WT-2a.10] A Unix citizen:** "the exit code carries the outcome". A missing file, a size or SHA mismatch, or a failed write exits 1, never 0.
- **[WT-2a.7] Exactly once per reservation:** disposition is declared, "never a guess". The spec says delivery is at most once per consuming read: the row is claimed first, and a failed delivery leaves the row consumed and the file in place.
- **[WT-2a.11] One operator trust domain:** "validates shape and recognizes its own artifacts for correctness and portability". This covers the shape check and the host check. It is also strained by the location check (see part 5).
- **[WT-2a.2], [WT-4.1] "Queues carry facts; processes carry hints":** removing `_spilled_output_dirs` takes away process memory that controlled the fate of a durable artifact.
- **[WT-4.1] "Keep an identity you already have":** the command layer passes along the `outputs_root` of the context it actually used, including stored-spec rebinding and `result --stream` events. The CLI doesn't have to work it out again.
- **[WT-4.4] "Shared function, shared code":** `render_text_output` replaces two copies of the same rendering code, in `cli/run.py` and `cli/app.py`.
- **[WT-4.4] "No capability diminution":** the change fixes a lost capability. Before, a reference pointed at a deleted file, and persistent tasks overwrote `output.dat`. Exclusive `{seq}.dat` files stop the overwrites.
- **[WT-2a.9] One current contract, and [WT-0] rule 3:** the specs (02, 05, 10, 14), README and CHANGELOG change together with the code. The new meaning of `cleanup_on_exit` is stated, not changed silently.
- **[WT-4.4] "Observable with standard tools":** spills stay at predictable paths that `ls`, `cmp` and `sha256sum` can check, and the reference stays visible to a queue peek or `--json`.

## 3. What the change refuses, and the entry that refuses it

- **Main refusal: a Weft-run retention system for spill files.** That means a TTL reaper, a registry of spill files, a cleanup monitor, or a deleter tied to consumption. It would be new machinery to track artifact lifetime, and its record would become a second lifecycle store. **[ALT-WT-010]** refuses this ("A new scheduler, event bus, index, or cache subsystem"; "No heaps, counters, or registries…"). The CHANGELOG and README instead send owned retention to "an explicit output location."
- **Secondary refusal:** recording spill membership in an unconsumed `weft.state.*` queue so Weft could clean up later. **[ALT-WT-019]** refuses this ("queues as unconsumed relational tables… manual GC").
- **Bounded, not refused:** dereferencing in `--json`, `--all`, pipelines and the Python client. Those readers return the reference as written, so the machine-readable contract has one shape (**[WT-2a.9]**, **[ALT-WT-012]**). This is a scoping decision, not a refusal.
- **Cross-host delivery is refused** ("Weft does not support multi-host contexts"). No [ALT-WT-*] entry covers this. It is a product-boundary claim made only in spec 05, not in [WT-5]. That is a candidate **document deficiency**.

## 4. Falsifiable predictions

**P1. Delivered bytes match the inline result.** Take a string result and a dict result, each run once just under `output_size_limit_mb` and once just over it.
- Prediction: for each type, text-mode `weft run` writes the same kind of bytes in both runs, differing only in length.
- What would disprove it: the over-limit string comes out JSON-quoted or escaped, or the dict differs from `json.dumps(..., ensure_ascii=False)` (separators, `\uXXXX`). Then "fidelity" is false, because the inline path prints `str(value)` or `json.dumps(ensure_ascii=False)`, while the spill path prints "the UTF-8 encoding of the serialized result." The diff doesn't show those two encodings agreeing.

**P2. Spill files survive and stay separate.** A persistent task produces two over-limit outputs.
- Prediction: `{outputs}/{tid}/1.dat` and `2.dat` exist, with different SHA-256 values.
- They are still present after the task exits with `cleanup_on_exit: true` and after both results are consumed.
- `weft result TID --json` emits the reference byte-for-byte as stored.

**P3. Corruption fails loudly, and only once.** Truncate `1.dat`, then run `weft result TID` in text mode.
- Prediction: exit 1, with stderr naming the path.
- A second `weft result TID` finds no row (at most once per consuming read).
- Stdout may already hold part of the bytes, because `_deliver_spill` writes chunks before it checks the hash. If a caller treats any stdout from a failed run as valid, that misuse is visible here.
- What would disprove it: exit 0, or the row still being available afterwards.

**P4. The location check only recognises Weft's own files (theory-level).** If the check really just recognises Weft's artifacts:
- Prediction: deleting it changes no outcome for any reference Weft itself produced.
- What would disprove it: the only test that fails is one where a crafted reference points at a file outside the allowed roots. Then the check is acting as a trust boundary, which contradicts [WT-2a.11] and [ALT-WT-014].

**P5. No change to quiet cost.** The change adds no timer and no polling. Prediction: CPU use of an idle Manager and an idle task is unchanged (the [WT-4.3] probe).

## 5. Worst-fitting theory sentence

> [WT-2a.11]: "Weft validates shape and recognizes its own artifacts for correctness and portability, never as a trust boundary, and adds no in-band authentication or sanitizer that would imply one."

**The tension.** The change adds a step 3, "location": the symlink-resolved `path` must lie under a symlink-resolved allowed root, compared by path components. That has the same form as the `validate_safe_path`-style helper that [ALT-WT-014] refuses. Its reason given there was: "A sanitizer implies a boundary that does not exist."

**The case that it is correctness, not security.**
- An envelope outside the roots is printed as written, not rejected.
- Without the check, a user value that merely looked like an envelope could have its bytes streamed and then fail on the hash, exiting 1.

**Why that case is weak.**
- The shape check already looks for a 64-character hex SHA-256, an integer size and the `large_output` type. Accidental collisions are already very unlikely.
- Anyone inside the trust domain can `cat` any file the check would protect.
- So symlink resolution and containment add little correctness. They match the shape of path-traversal defence, which [WT-4.4] "No over-armoring" calls "adversary-invented states."

**Runners-up:**
- **[ALT-WT-012]:** "Dual readers hide contract bugs and cascade." The optional `hostname` ("References written before `hostname` existed remain readable by path") is a permanent second shape, not a "bounded forward migration."
- **[WT-4.2] "One owner for each resource":** naming "the caller" as owner of files Weft creates, with no mechanism or record, is an owner in name only. The theory's own disappear-when-consumed idea ([WT-2] item 2) is stated for queues only. Whether it should cover the files Weft creates is something the theory leaves open, which makes it a candidate **document deficiency**, not a change deficiency.

Author comparison: both verdicts are fits with strain and agree on ownership,
queue-first result facts, truthful delivery failure, no extra runtime machinery,
and caller-owned growth. Contested agent reasoning: resolved-root recognition
is not an OS trust boundary; an out-of-root value remains ordinary output and
no new access restriction is claimed. This is the expressly approved G7 artifact
recognition rule, and [WT-2a.11] explicitly permits recognizing artifacts.
Optional hostname is one current schema predicate, not a second decoder;
[ALT-WT-012] concern is retained as contested rather than silently declared
resolved. Caller-owned file lifecycle and cross-host scope may merit theory
clarification; record possible document deficiencies for owner consideration,
without modifying theory during this implementation. Inline-vs-spill JSON
serialization is a falsifiable concern, but the accepted fidelity contract is
against the existing producer serialization, not a new canonical JSON rewrite.

### Phase 1 touched-claim audit

Unchanged theory audit rows carry forward from the program-theory reconciliation
and latest committed manager repair audit. Touched [WT-2a.2] and [WT-4] ownership:
queue references remain authority; no MonitorStore, timer, queue family or
retirement service added. Caller owns files (documented). [WT-2a.10]: actual
20 MiB waiting run delivered content; path-scoped size/digest failures and real
closed pipe produce nonzero output errors. [WT-4] shared code/context identity:
result/run use one helper with command-resolved outputs_root, including rebound
stored specs and result-stream event projection. No change to reactor scheduling;
quiet-CPU behavior is unmeasured here, not asserted measured/aligned.

Additional RED proof: real subprocess stdout pipe read-one-byte then close
returned 120 although the delivery error named the path; Python's shutdown flush
replaced the selected exit status. Fix redirects stdout to devnull only after
BrokenPipeError, preserving exit 1. The unchanged three-process delivery suite
then passed all 3 cases (pipe, stored-spec rebound custom directory, symlinked
context). No timeout, sleep or worker-count adjustment was used.

### Phase 1 completed implementation review dispositions

Anthropic read-only review returned BLOCKED; findings were verified before action.

| ID | Disposition |
|----|-------------|
| 1 Windows binary descriptor | Reject premise: CPython 3.14 `Modules/_io/fileio.c` lines 473–475 calls `_setmode(self->fd, O_BINARY)` for the fdopen path too. No platform branch is added for an already handled primitive. Strengthen the real multi-process spill proof with newline-bearing payloads, checked byte-for-byte. Primary source: https://raw.githubusercontent.com/python/cpython/3.14/Modules/_io/fileio.c |
| 2 closed pipe exit 120 | Accept: actual process probe reproduced 120; redirect stdout to devnull only after BrokenPipeError prevents final interpreter flush replacing exit1. Unchanged process test passed. |
| 3 ordinary ANSI/rendering regression | Accept: helper accepts an ordinary-text emitter; both CLI adapters inject typer.echo so ANSI/encoding behavior is preserved without commands importing CLI/Typer. Add ordinary ANSI preservation probe. |
| 4 redundant wrapper | Accept: one recognized-file transfer phase owns write, newline and flush error translation; exception-message substring heuristic removed. |
| 5 pre-delivery fstat | Reject extra work: accepted contract streams bounded chunks then checks size/digest, explicitly permits incomplete bytes on error, and has separate firing integrity oracles. No second filesystem check is required for that contract. |
| 6 outputs_root projections | Modify: keep optional outputs_root consistently on structured command results, including --all, so the public shape has one meaning. Omit absent outputs_root from generic follow-event projection; actual result-stream projects its resolved root. No events.py context reconstruction. |
| 7 list newline | Reject change: owner-approved rule explicitly renders each top-level list element on its own line, including reference lists under result --stream. Scalar nl=False remains unchanged. |
| 8 test name | Accept: rename the all/JSON preservation test to state its actual boundary. |
| 9 missing real proofs | Accept: real stdout-pipe closure, rebound stored-spec custom root and symlinked context tests added and passed. Newline content is included in process spill creation proof. |
| 10 hostname changes | Accepted scope observation, not a new host alias system: hostname is exact producer metadata under G6, and multi-host delivery is outside this slice. |

Scoped recheck is requested only for accepted IDs 2, 3, 4, 6, 8 and 9.
Pre-existing hypothetical FIFO/device inputs are observations, not a new
sanitization or special-file subsystem.

### Final Phase 1 boundary reaffirmation

Author fit verdict remains fits with strain for the final Phase 1 production
snapshot including ordinary-output Typer callback, required emitter, omitted
absent event outputs_root, and stdout broken-pipe handling. Grounds/refusal/
predictions/tension are the five-part implementation author account above.
These refinements preserve existing ordinary formatting and pipe behavior;
no new lifecycle owner or scheduling input appears. Independent account for
this exact final boundary is requested after this author account is recorded.

Gate evidence discipline: initial broad dirty-tree runs are diagnostics, not
acceptance. Three custody failures reproduced in an output-free baseline with
only unrelated manager/task_state work, proving the dirty tree cannot judge
this targeted change. Initial output-only runs found new field expectations,
new test-module classification and a no-wait test ordering defect: the latter
called result before runtime observation publication although its intended
contract was after task exit. Fix the test to await actual task completion;
no production grace, timeout or concurrency tuning. All superseded broad
commands are retained with their outputs and were interrupted once their
snapshots were known stale. Final acceptance uses the exact output-only tree
with all standard tests, no skip list and unchanged concurrency.

### Phase 1 review rechecks and final boundary probes

Scoped Anthropic review: accepted IDs 2, 4, 8 and 9 PASS. ID 3 initially
reported ordinary BrokenPipeError still caught by the CLI's OSError branch;
fix re-raises ordinary BrokenPipeError to Click before that branch. The
unchanged real plain-1MiB and spill-11MiB pipe tests then passed. Narrow final
ID-3 recheck returned PASS (verbatim account below). ID 6's optional root
consistency was implemented by omitting absent keys from generic follow events;
removing root metadata from --all was rejected as an unnecessary special case.

**Finding 3: PASS**

- **The plain-text path lets the error through.** For ordinary text and JSON values, the helper calls `emit_text`, which is `typer.echo`, with no try block anywhere in `_render_output_value` or `render_text_output`. A `BrokenPipeError` from that write reaches both call sites. There, `except BrokenPipeError: raise` comes before `except OSError` (`weft/cli/app.py:312`, `weft/cli/run.py:114`), so the error is re-raised rather than turned into the `OSError` → stderr → `return 1` branch.
- **Click finishes the job from there.** Click's `main` catches EPIPE, installs its flush-pacifying wrapper, and exits 1. That explains the plain-1 MiB result: exit 1 and no "Exception ignored". The old typer.echo pipe behavior is preserved, not masked.
- **The conversion in the spill path is deliberate and narrow.** `_stream_spill` does turn `BrokenPipeError` into `OSError("incomplete output at …")` after redirecting stdout to devnull. That only covers the raw byte transfer of a recognized spill file, not ordinary Typer output. It gives the specified exit 1 with the spill path named on stderr, which the 11 MiB case confirms.
- **Nothing else in the diff catches it.** A grep of `weft/cli/app.py` and `weft/cli/run.py` turns up no other `OSError` or `BrokenPipeError` handlers. The only other broad catch is `except Exception` at `weft/cli/app.py:493`, which handles queue stdin input and isn't on the result-rendering path.

**Observation only (not part of finding 3):** `_stream_spill` calls `sys.stdout.flush()` before streaming. If earlier buffered output hits a closed pipe there, it gets reported as "incomplete output" for the spill instead of going through Click's EPIPE handling. The exit code is the same (1), so this isn't a defect.

Final owner-boundary additions: a real spawned producer with newline-bearing
bytes; numeric O_EXCL collision across processes; process umask002 group access;
persistent queue-backed work items and repeated direct calls; real agent two
spill outputs with each file verified; no-wait completion before result read;
stream/rebound/symlinked context; monitor queue retirement and restart under
both output roots with exact scoped dead identity and no spill unlink. The
monitor proof advances only LivenessMonitor's time namespace and uses the
TaskMonitor explicit now_ns seam, never the shared broker clock.

Initial fixture mistakes (unsupported persistent kw, missing local TID fixture,
FrozenDict pickling, invalid lifecycle transition setup) were corrected in tests;
none changed runtime policy or acceptance thresholds. Shared new task-test module
classification and public RunExecutionResult outputs_root expectations were
updated to the declared contract instead of weakening policy gates.

### Final Phase 1 independent account (final rendering boundary)

# Two-factor fit account: result output fidelity (Phase 1)

This account uses only the theory and the diff, as the user instructed. No files, plans, logs or other accounts were consulted, and no tools were called.

## 1. Verdict

**Fits with strain.**

The diff fixes a real fidelity defect. A task with `cleanup_on_exit` set could delete its spill file before any reader saw it, and repeated outputs overwrote `output.dat`. The fix composes existing concepts:
- The outbox row stays the durable fact.
- Each file has exactly one named owner, the caller.
- `run` and `result` share one renderer.
- The exit code carries delivery failure.

It adds no new lane, store, daemon or timer. The strain sits in three places:
- legacy-shape tolerance;
- text mode reading one way while the client reads another;
- a location check that looks a lot like a sanitizer.

## 2. Sections the change rests on

- **[WT-2a.2]:** the queue row (the reference) is the fact, and the file is payload, not lifecycle state.
- **[WT-4.2] One owner for each resource:** spill files now have exactly one explicit owner, the caller.
- **[WT-2a.10] A Unix citizen:** bytes go to stdout, and a missing file, mismatch or failed write exits 1, never 0. A broken pipe still exits 1 rather than 120.
- **[WT-2a.11]:** shape checks and "recognize its own artifacts" by root containment and hostname.
- **[WT-4.4]:**
  - *Shared function, shared code:* one `render_text_output` serves both commands.
  - *No capability diminution:* a reader can no longer lose output to a cleanup race.
- **[WT-2a.7]:** delivery is at most once per consuming read, and the retained file keeps a failed delivery recoverable.
- **[WT-2a.9]:** producers, the result types and the specs change together, with `outputs_root` carried through `TaskResult`, `RunExecutionResult` and stream events.

## 3. Refusal and ALT

- **Refused:** a Weft-owned retention sweeper or garbage collector for spill files. Weft never deletes them; retention belongs to the caller. This applies **[ALT-WT-010]** (no new subsystem without a measured cost and a named concept the owner accepts).
- **Refused:** turning the root containment check into a security boundary. **[ALT-WT-014]** governs. A path outside the roots prints as written instead of being rejected as dangerous, so the check only recognizes Weft's own artifacts, and it does not imply a trust boundary.
- **Refused:** silently dereferencing on machine-readable paths. `--json`, `--all`, pipelines and the client get the reference as written. This keeps one current contract for structured readers and does not change the API value.

## 4. Falsifiable predictions

1. **Cleanup race:** a task with `cleanup_on_exit: true` whose output exceeds `output_size_limit_mb`, read by `weft result TID` after the task exits, writes exactly `size` bytes whose SHA-256 equals the reference, and exits 0.
2. **No overwrite:** a persistent task that spills twice leaves `1.dat` and `2.dat`, and both deliver correctly.
3. **Corruption:** changing one byte in the file makes text-mode `weft result` exit 1 with a message naming the path. The row stays consumed.
4. **Foreign host:** a reference whose `hostname` differs from the reader's exits 1 without the file ever being opened.
5. **Short reads:** `weft result TID | head -c1` on a spill exits 1, not 120.
6. **Machine readers:** `--json` and the Python client return the reference dict unchanged.

Any one of these failing falsifies the fit claim.

## 5. Worst-fitting sentence and tension

**Worst-fitting:** "Absent in references written before this field existed… References written before `hostname` existed remain readable by path."

Against **[WT-2a.9]** and **[ALT-WT-012]** ("no permanent dual decoders"), this is a permanent dual-shape reader. It is not a bounded forward migration. The optional-`hostname` branch in `_spill_shape` and `_deliver_spill` never retires.

**Secondary tensions:**
- **Client mirrors CLI ([WT-4.4]):** text mode gives the content while the client gives the reference. The split is defensible as rendering, not capability, but the theory never says that, so two careful readers could disagree.
- **[WT-2a.11] vs [ALT-WT-014]:** a resolved-path containment test is mechanically the same thing as `validate_safe_path`. Only the fallback to printing the value, rather than rejecting it, keeps it on the "recognition" side. The theory gives no test for telling recognition from sanitizing.
- **No over-armoring ([WT-4.4]):** the 64-hex regex check, the hostname gate and the `dup2`-to-devnull trick for broken pipes come close to engineering for unlikely states.
- **Consume-to-disappear in spirit ([WT-4.1]):** queues clean themselves up, but `{tempdir}/weft/outputs` now grows without bound by design. The theory covers queue disappearance and says nothing about files Weft creates. That silence is a possible document deficiency.

Comparison: both final accounts say fits with strain. Caller-owned file growth
is the agreed owner choice and not a new internal lifecycle store. Optional
hostname remains contested agent reasoning against [ALT-WT-012]: one current
schema recognizes an optional additive field, with no legacy execution lane.
CLI/client difference remains contested representation reasoning: text renders
content while structured APIs preserve the same queue reference. Resolved-root
recognition is explicitly G7, not a security promise; the independent account
also acknowledges the ordinary-value fallback as this distinction. Potential
artifact-ownership theory wording is a possible document deficiency for the
owner, not silently revised here. The later extraction of the unchanged 64 KiB
read literal to `_constants.py::SPILL_OUTPUT_READ_SIZE` is house-style naming,
with no semantic or ownership change to this account's boundary.

### Phase 1 local checkpoint validation

Frozen output-only baseline tree: `/tmp/weft-output-target`, based on `b7671da6`.
Its 674 tracked/new source files were hashed before acceptance; repeated checks
reported no differences. Unrelated manager/custody/governance edits were excluded.
- Full SQLite all-tests command: **6672 passed, 60 skipped, 521.15 s**.
- Fast SQLite command: **6667 passed, 49 skipped, 1 failed, 519.02 s**.
  Exact failure: `test_interactive_drain_waits_once_while_descendant_holds_pipes`
  observed 9 `poll_stdout` calls versus the existing upper bound 8; no sleeps
  were recorded. The same test passed in the frozen full all-tests command.
  This is reported as an unresolved intermittent observation, not called a
  wholly green fast command or a proven pre-existing defect. No bound, timing,
  worker count or interactive-session implementation was changed.
- Output-owner focused SQLite and PostgreSQL: **33 passed** on each backend.
- Addon roots SQLite: passed with five expected opt-in/backend skips.
- Addon roots PostgreSQL: repeated unchanged full command **386 passed,
  4 skipped**; the earlier snapshot-publication timeout remains recorded as
  an intermittent pre-output cold-launch observation with origin unresolved.
  The same complete command on baseline also passed; that does not prove the
  earlier failure pre-existing. No timeout or worker budget was changed.
- Integrity sensitivity: removing only size validation made the isolated size
  case fail its expected-error oracle; restoring size and removing only digest
  validation made the digest case fail. Both original controls then passed.
- Frozen cheap gates: full mypy **496 source files**, Ruff, format **641 files**,
  full spec suite, doc-path gate and whitespace checks passed.
- Independent completed-code review accepted findings are closed by scoped
  PASS; independent theory accounts and contested comparisons are above.

Verification-overlap deviation for a LOCAL checkpoint: Phase 1's full
PostgreSQL command continues against the unchanged frozen Phase 1 tree while
Phase 2 may begin only after the Phase 1 commit exists. No publication or
release is authorized. The PG command is not claimed passed while running,
and final plan closure requires its result and Phase 2's own gates. This keeps
an immutable verification boundary while avoiding idle implementation time.
The non-green fast observation is disclosed above; full all-tests SQLite
covered that same contract at normal concurrency and passed.
