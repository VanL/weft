# TID Display Encoding: Lossless Rendering Everywhere

Status: draft
Source specs: docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5], [OBS.6], [OBS.8], [IMMUT.3], [LIVENESS.R5]; docs/specifications/01-Core_Components.md [CC-3.2]; docs/specifications/10-CLI_Interface.md [CLI-1.2.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/program-theory.md [WT-2a.1], [WT-6]
Superseded by: none

Class: 5+P. Trigger reasoning: the change replaces normative spec text
([OBS.4], [OBS.5], [CLI-1.2.3], the title clauses of [LIVENESS.R5] and
[CC-3.2], and vocabulary lines in [OBS.6], [OBS.8], [PY-2]) and adds a
rejected alternative, [ALT-WT-022], to `docs/program-theory.md`, which that
document says only a class-5 plan may do. Risky triggers also fire: a
public CLI input shape changes, the process title format changes, the value
format of persisted display fields changes (`short` in
`weft.state.tasks.<tid>`, `tid_short` in `weft.log.tasks`,
`manager_tid_short` in the operational log), and the liveness host loses an
evidence input (owner ruling 5 of 2026-11-03 removes title corroboration),
which is a contract change to the evidence a liveness path consults.
Hardening is therefore mandatory, and
`docs/agent-context/runbooks/hardening-plans.md` has been applied; it now
also covers the corroboration removal (difference-register row D14, the
Task 4 stop-if, §8). +P: Task 6 adds a standing structural test that
mechanically gates one aspect of the theory, a single canonicalization
function and a single decoder for every flexible TID lookup boundary
([WT-2a.1]; owner ruling 1 of 2026-11-03). Under the owner's principle of 2026-11-03, a test
that mechanically gates an aspect of the theory is valid, and adding such a
gate is material to how future work is verified ([DOM-15]). Effective
requirements are class 5's plus the pre-landing different-family review,
which §10 already schedules. No CI workflow step is added (owner ruling 4).

Plan type: implementation with spec revision (spec and theory text change,
then code; landed atomically, strategy B).

## 1. Goal

Every rendering of a task ID must decode back to the same TID ([WT-2a.1],
owner decision 2026-11-02). Today the process title, the `short` and
`tid_short` display fields, the `task tid --reverse` output, thread names,
and default pipeline names use a ten-digit folded short form
(`weft/helpers/__init__.py::tid_short_form`) that is not unique. Because it
is not unique, Weft carries a candidate search over retained
`weft.state.tasks.<tid>` names and an ambiguity error ([CLI-1.2.3]). This
plan replaces the short form with one lossless spelling, the 13-character
Crockford base32 encoding of the TID with no checksum. It deletes the fold,
its tests, the candidate search, and the ambiguity error. CLI arguments
accept the full TID or the 13-character encoding, and both decode to the
same integer, through one canonicalization function and one decoder.
Because the rendering is lossless, the liveness host's title regex, which
only corroborated a lossy form, is removed: the host decides liveness on
the scoped `(pid, create_time)` identity and the runtime handle alone, and
the title stays observability (owner rulings, 2026-11-03). The owner's
words: "the old lossy encoding is going away, as
should its tests, associated search code, etc."

## 2. Source Documents

Source specs (governing contract at the baseline; exact replacement text is
in `## Proposed Spec Delta`):

- `docs/specifications/07-System_Invariants.md` [OBS.4] (process title
  format), [OBS.5] (the ten-character short form and its non-uniqueness),
  [OBS.6] (the "short-ID resolution" clause), [OBS.7] (sanitation;
  unchanged), [OBS.8] (vocabulary), [IMMUT.3] (TID immutable and unique;
  unchanged), [LIVENESS.R5] ("titles corroborate and never prove a full
  TID"; that clause is replaced, a title is observability only, owner
  ruling 5 of 2026-11-03)
- `docs/specifications/01-Core_Components.md` [CC-3.2] (the host-pid
  evidence paragraph's "Process titles corroborate and never prove a full
  TID."; replaced the same way)
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (read for title
  corroboration: no [MF-5] sentence names a process title; the only title
  mention in that file, line 1850, outside [MF-5], lists the title as a
  surface that exposes live activity, which stays true; unchanged)
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.3] (`task tid`,
  short-ID candidates, the ambiguity error, the implementation mapping)
- `docs/specifications/14-Python_API_Surfaces.md` [PY-2] (the
  `runtime_liveness` input rule names "short IDs")
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2] (exact
  19-digit message-ID form; unchanged)

Theory:

- `docs/program-theory.md` [WT-2a.1] (one task identity, rendered
  losslessly; the owner decision of 2026-11-02 that this plan implements),
  [WT-2a.2] (process memory, including a title, is a hint), [WT-2a.9] (one
  current contract), [ALT-WT-012] (no compatibility code), [WT-4] "Keep an
  identity you already have", [WT-4] "No capability diminution", [WT-6]
  (rejected-alternative record format).
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (completed):
  §9 fork 2 (b), the owner decision text and the note that "cleanup plan 12
  records it as a new [ALT-WT-*] entry when the old encoding is removed";
  audit row I-1 (`drifted`, "12 TID display encoding"); the candidate check
  "5. Lossy TID short form". This plan is that cleanup plan 12. Its scope
  line ("retire [OBS.5] and the ten-digit short form, the
  candidate-discovery and ambiguity code, and the liveness host's title
  regex") is now satisfied as written: owner ruling 5 of 2026-11-03 removes
  the regex (Task 4).

Historical plans (retired, cited only in spec history lines):
`2026-08-31-short-tid-derivation-plan` and
`2026-08-31-registry-custody-contracts-plan` (source 396c3b85). Not
intended behavior any more.

Guidance: `CLAUDE.md` §1.1, §3, §4, §5.1;
`docs/agent-context/decision-hierarchy.md` [DOM-15];
`docs/agent-context/runbooks/writing-plans.md`;
`docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/adversarial-acceptance-probes.md`;
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

## 3. Context and Key Files

### Current structure

- **The fold.** `weft/helpers/__init__.py::tid_short_form` (lines 132–146)
  validates with `is_task_tid` and returns
  `(grain + counter * 2_441_406) % 10**10` zero-padded to
  `TASKSPEC_TID_SHORT_LENGTH` (`weft/_constants.py` line 84, value 10).
  Callers use it two ways: to produce a display value, and as a validator
  (`try: tid_short_form(x) except ValueError: skip`).
- **Title budget.** `weft/_constants.py` lines 889–919:
  `PROCESS_TITLE_CONTEXT_LENGTH = 8`, `PROCESS_TITLE_NAME_LENGTH = 20`,
  `PROCESS_TITLE_DETAILS_LENGTH = 15`, `PROCESS_TITLE_STATUS_LENGTH` (9,
  derived from the longest status), and `PROCESS_TITLE_MAX_LENGTH` is the
  sum `len("weft-") + 8 + 1 + TASKSPEC_TID_SHORT_LENGTH + 1 + 20 + 1 + 9 + 1
  + 15` = 71 today. `PROCESS_TITLE_HANDOFF_LENGTH` = max + 1 NUL = 72, used
  by `weft/core/process_title.py::_ProcessTitle.tick` to pad the macOS
  handoff. Measured with `./.venv/bin/python` on this repo: the formula
  gives 71 and 72.
- **Title producer.** `weft/core/tasks/base.py::BaseTask.__init__` sets
  `self.tid_short = tid_short_form(self.tid)` (line 274);
  `BaseTask._format_process_title` (line 2395) emits
  `weft-{context}-{self.tid_short}:{name}:{status}[:details]`. The TID
  segment is not passed through `_sanitize_process_title_segment` and is
  never truncated; name, context, and details are.
- **Title consumer.** `weft/liveness/host.py::inspect_host_process`
  (lines 20–58) first matches `(pid, create_time)`; only then does it read
  `cmdline()[0]` and match
  `^weft-[A-Za-z0-9_-]+-{short}:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+`. A match
  returns `("live", "identity_match_title_match")`; a miss returns
  `("live", "identity_match_title_unconfirmed")`. The only production caller
  that passes `expected_tid`,
  `weft/liveness/analysis.py::probe_runtime_liveness` (line 107), reads only
  `.evidence`, never `.reason`; the two calls in
  `weft/core/manager_runtime.py` (lines 939, 975) pass no `expected_tid` and
  never read a title. A title therefore never changes a liveness
  verdict. Owner ruling 5 (2026-11-03) removes this title read, the regex,
  both `identity_match_title_*` reasons, and the `expected_tid` parameter
  (Task 4).
- **Candidate search and ambiguity.**
  `weft/commands/system.py::_read_tid_states` (line 201) groups every
  `weft.state.tasks.<tid>` suffix from `weft/core/task_state.py::list_task_state_tids`
  by short form. `weft/commands/tasks.py::resolve_full_tid` (line 149) and
  `weft/commands/system.py::_resolve_tid_filters` (line 223) raise
  `CommandUsageError("Ambiguous short TID …")` (exit 2) on a collision.
  `_resolve_tid_filters` has no production caller (grep below: only its
  definition and three test references). `_task_control_result` runs an
  ambiguity preflight loop over every selected TID (lines 2565–2568).
  `weft/commands/_task_snapshot_reducer.py::reduce_task_event` matches a
  filter against the full TID or the short form (line 196).
- **CLI TID normalizers today.** `resolve_full_tid` (task status, ping, stop,
  kill, tid via `task_tid`); `_command_tid` (tasks.py line 2396, rejects any
  non-decimal input; all four callers pass `allow_unknown_full=True`);
  `weft/commands/submission.py::normalize_tid` (exact 19-digit; used by
  `weft/commands/events.py` and `WeftClient.task`);
  `weft/commands/result.py::_normalize_tid` (any decimal digits);
  `weft/commands/manager.py::cmd_manager_stop` / `cmd_manager_status` (no
  normalization); `weft system prune --task`, which
  `weft/commands/prune.py::cmd_system_prune` passes unnormalized into
  `RetentionPruneConfig.task_filters` (line 207), where
  `weft/core/pruning/retention.py` filters task evidence by exact membership
  (lines 380–387). The `is_task_tid` filter at
  `weft/core/pruning/runtime.py` line 499 is a different path: it filters
  snapshot-read candidates in `_current_task_state_rows`, not the
  `--task` selector. Only the first two accept a short form today.
- **`task tid`.** `weft/cli/app.py::task_tid` (line 1786) prints
  `tid_short_form(result)` for `--reverse`; `weft/commands/tasks.py::cmd_task_tid`
  (line 2664) validates `--reverse` with `is_task_tid` only (no range check).
- **Display fields.** `short` in every `weft.state.tasks.<tid>` row
  (base.py line 2538; required nonempty by
  `weft/liveness/policy.py::valid_tid_state_payload`, line 173); `tid_short`
  in task-log events (base.py lines 2027, 2642), in `TaskSnapshot`
  (`weft/commands/types.py` line 137), and in TaskMonitor JSONL summaries
  (`weft/commands/task_monitor.py` line 416); `manager_tid_short` in the
  operational log (`weft/core/serve_log.py` line 119). [OBS.6] and
  [CLI-1.2.3] already state the stored `short` is display metadata, not
  resolution authority.

### Files to modify

- `weft/_constants.py`
- `weft/helpers/__init__.py`
- `weft/core/tasks/base.py`
- `weft/core/pipelines.py`
- `weft/core/monitor/task_monitor.py`
- `weft/liveness/host.py` (title regex and `expected_tid` removed; owner
  ruling 5)
- `weft/liveness/analysis.py` (the one `expected_tid=` call)
- `weft/commands/tasks.py`
- `weft/commands/system.py`
- `weft/commands/_task_snapshot_reducer.py`
- `weft/commands/events.py`
- `weft/commands/task_monitor.py`
- `weft/commands/submission.py`
- `weft/commands/result.py`
- `weft/commands/manager.py`
- `weft/commands/prune.py`
- `weft/cli/app.py`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/14-Python_API_Surfaces.md`
- `docs/program-theory.md`
- `README.md`, `CHANGELOG.md`
- this plan (`docs/plans/2026-11-02-tid-display-encoding-plan.md`) and
  `docs/plans/README.md` (status flips in Tasks 1 and 8)
- tests: the 25 files that import `tid_short_form` (list in the execution
  log), plus `tests/core/test_process_title.py`,
  `tests/core/test_pipelines.py`, `tests/core/test_manager.py`, and
  `tests/core/test_task_monitoring.py` (producer-boundary assertions, Task
  4), `tests/core/test_client.py` (the client case table, Task 6), and
  `tests/commands/test_manager_commands.py`, `tests/cli/test_cli_manager.py`,
  and `tests/commands/test_retention_prune.py` (the selection tests for the
  widened Manager and prune surfaces, Task 6); delete
  `tests/system/test_short_tid.py`; new `tests/system/test_tid_encoding.py`
  (new file)

This list is the canonical staging list for Task 8's explicit-file commit.

### Read first

- `weft/helpers/__init__.py` lines 132–146 and `weft/helpers/message_ids.py`
  (whole file: `is_task_tid`, `normalize_exact_message_id`)
- `weft/liveness/host.py` (whole file) and `weft/liveness/analysis.py`
  lines 90–122
- `weft/core/tasks/base.py` lines 2350–2435
- `weft/commands/tasks.py` lines 140–197, 2396–2415, 2526–2580, 2664–2690
- `weft/commands/system.py` lines 199–236
- `tests/system/test_short_tid.py` and `tests/commands/test_tid_mapping_contracts.py`
  (whole files; they hold the short-form and ambiguity tests this plan
  removes)
- `tests/helpers/hypothesis_strategies.py::taskspec_tid_strings`

### Shared paths: reuse, do not duplicate

- `weft/helpers/message_ids.py::is_task_tid` and `normalize_exact_message_id`
  stay the full-TID spelling and range checks.
- `simplebroker.format_message_id` formats the decoded integer back to the
  canonical 19-digit string.
- `weft/commands/submission.py::normalize_tid` is the one TID
  canonicalization function (owner ruling 1, 2026-11-03): it is extended to
  decode the encoding, and every TID-argument boundary calls it (§4 "One
  canonicalization function, one decoder").
- The title formatter `BaseTask._format_process_title` and the
  `PROCESS_TITLE_*` sum stay the single budget calculation. The TID width
  grows from 10 to 13 and the name limit shrinks from 20 to 17 (owner
  ruling A), so the sum stays 71 and the handoff length stays 72.

### Abstractions not to add

Exactly two new functions, `encode_tid` and `decode_tid`, placed in
`weft/helpers/__init__.py` where `tid_short_form` is today, one replacement
constant (`TASKSPEC_TID_ENCODED_LENGTH`, added in Task 3; `TASKSPEC_TID_SHORT_LENGTH`
is deleted with the fold in Task 4),
and one new alphabet constant in `weft/_constants.py`. Nothing else: no codec class, no TID
value type, no second module, no registry, no compatibility decoder for the
ten-digit form, no feature flag. `weft/commands/submission.py::normalize_tid`
is extended, not duplicated: no second normalizer and no per-command
decoder.

### Comprehension questions (answer in the execution log before the first edit)

1. A task started by the previous Weft version is still running when the
   Manager restarts onto this change. Its title is
   `weft-proj-0123456789:mytask:running`. What does
   `inspect_host_process(pid, create_time)` return, and does any liveness
   verdict change?

   Expected answer: `("live", "identity_match")` once the scoped
   `(pid, create_time)` identity matches a non-zombie process. The host no
   longer takes `expected_tid` and never reads the title (owner ruling 5,
   2026-11-03), so the old ten-digit segment plays no part. No verdict
   changes: before this change the title outcome could only pick between
   two `live` reasons, and `probe_runtime_liveness` reads only `.evidence`.
   The title is observability only ([OBS.4], [LIVENESS.R5] as replaced);
   the old title lasts until that task exits.

2. Which queue names change, and does decoding a 13-character argument read
   any queue?

   Expected answer: no queue name changes. `T{tid}.*`,
   `weft.state.tasks.<tid>`, and every other TID-bearing queue name use the
   full 19-digit decimal TID. The encoding appears only in renderings
   (process titles, thread names, the `short`/`tid_short`/`manager_tid_short`
   display values, default pipeline names, `task tid --reverse` output).
   `decode_tid` is arithmetic and reads no queue.

## 4. Invariants and Constraints

Must not change:

- **The TID integer and its minting.** TIDs are SimpleBroker hybrid
  timestamps from the broker's timestamp generator, used as the
  spawn-request message ID; nothing about minting, the integer, or
  `TaskSpec.tid` validation changes ([IMMUT.3], [SB-0.2]).
- **Queue names.** Every `T{tid}.inbox`, `T{tid}.reserved`, `T{tid}.outbox`,
  `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `weft.state.tasks.<tid>` uses the
  full integer as 19 decimal digits. No queue name ever contains the
  encoding.
- **Payload field names and validity.** `short`, `tid_short`, and
  `manager_tid_short` keep their names; only the value changes.
  `valid_tid_state_payload` keeps requiring a nonempty string `short`. Old
  rows whose `short` holds a ten-digit value stay valid: no reader treats
  `short` as authority. Renaming the fields would be a persisted-shape break
  and would make a running prior-version task's snapshots invalid across the
  restart that it outlives by design ([WT-2a.13]). Owner ruling,
  2026-11-03: "The field names should stay the same. If you need to
  distinguish between the tid and the short form, it should be short_tid."
  The existing names are kept. A genuinely new field that carries the short
  display form beside the TID, where no field exists today, would be named
  `short_tid`; this plan adds no such field, so `short_tid` names no surface
  here.
- **Title shape and vocabulary.** The `weft-` prefix, the 8-character
  context, the `:`-separated segments, the 15-character details, and the
  `[A-Za-z0-9_-]` vocabulary stay. The TID segment is never truncated.
- **Liveness authority.** The scoped `(pid, create_time)` identity, under
  the runtime handle's `control.authority`, decides host liveness alone.
  Owner ruling 5, 2026-11-03: "I don't think we need corroboration now,
  because the short form is lossless. Remove." The process title stays as
  observability (`ps`, [OBS.4]) and is never liveness evidence
  ([LIVENESS.R5] and [CC-3.2] as replaced in the delta).
- **TID opacity.** Owner ruling on plan 10, 2026-11-03: "Weft only encodes
  and decodes the *short* display form. Everywhere else it uses the TID."
  The TID is opaque everywhere. `encode_tid` and `decode_tid` treat the TID
  as one whole integer in 0 through 2^63 − 1 and map it bijectively to and
  from the 13-character form; that is the only encoding and decoding Weft
  does. No Weft code reads the physical-time or counter component of a TID.
  The fold's bit operations (`tid_short_form`'s grain/counter split) leave
  with it in Task 4, and no replacement code splits a TID.
- **Case canonicalization at the input boundary.** Owner ruling,
  2026-11-03: "As for accepting, we should accept either lower or upper
  case and should canonicalize at the boundary." An encoded TID argument is
  accepted in upper, lower, or mixed case. The boundary is every flexible
  TID lookup argument in `weft/commands/` and `weft/client/` (the strict
  `runtime_liveness` and `task tid --reverse` boundaries, which take a full
  TID only, are the stated exceptions and do not widen): the existing
  normalizers (`tasks.py::resolve_full_tid`, `tasks.py::_command_tid`,
  `result.py::_normalize_tid`, `submission.py::normalize_tid`) and the
  surfaces owner ruling 1 widens (`manager.py::cmd_manager_stop`,
  `manager.py::cmd_manager_status`, the `--task` selector mapping in
  `prune.py::cmd_system_prune`, and `WeftClient.task`, which already calls
  `submission.normalize_tid`). The existing normalizers already strip
  whitespace and a leading `T` there (`CLAUDE.md` §4.11, "validate at the
  boundary, then stay strict"); the manager and prune selector mappings
  have no normalization today and gain it in Task 6 (the cross-family round
  of 2026-11-03 corrected an earlier sentence that listed them with the
  existing normalizers). Everything past the boundary sees only canonical
  forms (the full TID, and the uppercase encoding that producers emit). No
  internal consumer gains case tolerance: the reducer filter stays exact
  membership, and the liveness host reads no title at all (owner ruling 5).
  A non-canonical form reaching internal code is a contract violation that
  the boundary tests guard against; internal code does not absorb it.
  Producers emit uppercase only.
- **One canonicalization function, one decoder.** Owner ruling 1,
  2026-11-03: "Sure, widen. Make sure they all use the same
  canonicalization function and the same decoder function." The one
  decoder is `weft/helpers/__init__.py::decode_tid`: it folds case and maps
  a full or encoded TID to the canonical 19-digit full TID. The one
  canonicalization function is `weft/commands/submission.py::normalize_tid`,
  the existing shared normalizer (already used by `weft/commands/events.py`
  and `WeftClient.task`), extended rather than duplicated: it strips
  surrounding whitespace, removes one leading `T`, calls `decode_tid`, and
  raises `InvalidTID` on failure. Every boundary listed above calls
  `normalize_tid` and maps `InvalidTID` to its own existing outcome (`None`
  from `resolve_full_tid`, `InvalidTID` from `_command_tid`, the existing
  digits-only fallback in `result._normalize_tid`, the unchanged not-found
  path for the manager and prune selectors). In production `normalize_tid`
  is the only caller of `decode_tid`, and no boundary carries its own case
  folding or decoding. The `task` commands keep their existing removal of
  repeated leading `T`s by stripping before the call
  (`raw.strip().lstrip("T")`, then `normalize_tid`), so no prefix spelling
  accepted today is narrowed. The structural test in Task 6,
  `tests/system/test_tid_encoding.py::test_tid_arguments_share_one_canonicalizer_and_one_decoder`,
  enforces this as a standing gate (the +P in the class paragraph).
- **Forward-only state, reserved policy, `spec`/`io` immutability, spawn
  context.** Untouched by this change.
- **Python `runtime_liveness` stays strict.** It accepts only an unprefixed
  canonical full TID ([PY-2]); the encoding is a CLI input, and this plan
  only renames the "short IDs" clause.

Hidden couplings:

1. Retired by owner ruling 5 (2026-11-03). The formatter used to share
   `encode_tid` with the liveness title regex; the host no longer reads
   titles, so no consumer must agree with the formatter's case or width.
   The item keeps its number because items 5 and 7 are cited by number.
2. `TASKSPEC_TID_ENCODED_LENGTH` and `PROCESS_TITLE_NAME_LENGTH` both feed
   `PROCESS_TITLE_MAX_LENGTH` and `PROCESS_TITLE_HANDOFF_LENGTH`, which sets
   the padding of the macOS handoff in
   `weft/core/process_title.py::_ProcessTitle.tick`. The two changes (10 → 13
   and 20 → 17) cancel, so the handoff stays 72; changing one without the
   other would change the handoff length. Tests
   that hardcode a ten-character `tid_short` (`tests/core/test_process_title.py`
   line 208) or a digits-only TID segment
   (`tests/tasks/test_task_observability.py` line 585) break.
3. Validator call sites (`_public_status_event`, `_reduce_task_log`,
   `reduce_task_event`) rely on the helper raising `ValueError` for any
   non-TID, including non-`str` values such as an int `tid`. `encode_tid`
   keeps that contract and additionally rejects 19-digit strings at or above
   2^63, which SimpleBroker cannot mint.
4. The default pipeline name `pipeline-{encoding}` is 22 characters; the
   title's 17-character name segment truncates it to `pipeline-` and the
   first eight encoded characters. The pipeline task's own
   TID is `pipeline_tid` (`weft/core/pipelines.py` line 631), so the title's
   TID segment still carries the full encoding. The TaskSpec name itself is
   untruncated.
5. `cmd_task_tid --reverse` checks `is_task_tid` only; the CLI then calls the
   encoder. Without a range check, `--reverse 9999999999999999999` would
   raise inside the CLI adapter. Task 6 adds the check.
6. Thread names `weft-worker-{tid_short}-{lane}` (base.py line 772) carry the
   encoding; `tests/tasks/test_task_execution.py` line 2214 searches thread
   names for it.
7. Task names of 18 to 20 characters that a test expects verbatim in a
   title truncate at 17. Found by
   `grep -noE '(name"?\s*[:=]\s*f?"[^"]{18,20}")'` over the 16 test files
   that set or read titles: `observability-task` (18;
   `tests/tasks/test_task_observability.py` lines 57 and 538) and the
   proposed `encoded-title-test` (18) break; `abcdefghijklmnopqrst` (20;
   `tests/core/test_process_title.py` line 207) stays green because that
   test compares `ps` output with the formatter's own output and the length
   with `PROCESS_TITLE_MAX_LENGTH`, and it now exercises the truncation. The
   other 18–20-character names in those files (`task-default-cleanup`,
   `task-monitor-child`, `autostart-pipeline`, `restart-backoff-due`,
   `restart-after-kill`, `distinct-task-name`, `nested-immutability`) appear
   in no title assertion. Task 4 fixes both breaks.
8. Series plan 11 (`docs/plans/2026-11-02-cli-client-surface-parity-plan.md`)
   extracts the body of `cmd_system_prune` into `_prune_families`, which
   its new `client.system.prune` also calls. The `task` selector
   normalization of Task 6 must live in that shared body, not in either
   wrapper, or the client path bypasses it; the structural test locates
   the body by its `task_filters=` binding so it survives the extraction.
   Landing order is free; the sequencing text is in Task 6 (owner's
   outside reviewer, 2026-11-03).

Error-path priorities: decoding failure at a CLI boundary is an input error
(the command's existing invalid- or unknown-TID outcome, never a
traceback). Title application stays best-effort ([OBS.4]); a native
truncation never fails a task.

Review gates: no new execution path; no new dependency; no compatibility
code for the ten-digit form ([ALT-WT-012]); no drive-by refactor; no mocks
in place of the real formatter, the real liveness host, or the real CLI;
cross-family review of plan and delta before the spec-promotion slice.

### Encoding choice

The governing model is a bijection between the TID integer and its display
string, not a hash plus a lookup: an arithmetic codec removes
retention-dependent identity recovery. Forms compared over the full 63-bit
domain:

| Form | Width for the full domain | Relevant tradeoff |
|---|---:|---|
| Crockford base32 (chosen) | 13 | excludes visually confusing letters; fixed-width lexical order matches numeric order |
| Base36 | 13 | no width saving over base32 across the full 63-bit domain |
| Hex | 16 | simpler, familiar alphabet; longer title segment |
| Decimal | 19 | the existing canonical form; longest segment |

### Decision: name 17 or title 74

Both options keep the `weft-` prefix and the 8-character context.

| | A: name segment 20 → 17, title stays 71 | B: name stays 20, title 71 → 74 |
|---|---|---|
| Constants touched | `PROCESS_TITLE_NAME_LENGTH` and the TID length | the TID length only; the sum widens itself |
| Handoff length | 72 (unchanged) | 75 |
| Task names in titles | every name of 18–20 characters loses up to 3 characters; existing `pgrep -f`/`pkill -f` patterns on such names stop matching | unchanged |
| Shell safety ([OBS.7], [OBS.8]) | unchanged: same vocabulary, same separators | unchanged |
| `ps` visibility | identical: the identity occupies characters 1–27 in both (`weft-` 5 + context 8 + `-` 1 + TID 13), so it stays in the leftmost columns of any `ps` width; `/proc/<pid>/comm` (15 bytes on Linux) shows only the prefix in both | identical |
| Native buffer | no change | 3 more bytes. [OBS.4] already accepts silent native truncation and forbids capacity probing; truncation falls on the trailing status/details, never on the TID in characters 15–27 |

Evidence. [OBS.4] gives no `ps` column limit; the spec's only platform
statements are "Native buffer limitations may cause silent truncation" and
the setproctitle 1.3.7 NUL reservation recorded at
`PROCESS_TITLE_NUL_RESERVATION`. The argv span setproctitle can rewrite in a
spawned child was measured on this machine (macOS, repo venv): a
`multiprocessing` spawn child's argv is 159 bytes including NULs, which
exceeds both 72 and 75.

**Author's recommendation was B, title 74.** It keeps every task name
visible, touches one constant, and costs three bytes against a buffer
measured at 159 on this macOS host.

**Owner ruling (2026-11-02): A, title stays 71.** The owner recalls an
external limit on how much of the title can be rewritten and that 71 was
set at that limit. The 159-byte figure above is one macOS measurement of
the spawn child's argv span; it does not establish the limit on Linux, on
Windows, or on the padded macOS handoff, and the plan may not widen the
title on it. Task 3 therefore sets `PROCESS_TITLE_NAME_LENGTH = 17`, so
`PROCESS_TITLE_MAX_LENGTH` stays 71 and `PROCESS_TITLE_HANDOFF_LENGTH`
stays 72; the [OBS.4] replacement says 17 and 71; and the task-name row of
the difference register reads "intentionally retired" (names of 18 to 20
characters lose up to 3 characters; `pgrep -f` and `pkill -f` patterns on
such names change). The owner also asked for the span to be measured, and
ruled on how on 2026-11-03: "This should not be part of CI regularly. This
is something to test and then document." Task 4 therefore runs the
one-time measurement described under "Rewritable span measurement" on each
supported platform, records the figures in the execution log, and
documents them in [OBS.4], where the 71-character budget lives. If every
platform reports a span comfortably above 75 bytes, the plan reports that
and the owner may revisit the ruling; the plan does not revisit it on its
own.

**Rewritable span measurement (Task 4; one-time, not a test).** A
standalone script, run once per platform by the implementer and never
committed, collected by pytest, or wired into CI (owner ruling 4,
2026-11-03), spawns a `multiprocessing` child the way
`weft/core/launcher.py` does. The child drives a real
`weft.core.process_title._ProcessTitle`, so it exercises both writers the
runtime uses:

- Phase 1, initial writer. The child calls `set()` with a 1,024-character
  probe string drawn from the title vocabulary. On macOS this is the
  `processtitle` writer that `_ProcessTitle._initialize` selects; on Linux it
  is the stock `setproctitle` setter; on Windows it is whatever writer
  `_ProcessTitle._initialize` selects there; neither has a second phase.
- Phase 2, post-handoff writer (macOS only). The child first sets a
  71-character title, forces the deadline (`_deadline = 0`, as the existing
  native test in the same file does), calls `tick()`, which pads to
  `PROCESS_TITLE_HANDOFF_LENGTH` and imports the stock setter, and then calls
  `set()` with the 1,024-character probe.

Synchronization: after each write the child sends a message on a
`multiprocessing.Pipe` and blocks until the parent replies; the parent reads
`psutil.Process(pid).cmdline()` only between those two messages; a `finally`
block tells the child to exit, joins it with a timeout, and kills it if it
is still alive. For each phase the script prints one line
`span-measurement platform=<sys.platform> phase=<1|2> survived=<n>
joined=<len(" ".join(cmdline))>`, where `survived` is the longest prefix of
the probe present in the readback. When all 1,024 characters survive, the
figure is a lower bound (`>=1024`), not a maximum. On `darwin` and
`linux`, if fewer than 71 characters survive in a phase, the script also
prints `span-below-title-budget phase=<n>` and exits 1; that is a finding
for the owner, not a gate. On `win32` the script makes no budget
inference: an unchanged argv there measures no rewritable capacity (§6),
so a readback of zero is not a below-budget finding; the script prints
`span-readback-unavailable platform=win32` after the phase line and exits
0. The figures are evidence for the owner, not a gate on
the budget.

The handoff code's comment ("Pad before import: Darwin import initializes")
implies that on macOS the stock setter's discovered span is set by the
padding itself, so phase 2 may report about `PROCESS_TITLE_HANDOFF_LENGTH`
by construction. That is an expectation the measurement tests, not a
finding; the implementer records what phase 2 reports without
reinterpreting it.

How each platform is run (reproducibly, once, no standing step, no gate):
the implementer writes the script to a scratch path outside the repository,
copies its full text into the execution log before the first run so every
platform runs the same text, and runs `./.venv/bin/python <scratch
path>/span_measurement.py` from the repository root after `. ./.envrc` and
`uv sync --all-extras`:

- macOS: on the development Mac, both phases.
- Linux: on a Linux machine (or a Linux container with the repository
  checked out at the same commit and the same `uv sync --all-extras`),
  phase 1 only.
- Windows: on a Windows machine, phase 1 only, with `.venv\Scripts\python`
  in place of `./.venv/bin/python`. Not through `workflow_dispatch`: the
  existing `Test` workflow's dispatch takes only the `reactor_baseline`
  input and runs its fixed pytest steps, so invoking the script there would
  need a workflow edit, which ruling 4 removes. `cmdline()` is expected not
  to reflect the setter on `win32` (§6); the script records what the
  readback shows and exits 0, and the Windows record is "readback
  unavailable on this platform; the title budget is not measurable here by
  this method", not an inferred span and not a below-budget finding.

The implementer copies each printed line into the execution log with the
date, the platform, the Python version, and the commit, and Task 4 adds the
figures to [OBS.4] in the documented form "measured on <date>: <platform>:
<bytes rewritable>" (see "[OBS.4] — measured span line" in the delta); the
Windows entry reads "measured on <date>: win32: readback unavailable on
this platform; the title budget is not measurable here by this method".

### Owner decisions

Every fork this plan raises, in one place. Items 1–5 and 7 are ruled by
the owner; item 6 is deferred to closeout by the owner. Rulings of
2026-11-03 on the remaining items were given in one answer, numbered 1–6
as the session lead listed them; each is quoted where it is applied.

1. Title budget: ruled A by the owner on 2026-11-02 (name 17, title stays
   71), with the per-platform span measurement reported back (see
   "Decision: name 17 or title 74"; how it is measured is item 7). Not
   open.
2. Field naming: ruled by the owner on 2026-11-03, keep, as recommended:
   "The field names should stay the same. If you need to distinguish
   between the tid and the short form, it should be short_tid." `short`,
   `tid_short`, and `manager_tid_short` (and the `tid_short` attribute and
   `{tid_short}` placeholder) keep their names; only their values change.
   Any future genuinely new field that carries the short form beside the
   TID is named `short_tid`; this plan adds none (§4 "Payload field names
   and validity", §9). Not open.
3. Decoding case: ruled by the owner on 2026-11-03: "As for accepting, we
   should accept either lower or upper case and should canonicalize at the
   boundary." Upper, lower, and mixed case are accepted, and case is
   canonicalized at the CLI and client input boundary (§4 "Case
   canonicalization at the input boundary", the [OBS.5] delta). Excluded
   letters: ruled by the owner on 2026-11-03 (ruling 6): "Exclude, don't
   normalize." The decoder rejects `I`, `L`, `O`, and `U` in either case;
   there are no Crockford I/L→1 or O→0 aliases. That was already the
   plan's text ([OBS.5] delta, Task 2 rejection table, Task 3 "Not
   allowed"), so only this item closes. Not open.
4. The D8 widening of `weft result`, `manager stop`/`manager status`,
   `system prune --task`, and `WeftClient.task`: ruled by the owner on
   2026-11-03 (ruling 1): "Sure, widen. Make sure they all use the same
   canonicalization function and the same decoder function." Every listed
   surface is widened, `WeftClient.task` included (a [PY-2] sentence in the
   delta). The second sentence is the design constraint stated in §4 "One
   canonicalization function, one decoder" (`normalize_tid` and
   `decode_tid`) and enforced by the Task 6 structural test. Not open.
5. Whether [ALT-WT-022] needs a [REV-WT-*] record: ruled by the owner on
   2026-11-03 (ruling 2): "Agree". No [REV-WT-*] record; the note after the
   theory delta stays. Not open.
6. Owner endorsement of the [ALT-WT-022] text: deferred to closeout
   (owner, 2026-11-03: "Not now"); accepted by the owner (2026-11-03: "I
   accept the theory edits"), which covers the [WT-2a.1] edit and the
   [ALT-WT-022] entry as drafted. Not open.
7. The CI step for the span measurement: ruled by the owner on 2026-11-03
   (ruling 4): "This should not be part of CI regularly. This is something
   to test and then document." The step is removed from the plan
   (`.github/workflows/test.yml` is no longer modified, so the `CLAUDE.md`
   §8 "Don't do: modify CI/CD" concern is moot); the span is measured once
   per platform and documented (Task 4). Not open.

Fresh-eyes findings for the owner (2026-11-02), both resolved:

- (a) `weft/commands/submission.py::normalize_tid` is reached by no
  un-normalized CLI argument: there is no `weft task events` command, and
  `task status --watch` hands `follow_task_events` a TID already
  normalized by `_command_tid`. Widening it changes the public Python
  surface `WeftClient.task(...)`. Resolved by ruling 1 (item 4):
  `WeftClient.task` is included, and the [PY-2] delta gains the sentence
  that the client's task lookup accepts the encoded form and canonicalizes
  it at its boundary.
- (b) The reconciliation plan's §9 fork 2 (b) scope for this plan reads
  "retire … the liveness host's title regex", while this plan kept title
  corroboration. Resolved by ruling 5: "I don't think we need
  corroboration now, because the short form is lossless. Remove." The
  regex, both `identity_match_title_*` reasons, and the `expected_tid`
  parameter leave `weft/liveness/host.py` (Task 4); the scope line is now
  satisfied as written. Stop-if checked against the classifier: the title
  is the only evidence in no reachable case (D14), so the removal is not
  escalated.

### Difference register

| # | Behavior | Today | After | Disposition |
|---|----------|-------|-------|-------------|
| D1 | Display value of `short`, `tid_short`, `manager_tid_short`, title TID segment, thread names, default pipeline name, `task tid --reverse` | 10-digit fold | 13-character Crockford base32, uppercase | intentionally changed (the decision) |
| D2 | `task tid X` with a short form | namespace search; the matching full TID, `InvalidTID` ("Invalid resolved task ID", exit 2) when nothing matches, or the ambiguity error | arithmetic decode; returns the full TID without an existence check, exactly as a full-TID argument does today | unified with the full-TID path |
| D3 | Ambiguity error (`CommandUsageError`, exit 2, lists candidates) | possible on task status/ping/stop/kill/tid | cannot occur; code and tests deleted | intentionally retired |
| D4 | Batch stop/kill ambiguity preflight before any control write | runs | deleted; per-item accepted/failures partition unchanged | retired with D3 |
| D5 | `_resolve_tid_filters` | test-only helper | deleted | retired (no production caller) |
| D6 | Reducer filter matches full TID or short form | both | full TID only (every production filter is a full TID) | unified |
| D7 | A ten-digit argument to a `task` command | resolved by search | not a TID: `Task … not found` / `Invalid task ID`, exit 2, no control write | intentionally retired, no compatibility decoder |
| D8 | `weft result`, `WeftClient.task` (via `submission.normalize_tid`, which no CLI argument reaches un-normalized: there is no `weft task events` command, and `task status --watch` passes an already-normalized TID), `manager stop`/`manager status`, `system prune --task` | full TID only (`weft result`: any decimal string, pinned by `tests/commands/test_result.py::test_result_tid_normalization_removes_at_most_one_prefix`; `manager stop`/`status` and `prune --task`: the raw string, no normalization) | also accept the 13-character encoding in any case, through the one canonicalization function `normalize_tid`; every other accepted spelling is unchanged, except that a 13-character all-decimal argument is now read as an encoded TID (it named no task before, since every TID has 19 digits), and the manager and prune selectors now also accept surrounding whitespace and one leading `T` because they share `normalize_tid` (a spelling that named no Manager or task before) | new acceptance, owner ruling 1 (2026-11-03): "Sure, widen. Make sure they all use the same canonicalization function and the same decoder function." |
| D9 | Validators given a 19-digit string ≥ 2^63 | accepted by `tid_short_form` | rejected by `encode_tid` (SimpleBroker cannot mint it) | narrowed to the real domain |
| D10 | `task tid --reverse` with a 19-digit value ≥ 2^63 | printed a short form | `InvalidTID`, exit 2 | narrowed |
| D11 | Title maximum / handoff length | 71 / 72 | 71 / 72 | preserved (option A, owner ruling) |
| D12 | Task name in title | up to 20 characters | up to 17 characters | intentionally retired (option A, owner ruling): names of 18 to 20 characters lose up to 3 characters, and `pgrep -f`/`pkill -f` patterns on such names change |
| D13 | Host reason for a running prior-version task | `identity_match_title_match` (the baseline regex matches its ten-digit segment) | `identity_match`, evidence `live`; the title is not read (earlier drafts of this plan said `identity_match_title_unconfirmed`) | changed with D14: a prior-version title is treated exactly like a new one; no verdict changes |
| D14 | Title corroboration in `weft/liveness/host.py::inspect_host_process` | after a `(pid, create_time)` match, with `expected_tid`, reads `cmdline()[0]` and matches a title regex; reason `identity_match_title_match` or `identity_match_title_unconfirmed`, evidence `live` either way | regex, both reasons, the `cmdline()` read, and the `expected_tid` parameter removed; after the identity match the result is `("live", "identity_match")`; the title is observability only ([OBS.4]) | class C (changed, no capability diminution), determined by reading the classifier: the title is read only after the scoped identity has already matched a non-zombie process, both title outcomes return evidence `live`, every non-`live` outcome (`missing_exact_identity`, `process_absent`, `process_unresolved`, `process_zombie`, `identity_mismatch`) returns before the title is read, a `create_time` that cannot be read returns `unknown`/`process_unresolved` before it, the only `expected_tid` caller (`weft/liveness/analysis.py::probe_runtime_liveness`) reads only `.evidence` and reports its own reason (`host_identity_live`), and the other callers (`weft/core/manager_runtime.py` lines 939 and 975, `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py` line 363) pass no `expected_tid`. The identity check alone already decided every case the title decided, so no case relied on the title (owner ruling 5) |

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [OBS.4] measured span line | span measured on macOS, Linux, and Windows | macOS measured (both phases); Linux and Windows recorded as `pending: owner-run` | the implementer runs only on the development Mac; the series brief directs recording both as owner runs with the procedure | none: the [OBS.4] line already carries the pending entries; the owner replaces them with figures in the stated form |
| [OBS.5] (D9) | `encode_tid` rejects 19-digit values at or above 2^63 at the validator sites | the same, plus `weft/commands/tasks.py::_task_status_record` now sends any argument that `resolve_full_tid` rejects (including an out-of-range 19-digit value) to the bulk path instead of the known-TID path | the fast suite caught it: `tests/commands/test_submission_observation.py::test_custody_is_not_probed_for_noncanonical_or_out_of_range_identity[9999999999999999999]` failed with `ValueError: invalid message ID` from `encode_tid` building the synthetic `unknown` snapshot's `tid_short` in `_known_status_record`; such a value names no task (SimpleBroker cannot mint it), so it takes the not-a-TID path; the test's contract (`status` is `None` or `unknown`, no custody probe) holds | none |
| none (process) | one landing commit (strategy B) including `docs/program-theory.md`; `Status:` `promoted` at Task 1 | a landing commit with code, tests, specs, README, CHANGELOG and this plan (`Status: draft`), then a separate `Theory:` commit with the two theory blocks that moves `Status:` and the index row to `promoted` | the series' common rules commit an accepted theory edit alone so it reverts alone; `bin/check-doc-paths` checks every Replacement block of a `promoted` plan, so `promoted` before the theory blocks land would fail it | none |

## Spec Baseline

- `b7eed9f9` — docs/specifications/07-System_Invariants.md,
  docs/specifications/01-Core_Components.md,
  docs/specifications/10-CLI_Interface.md,
  docs/specifications/14-Python_API_Surfaces.md, and docs/program-theory.md
  at plan authoring time. Plan type: implementation with spec revision.
- Promotion baseline identifier: `af98c73b` + worktree (Task 1, 2026-11-03;
  the spec file diff is the landing commit's diff of the four spec files);
  the landing commit SHA is recorded in the `Theory:` commit's Execution
  log entry.

## Proposed Spec Delta

Promotion strategy:

| Target file | Strategy | Sections touched |
|-------------|----------|------------------|
| docs/specifications/07-System_Invariants.md | B — atomic | [OBS.4] first paragraph; [OBS.4] measured span line (Task 4); [OBS.5] replaced; [OBS.6] one sentence; [OBS.8] body; [LIVENESS.R5] bullet; Related Plans |
| docs/specifications/01-Core_Components.md | B — atomic | [CC-3.2] title sentence; Related Plans (the list after [CC-3.5]) |
| docs/specifications/10-CLI_Interface.md | B — atomic | [CLI-1.2.3] `task tid` bullet, short-ID paragraph, implementation mapping; Related Plans |
| docs/specifications/14-Python_API_Surfaces.md | B — atomic | [PY-2] `runtime_liveness` input clause; Related Plans |
| docs/program-theory.md | B — atomic | [WT-2a.1] parenthetical; new [ALT-WT-022] |

Why B: the delta is small, every new mapping claim names `encode_tid`,
`decode_tid`, or the extended `normalize_tid`, which exist only after the
code lands, the title clauses describe a host that reads no title only
after Task 4 lands, and [WT-2a.9] asks for
one coordinated change. Spec text, mapping claims, code, and reciprocal
`Spec:` backlinks land in one commit. Task 1 applies the text in the
worktree first so later tasks are judged against it; nothing is committed
between Task 1 and Task 8.

Status mechanism: no prose `Status:` change and no planned/exploratory
classification; all five files stay active.

### [OBS.4] — replace the opening of the first paragraph

Replaced span: opens "Process titles follow `weft-{context_short}-…" and
closes "…optional details are limited to 15 ASCII characters. When" (the
first three lines under the heading). The rest of the paragraph, from "the
native buffer has sufficient capacity" to "does not delay the initial Unix
title.", is unchanged. Clause audit: the 8 and 15 limits are kept and the name
limit narrows from 20 to 17; "the short TID follows OBS.5" becomes the
encoded-TID clause; the 71-character total is unchanged.

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
Process titles follow `weft-{context_short}-{tid_short}:{name}:{status}[:details]`
  Context and task-name segments are limited to 8 and 17 ASCII characters respectively;
  the `tid_short` segment is the complete 13-character encoded TID of OBS.5 and is never
  truncated; optional details are limited to 15 ASCII characters, so the longest supported
  title is 71 characters. When
```

### [OBS.5] — replace the bullet

Replaced span: opens "- **OBS.5**: The TID short form is exactly ten decimal
characters derived" and closes "Malformed suffixes are ignored." The two
following "Implementation plans:" history lines stay. Clause audit: the
fold, its multiplier, "The form is not unique", the ambiguity pointer, and
the candidate definition are retired with the short form. "no producer
slices digits from the decimal TID" survives as "no producer formats the
encoding itself". "Stored mapping shorts are display metadata" survives.
"Discovery does not read payloads" survives in [OBS.6] ("Names-only
discovery reads no payloads"). "Malformed suffixes are ignored" described
only the candidate search and retires with it.

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
- **OBS.5**: Every rendering of a TID decodes back to the same TID. A TID
  has exactly two spellings: the canonical 19-ASCII-decimal full TID of
  [SB-0.2], used in every queue name, and the encoded TID, used wherever a
  shorter rendering is displayed. The encoded TID is the TID's integer value
  in Crockford base32 (alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ`), most
  significant digit first, zero-padded to exactly 13 characters, with no
  checksum and no separators. TIDs lie in SimpleBroker's exact message-ID
  range, 0 through 2^63 − 1, so the first character is `0` through `7`; the
  encoding is a bijection on that range, carries the low 12 counter bits in
  full, and, being fixed-width, sorts in TID order. Producers emit
  uppercase. A decoder accepts either case, including mixed case, and
  rejects any other length, any character outside the alphabet (including
  `I`, `L`, `O`, `U`, and `-`, in either case), and a first character above
  `7`. Case is canonicalized at the input boundary by one function,
  `weft/commands/submission.py::normalize_tid`, which every flexible TID
  lookup boundary passes through (the `task` command resolvers, `weft
  result`, the Manager stop and status selectors, the prune `--task`
  selector, and `WeftClient.task`) and which alone calls the decoder; the
  strict boundaries, `runtime_liveness` (a canonical full TID only) and
  `task tid --reverse` (a full TID, which it encodes), are the stated
  exceptions and accept no encoded TID: it
  decodes an encoded argument, in any case, to the full TID before any
  other code sees it, so code past the boundary handles only the full TID
  and the uppercase encoding. The process-title `tid_short` segment,
  the task-log `tid_short` field, the runtime-snapshot `short` field, and the
  operational-log `manager_tid_short` field carry the encoded TID; they are
  display values, never resolution authority. Weft has no lossy TID form, so
  no TID input is ambiguous and no command searches retained queues to
  resolve one. `weft/helpers/__init__.py::encode_tid` and
  `weft/helpers/__init__.py::decode_tid` own the two directions; no producer
  formats the encoding itself.
```

### [OBS.6] — one sentence

Replaced span: opens "Multiple rows, including equivalent snapshots" and
closes "require a valid snapshot." Clause audit: "short-ID resolution"
retires; the payload-free discovery rule and the valid-snapshot rule stay.

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
Multiple rows, including equivalent snapshots, are ordered history, not
an invariant violation. Names-only discovery reads no payloads; all
runtime evidence consumers require a valid snapshot.
```

### [OBS.7] — unchanged

"Process-title segments are sanitized for shell-safe use" stays true: the
sanitized segments are context, name, status, and details. The TID segment
is generated, which [OBS.8] now says.

### [OBS.8] — replace the body line

Replaced span: the single line "Process titles stay within the allowed
character vocabulary".

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
Process titles stay within the allowed character vocabulary. The `tid_short`
segment is generated rather than sanitized; its Crockford base32 alphabet lies
inside that vocabulary.
```

### [OBS.4]–[OBS.8] backlink — insert as the first item under `## Related Plans`

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
- [TID display encoding](../plans/2026-11-02-tid-display-encoding-plan.md) replaces the lossy short form with the 13-character encoded TID under [OBS.4], [OBS.5], [OBS.6], and [OBS.8], and removes title corroboration from host liveness under [LIVENESS.R5].
```

### [OBS.4] — measured span line (added by Task 4)

Inserted as a new paragraph directly after the [OBS.4] paragraph (after
"initial Unix title.") and before "_Implementation mapping_:
`weft/core/process_title.py`, `weft/core/tasks/base.py`." Owner ruling 4
(2026-11-03): "This should not be part of CI regularly. This is something
to test and then document." The figures do not exist until Task 4 runs the
measurement, so this delta holds no Replacement block yet (a placeholder
block would fail the Replacement-sync check once the plan is `promoted`).
Task 4 adds a Replacement block targeting
`docs/specifications/07-System_Invariants.md` here with the exact inserted
text, in this form, one entry per
platform and, on macOS, per phase:

"Rewritable argv span of a spawned task process, measured once by the TID
display-encoding plan; documentation, not a gate: measured on <date>:
<platform>: <bytes rewritable>; …"

with the Windows entry in the form "measured on <date>: win32: readback
unavailable on this platform; the title budget is not measurable here by
this method" (the unchanged Windows argv measures no rewritable capacity).

and applies it to the spec in the same step, then reruns
`./.venv/bin/python bin/check-doc-paths`. "Weft does not probe capacity"
in [OBS.4] stays true: the measurement is a one-time manual run, not
runtime behavior.

Added by Task 4 on 2026-11-03 (macOS measured here; Linux and Windows
cannot be measured from the development Mac and are recorded as pending
owner runs; procedure in the Execution log):

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
Rewritable argv span of a spawned task process, measured once by the TID
display-encoding plan; documentation, not a gate: measured on 2026-11-03:
darwin (phase 1, initial writer): at least 1024 bytes rewritable (the whole
1,024-character probe survived, so this is a lower bound); measured on
2026-11-03: darwin (phase 2, after the padded handoff): 71 bytes rewritable;
linux: pending: owner-run; win32: pending: owner-run, recorded when run as
"measured on <date>: win32: readback unavailable on this platform; the title
budget is not measurable here by this method" if the readback shows the
unchanged argv. The procedure and the script text are in the plan's Execution
log.
```

### [LIVENESS.R5] — replace the bullet

Replaced span: the whole bullet, which reads, byte-exact:

```text
- **LIVENESS.R5**: Evidence authority follows
  `RunnerHandle.control.authority`; host identity requires `(pid, create_time)`
  with zombie rejection; extension probes are authoritative for `runner` and
  `external-supervisor`; titles corroborate and never prove a full TID. Probes
  are read-only and registration grants no control authority.
```

Clause audit: "titles corroborate and never prove a full TID" is the one
sentence in this file that names title corroboration as evidence (found by
`grep -n -i "corroborat" docs/specifications/*.md`; the other hit is
[CC-3.2]); it is replaced by owner ruling 5. Evidence authority, the
`(pid, create_time)` requirement, zombie rejection, extension authority,
read-only probes, and registration granting no control authority are
unchanged.

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
- **LIVENESS.R5**: Evidence authority follows
  `RunnerHandle.control.authority`; host identity requires `(pid, create_time)`
  with zombie rejection; extension probes are authoritative for `runner` and
  `external-supervisor`. A process title is observability only ([OBS.4]) and is
  never liveness evidence. Probes are read-only and registration grants no
  control authority.
```

### [CC-3.2] — replace the title sentence

Replaced span: in the paragraph that opens "Point-in-time liveness evidence
follows `control.authority`.", the last two lines, which read, byte-exact:

```text
is authoritative and host wrappers corroborate only. Process titles
corroborate and never prove a full TID.
```

Clause audit: "Process titles corroborate and never prove a full TID."
retires by owner ruling 5. "host wrappers corroborate only" is about host
wrappers of `runner` and `external-supervisor` extension probes, not
titles, and stays.

Replacement (`docs/specifications/01-Core_Components.md`):
```markdown
is authoritative and host wrappers corroborate only. A process title is
observability only ([OBS.4]) and is never liveness evidence.
```

Backlink, inserted as the first item under the `## Related Plans` that
follows [CC-3.5] (the second such heading in the file):

Replacement (`docs/specifications/01-Core_Components.md`):
```markdown
- [TID display encoding](../plans/2026-11-02-tid-display-encoding-plan.md) removes process-title corroboration from host-pid liveness evidence under [CC-3.2]; a title is observability only.
```

### [CLI-1.2.3] — restated, not removed

[CLI-1.2.3] also governs `task list`, `task status`, `task ping`,
`task stop`, and `task kill`, so the section stays and only its short-ID
text is restated.

Replaced span 1: opens "- `weft task tid` resolves short TIDs from namespace
names" and closes "…A full-ID lookup goes directly to that task's state
queue. Names alone grant neither control authorization nor
runtime/lifecycle evidence." (the bullet, the blank line, and the
"Short-ID candidates" paragraph). Clause audit: the candidate definition,
"ambiguous", "no command silently selects one", the batch "resolves all IDs
before writing any control message" rule (it existed to make ambiguity
batch-fatal; with arithmetic decoding there is nothing batch-fatal to
detect, and per-item failures keep the partition documented under
`task stop`/`task kill`), and "Derive shorts from suffixes" retire. "stored
`short` remains required payload shape, not resolution authority" moves to
[OBS.5]. The direct state-queue lookup and the names-grant-nothing rule
stay.

Replacement (`docs/specifications/10-CLI_Interface.md`):
```markdown
- `weft task tid` decodes an encoded TID to its full TID, returns a full TID
  unchanged, resolves PID lookups from newest-valid snapshots, and encodes a
  full TID with `--reverse`; decoding and encoding are arithmetic and read no
  queue.

Every CLI argument that names a task or a Manager by TID, except
`task tid --reverse` (which takes a full TID and encodes it), accepts the
canonical 19-ASCII-decimal full TID or the 13-character encoded TID of
[OBS.5], in any case, with surrounding whitespace and one leading `T`
removed (the `task` commands also keep their existing removal of repeated
leading `T`s); both spellings decode to the same TID. On the `task` commands any other
spelling, including the ten-digit short form of earlier releases, is not a
TID: the command fails with its invalid- or unknown-TID diagnostic (exit 2)
without a traceback and writes no control message. `weft result` keeps its
existing acceptance of other decimal strings. No TID input is ambiguous, so
there is no candidate search and no ambiguity error. A TID lookup goes directly to that task's state queue.
Names alone grant neither control authorization nor runtime/lifecycle
evidence.
```

Replaced span 2: opens "_Implementation mapping_: `weft/helpers/__init__.py::tid_short_form` owns"
and closes "…preflight resolution in `stop_tasks`, `kill_tasks`, and
`_task_control_result`." (the whole paragraph, which wraps across six
lines). Clause audit: the
derivation owner changes; namespace discovery for short IDs and the
collision rejection retire; the batch preflight retires with D4; the
suffix-bound newest-valid read stays with `weft/core/task_state.py`.

Replacement (`docs/specifications/10-CLI_Interface.md`):
```markdown
_Implementation mapping_: `weft/helpers/__init__.py::encode_tid` and
`weft/helpers/__init__.py::decode_tid` own the two TID spellings;
`weft/commands/submission.py::normalize_tid` is the one TID canonicalization
function and the only production caller of `decode_tid`;
`weft/commands/tasks.py::resolve_full_tid`,
`weft/commands/tasks.py::_command_tid`, `weft/commands/result.py`, and the
Manager and prune selector mappings in `weft/commands/manager.py` and
`weft/commands/prune.py` call it;
`weft/core/task_state.py` owns suffix-bound newest-valid snapshot reads.
```

Backlink, inserted as the first item under `## Related Plans`:

Replacement (`docs/specifications/10-CLI_Interface.md`):
```markdown
- [TID display encoding](../plans/2026-11-02-tid-display-encoding-plan.md) restates [CLI-1.2.3]: TID arguments accept the full or the 13-character encoded TID, and the retained-name candidate search and the ambiguity error are removed.
```

### [PY-2] — vocabulary in the `runtime_liveness` clause

Replaced span: opens "whitespace, a leading `T`, short IDs and zero are
rejected." and closes "short-TID lookup. Unlike terminal-snapshot
normalization, invalid input raises". Behavior unchanged: the method still
accepts only an unprefixed canonical full TID.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```markdown
whitespace, a leading `T`, the encoded TID of [OBS.5] and zero are rejected. Validate spelling
with `is_task_tid` and range with `normalize_exact_message_id`, requiring a
positive value; translate failure to `InvalidTID` before any probe, without
decoding another spelling. Unlike terminal-snapshot normalization, invalid input raises
```

Backlink, inserted as the first item under `## Related Plans`:

Inserted span (owner ruling 1, 2026-11-03; fresh-eyes finding (a)): one new
paragraph directly after the paragraph that opens "Full-TID
`client.tasks.status(tid)` and `client.task(tid).snapshot()`" and before
the paragraph that opens "Custody-only point snapshots use status". It
states the widened `WeftClient.task` input; `runtime_liveness` stays
strict.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```markdown
`client.task(tid)` accepts the canonical full TID or the 13-character encoded TID of [OBS.5], in any case, with surrounding whitespace and one leading `T` removed, and canonicalizes it at that boundary through `weft/commands/submission.py::normalize_tid`, the one TID canonicalization function, so the returned `Task` carries the full TID; any other spelling raises `InvalidTID`.
```

Backlink, inserted as the first item under `## Related Plans`:

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```markdown
- [TID display encoding](../plans/2026-11-02-tid-display-encoding-plan.md) widens `client.task(tid)` to accept the encoded TID through the one canonicalization function and rewords the rejected-spellings clause of the [PY-2] `runtime_liveness` input rule to name the encoded TID; `runtime_liveness` behavior is unchanged.
```

## Proposed Theory Delta

### [WT-2a.1] — replace the closing parenthetical

Replaced span: opens "argument, decodes back to the same TID ([IMMUT.3],
[SB-0.2]). (Owner" and closes "TID display-encoding plan.)". The invariant
sentence is unchanged; only the forward reference becomes a past one.

Replacement (`docs/program-theory.md`):
```markdown
  argument, decodes back to the same TID ([IMMUT.3], [SB-0.2], [OBS.5]).
  (Owner decision, 2026-11-02; the lossy ten-character form is refused in
  [ALT-WT-022].)
```

### [ALT-WT-022] — new entry, inserted after the [ALT-WT-021] entry and before "## Tensions and falsifiers [WT-7]"

Replacement (`docs/program-theory.md`):
```markdown
### [ALT-WT-022] A lossy TID short form

- Kind: tried and discarded (the ten-digit folded short form shipped under `2026-08-31-short-tid-derivation-plan`, retired, and was removed by `2026-11-02-tid-display-encoding-plan.md`)
- Disposition: rejected
- Candidate: A display form of the TID that cannot be decoded back to it,
  such as the TID's last ten decimal digits or the ten-digit fold
  `(grain + counter × 2_441_406) mod 10¹⁰`, together with a search over
  retained `weft.state.tasks.<tid>` names and an ambiguity error when one
  short form matches more than one task.
- Why plausible: Process titles have a fixed length budget, and a
  ten-character segment left more room for the task name than the
  nineteen-digit TID did.
- Reason: A rendering that is not an identity. A lossy form cannot decode
  back to its TID, so every reader needs a search to recover the identity it
  was shown. The search depends on which state queues retention has kept,
  and its answer can be ambiguous. That breaks [WT-2a.1] and "Keep an
  identity you already have" ([WT-4]): a title or a log line should carry
  the identity, not a pointer that must be resolved. The 13-character
  encoded TID fits the title budget without loss.
- Current consequence: Lossless display encoding defined by [OBS.5].
- Reconsider when: Never while [WT-2a.1] stands.
- Evidence: Owner, 2026-11-02: "the old lossy encoding is going away, as
  should its tests, associated search code, etc."; [OBS.5] history: the
  last-ten-digit form collided whenever two TIDs differed by a multiple of
  10¹⁰ nanoseconds, and the 2026-08-31 fold removed that collision but stayed
  non-unique and kept the [CLI-1.2.3] ambiguity error;
  `2026-11-02-program-theory-reconciliation-plan.md` §9 fork 2 (b) and
  audit row I-1.
```

No new [REV-WT-*] record: [REV-WT-004] already records the lossless clause
of [WT-2a.1] as new; this entry records the refusal that clause implies.
Owner ruling 2, 2026-11-03: "Agree" (no [REV-WT-*] record). The owner's
endorsement of the [ALT-WT-022] text is deferred to closeout (owner,
2026-11-03: "Not now"; Task 8).

## 5. Tasks

Tasks run in order. Nothing is committed until Task 8; the landing change is
one commit (strategy B).

0. **Preflight.**
   - Outcome: comprehension answers recorded; the plan's index row exists.
   - Do: write answers to the two comprehension questions in the execution
     log. Confirm `docs/plans/README.md` `## Status Index` has a `draft` row
     for this plan (the row is created with the plan file by whoever lands
     the plan; `tests/specs/test_plan_metadata.py` fails without it).
   - Stop if: either answer differs from the recorded one.
   - Done when: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q` passes.

1. **Spec-and-theory promotion slice (worktree only).**
   - Outcome: every `Replacement` block above is applied verbatim; plan
     `Status:` and its index row become `promoted`; the promotion baseline
     identifier is recorded as `b7eed9f9` + worktree plus the spec diff.
   - Files: `docs/specifications/07-System_Invariants.md`,
     `docs/specifications/01-Core_Components.md`,
     `docs/specifications/10-CLI_Interface.md`,
     `docs/specifications/14-Python_API_Surfaces.md`,
     `docs/program-theory.md`, this plan, `docs/plans/README.md`.
   - Constraints: replace only the quoted spans; insert backlinks at the top
     of each `## Related Plans` list (in `01-Core_Components.md`, the list
     after [CC-3.5]); insert the [PY-2] `client.task(tid)` paragraph at its
     stated position; place [ALT-WT-022] directly after the [ALT-WT-021]
     entry. The [OBS.4] measured span line is not applied here; Task 4 adds
     it with the measured figures.
   - Verify: `./.venv/bin/python bin/check-doc-paths` exits 0. With
     `promoted` status it checks that every `Replacement` block is contained
     in its target, and it checks backticked `docs/`, `skills/`, `bin/`, and
     `tests/` path claims in the specs; the new spec text cites no new path
     of those kinds.
   - Stop if: a block does not fit its span without changing adjacent
     sentences; record a deviation row and ask.

2. **RED: encoding contract tests.**
   - Outcome: a new `tests/system/test_tid_encoding.py` (new file,
     `pytestmark = [pytest.mark.shared]`, property tests additionally
     `pytest.mark.property`) that fails because the helpers do not exist.
   - Tests to write (all against `weft.helpers.encode_tid` /
     `weft.helpers.decode_tid`; no mocks):
     - property: for `n` in `st.integers(0, 2**63 - 1)` mapped through
       `simplebroker.format_message_id`, `decode_tid(encode_tid(t)) == t`,
       `len(encode_tid(t)) == 13`, the first character is in `"01234567"`,
       and `decode_tid(encode_tid(t).lower()) == t`;
     - property: for two such TIDs, `encode_tid(a) < encode_tid(b)` iff
       `int(a) < int(b)` (fixed width keeps TID order);
     - exhaustive low-order sweep: the 4,096 consecutive whole integers
       `g + c` for `c` in `range(4096)`, from
       `g = 1_760_000_000_000_000_000 & ~0xFFF`, encode to 4,096 distinct
       strings and each decodes back (the test builds integers; no Weft code
       splits a TID, §4 "TID opacity");
     - fixed vectors: `0 → "0000000000000"`, `1 → "0000000000001"`,
       `1837025672140161024 → "1JZKD3PKK0000"`,
       `1779000000000000001 → "1HC272C7PQ001"`,
       `2**63 - 1 → "7ZZZZZZZZZZZZ"` (computed for this plan with the
       reference algorithm below);
     - case: `decode_tid("1jzkd3pkk0000")` (lowercase) and
       `decode_tid("1jZkD3pKk0000")` (mixed case) both return
       `"1837025672140161024"`, the same TID as the uppercase vector;
     - `decode_tid` returns a canonical full TID unchanged;
     - rejection table, each raising `ValueError`: `encode_tid` of `""`,
       `"123"`, `"x" * 19`, `"²" * 19`, `"9223372036854775808"`, and the
       int `1`; `decode_tid` of `""`, `"0123456789"` (the old ten-digit
       form), 12 and 14 characters, `"1JZKD3PKK000I"`, `"…L"`, `"…O"`,
       `"…U"`, their lowercase forms `"1jzkd3pkk000i"`, `"…l"`, `"…o"`,
       `"…u"`, `"1JZKD-PKK0000"`, `"8000000000000"` (first character above
       `7`), a fullwidth-digit string of length 13, and
       `"9223372036854775808"`.
   - Do not move `test_invalid_expected_tid_preserves_exact_process_evidence`
     from `tests/system/test_short_tid.py`: owner ruling 5 removes the
     `expected_tid` parameter it exercises (Task 4), so an invalid TID can no
     longer reach the host and the test protects nothing; it is deleted with
     that file.
   - Verify RED: `./.venv/bin/python -m pytest tests/system/test_tid_encoding.py -q`
     fails at collection with `ImportError: cannot import name 'encode_tid'`.
     Record that exact assertion in the execution log.

3. **GREEN: constants and helpers.**
   - Files: `weft/_constants.py`, `weft/helpers/__init__.py`.
   - `weft/_constants.py`: add
     `TASKSPEC_TID_ENCODED_LENGTH: Final[int] = 13` (docstring: "Width of the
     Crockford base32 encoded TID [OBS.5].") beside
     `TASKSPEC_TID_SHORT_LENGTH`, which stays through this task because the
     retained `tid_short_form` (and the helpers import at
     `weft/helpers/__init__.py` line 39) still use it; Task 4 deletes both
     together. No compatibility code. Also add
     `TASKSPEC_TID_ENCODING_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"`.
     Update the `PROCESS_TITLE_MAX_LENGTH` sum to use the new constant, and set
     `PROCESS_TITLE_NAME_LENGTH = 17` (option A, owner ruling), so
     `PROCESS_TITLE_MAX_LENGTH` still computes to 71 and
     `PROCESS_TITLE_HANDOFF_LENGTH` to 72. Add an assertion-free docstring
     line on the name constant: "17 so the 13-character encoded TID fits the
     71-character title [OBS.4]."
     Tests that pin the 20-character name limit follow the constant:
     `tests/tasks/test_task_observability.py` imports
     `PROCESS_TITLE_NAME_LENGTH` (line 29) and builds its exact-maximum
     name from it (line 1095), so they stay green; grep
     `tests/ -e "n\" \* 20" -e "20 characters"` for any literal and change it to 17.
   - `weft/helpers/__init__.py`: add `encode_tid` and `decode_tid` directly
     where `tid_short_form` is; keep `tid_short_form` for this task only so
     the tree still imports. Reference algorithm (the contract, not a
     suggestion to generalize):

     ```python
     def encode_tid(tid: str) -> str:
         if not is_task_tid(tid):
             raise ValueError("tid must be a 19-digit decimal task ID")
         value = normalize_exact_message_id(tid)  # rejects >= 2**63
         chars = []
         for _ in range(TASKSPEC_TID_ENCODED_LENGTH):
             value, digit = divmod(value, 32)
             chars.append(TASKSPEC_TID_ENCODING_ALPHABET[digit])
         return "".join(reversed(chars))

     def decode_tid(text: str) -> str:
         if is_task_tid(text):
             normalize_exact_message_id(text)
             return text
         if not (isinstance(text, str) and len(text) == TASKSPEC_TID_ENCODED_LENGTH
                 and text.isascii()):
             raise ValueError("not a full or encoded task ID")
         value = 0
         for char in text.upper():  # canonicalize case at the boundary [OBS.5]
             digit = TASKSPEC_TID_ENCODING_ALPHABET.find(char)
             if digit < 0:
                 raise ValueError("not a full or encoded task ID")
             value = value * 32 + digit
         return format_message_id(value)  # ValueError when >= 2**63
     ```

     `format_message_id` comes from `simplebroker`; `normalize_exact_message_id`
     from `.message_ids`. Docstrings cite
     `docs/specifications/07-System_Invariants.md [OBS.5]` and
     `docs/specifications/10-CLI_Interface.md [CLI-1.2.3]`. Neither function
     strips whitespace or a leading `T`; callers keep their own prefix rules.
     `decode_tid` is the one case-canonicalization point, called in
     production only by `weft/commands/submission.py::normalize_tid`, the
     one canonicalization function, which the flexible boundaries call (§4
     "Case canonicalization at the input boundary" and "One
     canonicalization function, one decoder"); `encode_tid` emits uppercase only, and no other
     Weft code folds case.
   - Not allowed: Crockford's I/L→1 and O→0 aliases (one spelling per value,
     case aside), a checksum, a class, a second module.
   - Done when: Task 2 tests pass.

4. **Switch every producer and validator; delete the fold.**
   - RED first, in this order, then implement:
     - `tests/liveness/test_host.py` (owner ruling 5, 2026-11-03: "I don't
       think we need corroboration now, because the short form is
       lossless. Remove."): delete
       `test_host_inspection_uses_process_title_only_as_corroboration` and
       its parametrization, and the `tid_short_form` import. The title
       cases the plan carried for it (the encoded title →
       `identity_match_title_match`, the prior-version title
       `weft-project-6174316406:consumer:running` and the lowercase encoded
       title added on 2026-11-03 → `identity_match_title_unconfirmed`, and
       `"python worker.py"`) now test nothing: the host reads no title, so
       every one of them would return `("live", "identity_match")` and no
       mutation of the formatter or the codec could make them fail. In
       `test_host_inspection_accepts_matching_non_zombie_identity`, add
       `assert observation.reason == "identity_match"` beside the existing
       evidence assertion, and give that test's existing fake process a
       `cmdline()` method that raises `AssertionError("title must not be
       read")`. It already passes at baseline (the call passes no
       `expected_tid`), so it is a guard, not a RED test. The mutation it
       catches is a title read reintroduced on that path: the host's title
       read catches `AttributeError` (with `psutil.AccessDenied`,
       `psutil.Error`, and `OSError`), so a fake without `cmdline` would let
       a restored read pass silently, but it does not catch
       `AssertionError`, so the restored read fails the test. No new test. The
       rejection and absent/unresolved tests stay unchanged; they prove
       every non-`live` outcome is decided before any title could be read
       (D14).
     - `tests/tasks/test_task_observability.py`: lines 538 and 580 use
       `encode_tid`; the line 538 prefix expectation names the contract's
       truncated name,
       `f"…:{'observability-task'[:PROCESS_TITLE_NAME_LENGTH]}:"`
       (`observability-task` is 18 characters, so the title carries
       `observability-tas`; hidden coupling 7); the regex at line 585 changes `-[0-9]+:` to
       `-[0-9A-HJKMNP-TV-Z]{13}:`; the exact-maximum test at lines
       1080–1105 is unchanged in code (its name comes from
       `PROCESS_TITLE_NAME_LENGTH`) and proves the real formatter still
       reaches `PROCESS_TITLE_MAX_LENGTH` with a 17-character name and a
       13-character TID; the literal 71 is pinned by the property test
       below.
     - `tests/core/test_process_title.py` line 208: `obj.tid_short` becomes
       a 13-character value such as `"1HC272C7PQ001"`.
     - new property test in `tests/system/test_tid_encoding.py`: build a
       `BaseTask` subclass with `object.__new__` exactly as
       `tests/core/test_process_title.py` lines 203–209 do, and for
       Hypothesis-drawn names and details (arbitrary text up to 40
       characters), context paths `"/tmp/" + <text without "/" or NUL>`, statuses from `PROCESS_TITLE_STATUSES`, and TIDs from
       `taskspec_tid_strings()` (set `obj.tid_short = encode_tid(tid)`, since
       `object.__new__` skips `BaseTask.__init__`), assert
       `len(title) <= PROCESS_TITLE_MAX_LENGTH`, `PROCESS_TITLE_MAX_LENGTH == 71`,
       and `decode_tid(title.split(":", 1)[0].rsplit("-", 1)[1]) == tid`. This
       proves the formatter never truncates or sanitizes the TID segment; the
       `__init__` wiring (base.py line 274) is proven by the real-subprocess
       test below, which fails if `tid_short` is not the encoding.
     - move `test_consumer_publishes_and_matches_new_short_title` from
       `tests/system/test_short_tid.py` into the new file as
       `test_consumer_publishes_and_matches_encoded_title`: same real
       subprocess and `setproctitle` script, expecting
       `f"-{encode_tid(task.tid)}:encoded-title:running"` (rename the
       task from `short-form-test` to `encoded-title`, 13 characters and so
       within the 17-character limit, in both the TaskSpec and the script),
       and `rows[-1]["short"] == encode_tid(tid)`; drop the script's
       `inspect_host_process` call, its `weft.liveness.host` import, and the
       platform-specific reason assertion (owner ruling 5: the host no
       longer reads titles, so the test proves only that the producer
       publishes the full encoding), and drop its trailing comment and the
       two statements that rewrite `short` to `tid[-10:]` and resolve by
       short form.
     - producer-boundary assertions, extending existing tests rather than
       repeating the codec tests at each caller. Each must fail if its
       producer publishes a truncated encoding such as
       `encode_tid(tid)[:10]`:
       - default pipeline name: in `tests/core/test_pipelines.py`, beside
         `test_pipeline_compiler_assigns_stable_child_tids_by_stage_and_edge_name`
         and reusing its fixtures, compile a pipeline payload with no
         `name` and assert
         `decode_tid(compiled.runtime.pipeline_name.removeprefix("pipeline-")) == compiled.runtime.pipeline_tid`
         (no existing test covers the default name;
         `weft/core/pipelines.py` line 411);
       - `manager_tid_short` from the Manager: extend
         `tests/core/test_manager.py` line 1856 with
         `decode_tid(event["manager_tid_short"]) == event["manager_tid"]`
         (producer `weft/core/manager.py` line 808, `self.tid_short`);
       - `manager_tid_short` from TaskMonitor: extend
         `tests/core/test_task_monitoring.py` line 422 with
         `decode_tid(cycle["manager_tid_short"]) == cycle["manager_tid"]`
         (producer `weft/core/monitor/task_monitor.py` line 4737).
       `tests/core/test_serve_log.py` line 42 supplies `manager_tid_short`
       itself, so its edit is mechanical and proves no producer.
     - not a test: the one-time rewritable span measurement (§4
       "Rewritable span measurement"; owner ruling 4, 2026-11-03: "This
       should not be part of CI regularly. This is something to test and
       then document."). Run the script once on macOS, Linux, and Windows
       as described there, record each printed line in the execution log
       by date, platform, phase, Python version, and commit, then add the
       "[OBS.4] — measured span line" Replacement block to the delta with
       the figures ("measured on <date>: <platform>: <bytes rewritable>"),
       apply it to `docs/specifications/07-System_Invariants.md`, and run
       `./.venv/bin/python bin/check-doc-paths`. No test file, no CI step,
       no gate.
   - Then implement, replacing each `tid_short_form` call with `encode_tid`:
     `weft/core/tasks/base.py` line 274; `weft/core/pipelines.py` line 411;
     `weft/core/monitor/task_monitor.py` line 4737; `weft/commands/events.py`
     line 147; `weft/commands/tasks.py` lines 185 (inside `task_tid`'s `reverse`
     branch, which must keep returning `None` for invalid input: catch
     `ValueError`, since `encode_tid` now also rejects values at or above
     2^63), 974, 1213, 1580, 1700;
     `weft/commands/system.py` line 1182 (validator);
     `weft/commands/_task_snapshot_reducer.py` lines 193 and 520;
     `weft/commands/task_monitor.py` lines 276 (validator) and 416;
     `weft/cli/app.py` line 1809. Validator sites keep the
     `try: encode_tid(x) except ValueError` shape.
   - Mechanical test edit: in the 23 remaining test files that import
     `tid_short_form` for fixture values (execution log list), replace the
     import and calls with `encode_tid`. Do not change the fixtures that
     write `"short": tid[-10:]` (`tests/core/test_service_helper_connections.py`
     line 85, `tests/core/test_heartbeat_helpers.py` line 296,
     `tests/cli/test_cli_manager.py` line 553): they prove `short` is display
     metadata whose value no reader trusts.
   - Delete `tid_short_form` from `weft/helpers/__init__.py`, the
     `TASKSPEC_TID_SHORT_LENGTH` import there, the
     `TASKSPEC_TID_SHORT_LENGTH` definition in `weft/_constants.py` (kept
     through Task 3), and `tests/system/test_short_tid.py`.
     Update the `weft/helpers/__init__.py` module docstring spec list if it
     changes.
   - Remove title corroboration (owner ruling 5; D14):
     `weft/liveness/host.py::inspect_host_process` drops the keyword-only
     `expected_tid` parameter, the `cmdline()` read, the title regex, both
     `identity_match_title_*` reasons, and the `re` and `tid_short_form`
     imports; after the scoped `(pid, create_time)` identity matches a
     non-zombie process it returns `("live", "identity_match")`, the
     outcome the `expected_tid is None` path returns today. Its docstring
     states that the verdict rests on the scoped identity alone and that
     the title is observability only, citing [LIVENESS.R5] and [CC-3.2].
     `weft/liveness/analysis.py::probe_runtime_liveness` line 107 drops
     `expected_tid=tid`; the function keeps its `tid` parameter (its two
     production callers and two test calls pass it, and dropping it is a
     separate signature change this plan does not make). No other caller
     passes `expected_tid` to the host (`grep -rn "expected_tid" weft
     extensions integrations` finds, besides the host and that call, only
     the unrelated `expected_tid` of
     `weft/liveness/policy.py::valid_tid_state_payload` and its two callers
     in `weft/core/task_state.py`, which stay; the monkeypatched fakes in
     `tests/liveness/test_analysis.py`, `tests/core/test_manager.py`,
     `tests/commands/test_run.py`, `tests/commands/test_manager_commands.py`,
     and `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py`
     accept the narrower call unchanged).
   - Stop if: any reader is found that uses the stored `short` value for
     resolution or equality; a field rename looks necessary; a mock is
     needed to make a title test pass; or a reachable case is found in
     which the title is the only liveness evidence (for example a caller
     that passes `expected_tid` and reads `.reason`, or a path that reads
     the title when `create_time` cannot be read). The plan's reading of
     the classifier found none (D14); if one appears, report it to the
     owner instead of removing the regex.
   - Verify: `./.venv/bin/python -m pytest tests/system/test_tid_encoding.py tests/liveness tests/core/test_process_title.py tests/core/test_pipelines.py tests/core/test_manager.py tests/core/test_task_monitoring.py tests/core/test_serve_log.py tests/tasks/test_task_observability.py tests/tasks/test_task_execution.py tests/commands/test_run.py tests/commands/test_manager_commands.py extensions/weft_macos_sandbox/tests -q`, `grep -rn "identity_match_title" weft extensions integrations tests` prints nothing, and `grep -n "expected_tid" weft/liveness/host.py weft/liveness/analysis.py` prints nothing.

5. **Remove candidate discovery and the ambiguity path.**
   - RED/cleanup in `tests/commands/test_tid_mapping_contracts.py`: delete
     the `collision` fixture, `test_short_collision_names_all_candidates`,
     `test_ambiguous_batch_sends_no_control`, and
     `test_short_resolution_uses_namespace_even_with_malformed_only_rows`
     (its remaining assertions, that a malformed-only namespace yields no
     mapping, move into a renamed
     `test_malformed_only_namespace_yields_no_mapping`). In
     `test_resolution_derives_short_and_ignores_newer_malformed_mapping`,
     drop the short-resolution line and rename it
     `test_mapping_ignores_newer_malformed_row`. Rewrite
     `test_full_selector_excludes_other_colliding_task` as
     `test_full_tid_filter_excludes_other_tasks` using two distinct full
     TIDs and `reduce_task_event` with `tid_filters={first}` (no
     `_resolve_tid_filters`). Replace the module docstring
     "Registry-backed short resolution and batch preflight [CLI-1.2.3]" with
     "TID selector and mapping contracts [CLI-1.2.3]." Add
     `test_encoded_selector_decodes_without_reading_state` (green after
     Task 6): with no
     `weft.state.tasks.*` queue present,
     `task_cmd.resolve_full_tid(encode_tid(full)) == full`,
     `task_cmd.resolve_full_tid(encode_tid(full).lower()) == full`, and
     `task_cmd.resolve_full_tid("T" + encode_tid(full)) == full`.
   - `tests/commands/test_task_snapshot_reducer.py` lines 133–136: delete the
     assertion that a short-form filter matches.
   - `tests/commands/test_task_commands.py` line 607: rename to
     `test_canonical_task_tid_returns_full_tid_and_rejects_multiple_selectors`
     and use `encode_tid(full_tid)` in place of `"00128"`.
   - Implement:
     - `weft/commands/system.py`: delete `_read_tid_states` and
       `_resolve_tid_filters`; drop the now-unused
       `list_task_state_tids` import if nothing else uses it there.
     - `weft/commands/tasks.py::resolve_full_tid` becomes
       `resolve_full_tid(raw: str) -> str | None`: strip, `lstrip("T")`,
       return `submission.normalize_tid(candidate)`, or `None` on
       `InvalidTID` (the one canonicalization function, §4 "One
       canonicalization function, one decoder"; `normalize_tid` gains its
       `decode_tid` call in Task 6, so until then this path accepts only the
       full TID, and the encoded-selector test below goes green in Task 6,
       not here). Update its
       10 remaining call sites (the 11th, line 2568, is the preflight loop
       deleted below; drop the `ctx`/`broker` arguments); the
       `or tid.strip().lstrip("T")` fallbacks stay.
     - `_command_tid(raw: str) -> str`: strip, `lstrip("T")`, then
       `submission.normalize_tid(candidate)`, re-raising its failure as
       `InvalidTID(f"Invalid task ID: {raw!r}")`; no local decoding or case
       folding. Drop the unused
       `context` and `allow_unknown_full` parameters at its four call sites
       (lines 2456, 2505, 2537, 2578).
     - Delete the ambiguity preflight loop and its comment in
       `_task_control_result` (lines 2565–2568).
     - `weft/commands/_task_snapshot_reducer.py::reduce_task_event`: the
       filter test becomes `tid not in tid_filters`.
     - `weft/core/task_state.py`: no edit. Its module docstring makes no
       short-ID claim, and its [CLI-1.2.3] citation stays true (suffix-bound
       newest-valid reads); `list_task_state_tids` stays (LivenessMonitor,
       `latest_task_state_rows` in the same module, and `WeftTestHarness`
       use it).
   - Stop if: a production caller of `_resolve_tid_filters` or
     `_read_tid_states` appears; or removing the preflight changes a
     batch-control test other than the deleted ambiguity tests.
   - Verify: `./.venv/bin/python -m pytest tests/commands/test_tid_mapping_contracts.py tests/commands/test_task_commands.py tests/commands/test_task_snapshot_reducer.py tests/commands/test_status.py -q`.

6. **CLI boundary: accept both spellings everywhere a TID is an argument.**
   - RED first, black-box through `tests.conftest.run_cli` with
     `WeftTestHarness` (real Manager, real queues; no monkeypatching of
     resolvers), in `tests/cli/test_cli_list_task.py`:
     - `test_task_tid_reverse` expects `encode_tid(tid)`;
     - new `test_encoded_and_full_tid_arguments_name_the_same_task`,
       parametrized by spelling over `full`, `encoded`, `encoded-lower`,
       `encoded-mixed`, and `T-encoded` (the argument is `tid`,
       `encode_tid(tid)`, `encode_tid(tid).lower()`, that encoding with
       every second letter lowercased, or `"T" + encode_tid(tid)`; the
       lowercase and mixed cases assert their argument differs from
       `encode_tid(tid)`, so they cannot pass vacuously). Boundary proof
       (cross-family round of 2026-11-03, F1): the status assertion alone
       does not prove where canonicalization happens, because
       `cmd_task_status` forwards the candidate through `task_snapshot` to
       `_task_status_record`, which calls `resolve_full_tid` again and would
       repair a `_command_tid` that returned the raw candidate; so one
       existing command test in `tests/commands/` gains direct assertions
       that `_command_tid` returns the full 19-digit TID for the uppercase,
       lowercase, mixed-case and `T`-prefixed encodings (no Manager, no
       mocks), the mutation being `_command_tid` returning `candidate`
       unchanged. Each case
       submits its own isolated task with a known nonempty output (for
       example `echo` of a per-case sentinel), waits for completion, asserts
       `task status ARG --json` reports `"tid": tid` and `task tid ARG`
       prints `tid`, and then calls `result ARG` exactly once and asserts the
       sentinel. One read per task: `cmd_result` consumes
       (`await_task_result` → `_iter_consuming_result` →
       `_claim_result_rows` in `weft/commands/_result_wait.py`), so a second
       spelling against the same task would find no row. Consuming result
       semantics are unchanged;
     - new `test_tid_argument_probes` (adversarial floors, each asserting
       exit code 2 and `"Traceback" not in err`, with a one-line diagnostic
       naming the input): `task status 0123456789` (old ten-digit form),
       `task status 1JZKD3PKK000I`, `task status 8000000000000`,
       `task status ""`, `task tid --reverse 9999999999999999999`,
       `task stop 0123456789` (also assert `T0123456789.ctrl_in` holds no
       message, peeked through the harness context, per the [CLI-1.2.3]
       rule that an unknown TID is rejected before any control write), and `result 1JZKD-PKK0000`.
     - new structural test (owner ruling 1, 2026-11-03: "Sure, widen. Make
       sure they all use the same canonicalization function and the same
       decoder function."), in `tests/system/test_tid_encoding.py`:
       `test_tid_arguments_share_one_canonicalizer_and_one_decoder`. It
       parses source under `weft/` with `ast` (no imports, no mocks) and
       makes bounded checks only; it is not a general static analyzer
       (cross-family round 1 on the six rulings, 2026-11-03, F1, removed
       the package-wide substring heuristics, which flagged unrelated case
       folds such as status names and still missed a renamed decoder):
       (a) Per boundary. For each designated boundary function, located by
       module path and qualified name, with its TID parameter named:
       `weft/commands/tasks.py::resolve_full_tid` (`raw`),
       `weft/commands/tasks.py::_command_tid` (`raw`),
       `weft/commands/result.py::_normalize_tid` (`raw_tid`),
       `weft/commands/manager.py::cmd_manager_stop` (`tid`),
       `weft/commands/manager.py::cmd_manager_status` (`tid`),
       `weft/commands/prune.py`'s shared prune capability, located not by
       name but as the function whose body binds the `task_filters=`
       keyword from `task` (`cmd_system_prune` today; `_prune_families`
       after series plan 11's extraction, with no edit to this inventory;
       the check fails if zero or more than one function in `prune.py`
       binds that keyword), checking the `--task` selector mapping over
       `task` there (the loop or comprehension target bound by iterating
       `task`), and `weft/client/_client.py::WeftClient.task`
       (`tid`). The function's AST contains a call to `normalize_tid` (as a
       name or an attribute) whose argument expression contains a `Name`
       node for that parameter (the `task` commands pass it inline, as
       `normalize_tid(raw.strip().lstrip("T"))`, not through an
       intermediate name), and contains no `.upper()`, `.lower()`, or
       `.casefold()` call whose receiver expression contains that `Name`
       and no `int(...)` call whose argument contains it. Other case folds
       in the same function (for example on a status string) are not
       inspected.
       (b) One decoder call site. Counting calls to `decode_tid` (as a name
       or an attribute) across every `.py` file under `weft/` finds exactly
       one, and it lies inside
       `weft/commands/submission.py::normalize_tid`.
       (c) Stated limit. A second decoder under another name with its own
       alphabet would pass (a) and (b); that is the limit of a static
       check of this size, and the test's docstring says so. The behavior
       tests (the CLI equivalence test, the direct `_command_tid`
       assertions, the Manager and prune selection tests below, the client
       case table, and the codec's fixed vectors in Task 2) are what catch
       a decoder that decodes differently or a normalized value that never
       reaches selection.
       RED: before the implementation below, (b) fails with zero
       `decode_tid` calls and (a) fails for every boundary except
       `WeftClient.task`, which already calls `normalize_tid(tid)`; record
       the exact failing assertion in the execution log. Mutations the
       bounded checks catch, each checked once by hand and recorded: a
       direct extra `decode_tid` call (for example from `_command_tid`),
       caught by (b); a local case fold on the TID parameter (a
       `raw_tid.upper()` in `result._normalize_tid`), caught by (a); and a
       boundary that stops calling `normalize_tid` (`cmd_manager_status`),
       caught by (a). Discarding `normalize_tid`'s result is not caught
       here; the selection tests below catch it. It is a standing gate over
       the listed boundaries (the +P in the class paragraph): a new
       flexible TID lookup boundary adds itself to (a) in the same change.
     - selection tests for the widened surfaces (cross-family round 1 on
       the six rulings, 2026-11-03, F2), each extending an existing test
       and observing selection by the canonical full TID, not the call;
       the mutation each must catch, checked once by hand and recorded, is
       a boundary that calls `normalize_tid` and discards its result. Each
       uses the uppercase encoding only: case handling is shared through
       `normalize_tid` and covered by the codec vectors and the client
       table, so no lifecycle machinery is added for codec cases.
       - `tests/commands/test_manager_commands.py::test_stop_command_writes_stop_for_active_manager`:
         parametrize the `tid=` argument of `cmd_manager_stop` by spelling
         over `full` (`tid`) and `encoded` (`encode_tid(tid)`); the
         existing assertion that `T{tid}.ctrl_in` (the canonical full TID's
         control queue) holds `STOP` is the selection proof, and with a
         discarded result the encoded spelling selects no registry record
         and writes no `STOP` there.
       - `tests/cli/test_cli_manager.py::test_manager_start_and_status`:
         after the existing `manager status <tid> --json` call, add
         `manager status <encode_tid(tid)> --json` and assert exit 0 and
         that the status row's `"tid"` is the full `tid` and its
         `"status"` is `"active"`; with a discarded result the command
         reports the Manager not found.
       - `tests/commands/test_retention_prune.py::test_force_deletes_active_ping_in_selected_scope`:
         add a second, unselected task's `ctrl_in` ping row, and, after the
         existing `run_retention_prune` assertions, call
         `cmd_system_prune(family="task-local", context=ctx.root,
         apply=True, force=True, min_age=0, task=[encode_tid(tid)])` on a
         freshly written ping for `tid`; assert `result.deleted == 1`, the
         selected task's new ping is gone, and the unselected task's ping
         remains. With a discarded result the encoded selector matches no
         task and deletes nothing.
         Client path: once series plan 11's `client.system.prune` exists,
         the same arrangement runs a second time through
         `client.system.prune(family="task-local", apply=True, force=True,
         min_age=0, task=[encode_tid(tid)])` with the same three
         assertions, so selection is observed through both adapters; until
         plan 11 lands the client case is skipped with the reason "needs
         plan 11's client.system.prune" (a visible skip, never a silent
         pass).
   - Implement (each boundary is the input boundary of §4 "Case
     canonicalization at the input boundary" and calls the one
     canonicalization function, §4 "One canonicalization function, one
     decoder"; no boundary upper-cases or decodes separately, and no code
     after it handles an encoded or lowercase spelling):
     - `weft/commands/submission.py::normalize_tid`, the one
       canonicalization function: keep `strip()` and the single
       `removeprefix("T")`, then `decode_tid`; `InvalidTID` on
       `ValueError`. Docstring: "The one TID canonicalization function
       [OBS.5], [CLI-1.2.3]; every flexible TID lookup boundary passes
       through it; the strict `runtime_liveness` and `task tid --reverse`
       boundaries take a full TID only and do not." `tests/commands/test_submission.py` already pins
       one-prefix removal and the rejection of `"TT…"`, `"123"`, fullwidth
       digits, and `"9999999999999999999"`; those tests must stay green
       unchanged.
     - `weft/commands/result.py::_normalize_tid`: keep the empty check;
       return `normalize_tid(raw_tid)` and, on `InvalidTID`, fall through
       to the existing strip, single `removeprefix("T")`, and digits-only
       rule unchanged. `tests/commands/test_result.py` pins `"123"` and
       `tests/cli/test_cli_result.py` pins `weft result 999` and
       `invalid task id 'not-a-tid'`; this plan does not tighten them.
     - `weft/commands/manager.py::cmd_manager_stop` (when `tid` is given)
       and `cmd_manager_status`: replace `tid` with `normalize_tid(tid)`,
       and on `InvalidTID` leave it unchanged (existing not-found path).
     - `weft/commands/prune.py`: map each `task` selector the same way
       before it reaches `weft/core/pruning`, at the one point where the
       selectors become `task_filters` (today
       `task_filters=tuple(task or ())` inside `cmd_system_prune`,
       prune.py:207). Cross-plan coupling (owner's outside reviewer,
       2026-11-03; hidden coupling 8): series plan 11 moves the body of
       `cmd_system_prune`, from validation through the `SystemPruneResult`
       return, into a private `_prune_families(...)` that both the CLI
       adapter and the new `client.system.prune` call. Normalization lives
       in that shared capability whichever plan lands first: if plan 11
       lands first, this plan adds `normalize_tid` to the `task_filters`
       construction inside `_prune_families`; if this plan lands first,
       plan 11's extraction carries the normalized construction into
       `_prune_families` unchanged. In neither order does a wrapper
       (`cmd_system_prune` or `client.system.prune`) normalize on its own,
       so the client path never bypasses normalization.
     - `weft/client/_client.py::WeftClient.task`: no code change; it
       already calls `submission.normalize_tid` and so gains the encoding
       with it (D8; the [PY-2] `client.task(tid)` paragraph). Add one
       client test in `tests/core/test_client.py` that `client.task(x).tid`
       is the full TID for the uppercase, lowercase, mixed-case, and
       `T`-prefixed encodings, using the alphabet-bearing vector
       `1837025672140161024 → "1JZKD3PKK0000"` (the mixed-case row is that
       encoding with every second letter lowercased, and the lowercase and
       mixed-case rows assert their argument differs from the uppercase
       encoding, so they cannot pass vacuously).
     - `weft/commands/tasks.py::cmd_task_tid`: the `--reverse` branch raises
       `InvalidTID` unless `encode_tid(candidate)` succeeds, and returns the
       candidate; the CLI keeps printing `encode_tid(result)`.
     - `weft/cli/app.py` help strings: "Task ID or short ID" (lines 1619,
       1687) and "Short or full TID" (line 1788) become "Full or encoded
       TID"; "Return short TID for a full TID" (line 1796) becomes "Return
       the encoded TID for a full TID".
   - Stop if: a surface needs a different exit code than its current
     invalid/unknown path to satisfy a probe (record it in the deviation log
     instead of changing the exit mapping); or importing
     `weft/commands/submission.py` from `tasks.py`, `result.py`,
     `manager.py`, or `prune.py` creates an import cycle (house style
     forbids a late import to break it; report instead).
   - Verify: `./.venv/bin/python -m pytest tests/system/test_tid_encoding.py tests/cli/test_cli_list_task.py tests/commands/test_result.py tests/commands/test_submission.py tests/commands/test_tid_mapping_contracts.py tests/commands/test_realtime_events.py tests/core/test_client.py -q`.

7. **Documentation sync.**
   - `README.md`: lines 781–782 become
     "- **Encoded TID**: `1JZKD3PKK0000` (13 Crockford base32 characters; decodes
     back to the full TID per [OBS.5](docs/specifications/07-System_Invariants.md))";
     the `ps` example at lines 918–919 becomes
     `weft-proj-1JZKD3PKK0000:mytask:running` and
     `weft-proj-1JZKD3PKK0400:manager:completed`; line 922 keeps
     its format line unchanged; line 848 drops the sentence "Short-ID
     resolution uses queue names;" so the paragraph continues "Exact
     deletion keeps newer concurrent publications intact. Status and control
     require valid snapshots …" (the final `Short-ID` grep gate in
     `## Verification` fails without this).
   - `CHANGELOG.md` `## [Unreleased]`: a `### Changed` entry stating the
     break per [ALT-WT-012]: titles and the `short`, `tid_short`, and
     `manager_tid_short` values now carry the 13-character encoded TID; CLI
     TID arguments and `WeftClient.task` accept the full or encoded TID in
     any case; the ten-digit short form, its search, and the ambiguity
     error are removed; scripts that match ten-digit shorts in `ps` output
     or JSON must switch to the encoded form; the liveness host no longer
     reads process titles (`weft.liveness.host.inspect_host_process` drops
     its `expected_tid` keyword and the `identity_match_title_match` and
     `identity_match_title_unconfirmed` reasons; a matched identity reports
     `identity_match`); a task still running under the previous version
     keeps its old title until it exits and is reported live as before.
   - `CLAUDE.md` and `AGENTS.md` need no edit (the `{tid_short}` placeholder
     is unchanged).
   - Done when: `grep -rn "Short TID\|short TID\|short ID" README.md weft/cli docs/specifications` prints nothing.

8. **Traceability reconciliation, gates, and landing.**
   - Update `Spec:` docstrings that cite [OBS.5]/[CLI-1.2.3] in touched
     functions so each names the promoted section; the [CLI-1.2.3] and
     [OBS.5] mapping claims and the code backlinks must agree.
   - Run every command in `## Verification` from the current tree and record
     results in the execution log.
   - Closeout obligations from `docs/program-theory.md` (Verification and
     Governing process lines): refresh audit row I-1 to `aligned` with the
     new evidence (the `tests/system/test_tid_encoding.py` round-trip
     property and the grep gate), run the keyed
     possession probe at class-5 completion, and obtain owner endorsement of
     [ALT-WT-022] (owner decision 6: deferred to closeout, owner,
     2026-11-03: "Not now"; still open until this step). The refreshed audit evidence lives in this plan: append an
     `Audit refresh (I-1)` entry to the execution log holding the refreshed
     I-1 row in the table's column format, with every other row carried
     forward by reference to the theory-versus-code audit table in
     `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (row I-1
     there). That completed plan is not edited. The probe key and result go
     in this plan's `## Review Log` ([WT-7]).
   - Land as one commit by explicit file-list staging against the canonical
     list in §3 "Files to modify" (which includes this plan,
     `docs/plans/README.md`, `tests/core/test_client.py`, and the three
     Task 6 selection-test files; no CI workflow file, owner ruling 4); flip `Status:` and the index row to `completed` in the same
     change; record the commit SHA as the promotion baseline identifier.

## 6. Testing Plan

- Harnesses: `WeftTestHarness` and `run_cli` for every CLI assertion;
  `broker_env` and real `Queue` instances for state rows; the real
  `inspect_host_process` for host identity; a real `setproctitle`
  subprocess for the published title; the real
  `BaseTask._format_process_title` for the budget; `ast` over the source
  tree for the structural canonicalization test. Mock only `psutil.Process`
  where `tests/liveness/test_host.py` already does to supply `create_time`
  and `status`.
- Do not mock: resolvers (`resolve_full_tid`, `decode_tid`), the formatter,
  queue reads, or the CLI exit mapping.
- Contracts each test protects:
  - round-trip and order properties over `[0, 2^63)` plus the
    consecutive-integer sweep and the case vectors: [OBS.5] bijection over
    whole integers and case-insensitive decoding;
  - title property and exact-maximum test: [OBS.4] budget of 71 with a
    17-character name, TID segment lossless and untruncated;
  - rewritable span measurement: not a test and not in CI (owner ruling
    4, 2026-11-03); a one-time script run once per platform whose figures
    go into the execution log and [OBS.4] ("measured on <date>:
    <platform>: <bytes rewritable>"), as lower bounds when the whole
    1,024-character probe survives;
  - structural test
    `test_tid_arguments_share_one_canonicalizer_and_one_decoder`: one
    canonicalization function (`normalize_tid`) and one decoder
    (`decode_tid`) for every TID-argument boundary, no local case fold on a
    TID argument (owner ruling 1); a standing gate (+P);
  - producer-boundary assertions (default pipeline name, both
    `manager_tid_short` producers): each producer publishes the complete
    encoding; the codec property alone cannot show this;
  - `tests/commands/test_task_commands.py`
    `test_canonical_task_tid_returns_full_tid_and_rejects_multiple_selectors`:
    selector exclusivity only. Its resolution is mocked
    (`monkeypatch.setattr(task_cmd, "resolve_tid", …)`), so switching its
    input to `encode_tid(full_tid)` does not make it a decoding test;
    decoding is proven by `test_encoded_selector_decodes_without_reading_state`
    and the CLI equivalence test;
  - host tests: liveness rests on the scoped `(pid, create_time)` identity
    alone, every non-`live` outcome is decided before any title could be
    read, and a matched identity reports `identity_match` with no title
    read (D13, D14; owner ruling 5). The real-subprocess test proves only
    that the producer publishes the full encoding in the title and the
    `short` row; it no longer calls the host;
  - CLI equivalence and probes: [CLI-1.2.3] restated text, adversarial
    floors 1 and 2 (no traceback; exit class 2), and D7;
  - deletion check: the grep gate in `## Verification` proves no test of the
    ambiguity path or the fold remains (the removed path has no test left).
- Tempting to skip, kept in scope: lowercase, mixed-case, and `T`-prefixed
  encodings at the `task` command boundary and `WeftClient.task`, the
  encoded selection tests for `manager stop`, `manager status`, and
  `system prune --task` (Task 6), and the
  hand-checked mutations of the structural test (the
  CLI's existing prefix rule meets a new alphabet that contains `T`; the
  first encoded character is always `0`–`7`, so stripping a leading `T` is
  unambiguous); the `--reverse` out-of-range traceback (hidden coupling 5).
- Out of scope for tests: Windows title readback (psutil reports the
  unchanged argv there; the existing platform branch stays).

## 7. Verification

Per-task commands are listed in each task. Final gates, from the repo root:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/system/test_tid_encoding.py tests/liveness tests/commands/test_tid_mapping_contracts.py tests/cli/test_cli_list_task.py tests/core/test_process_title.py tests/tasks/test_task_observability.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m pytest tests/specs -q
grep -rn "tid_short_form\|TASKSPEC_TID_SHORT_LENGTH\|_read_tid_states\|_resolve_tid_filters\|Ambiguous short TID\|Short-ID\|short-ID\|identity_match_title\|titles corroborate" weft tests integrations extensions bin docs/specifications README.md
grep -rn -i "ambiguous" tests/commands/test_tid_mapping_contracts.py tests/commands/test_task_commands.py
```

Success: every pytest, mypy, ruff, and `check-doc-paths` command exits 0;
both `grep` commands print nothing.

Three-OS note: CI runs the suite on `ubuntu-latest`, `macos-latest`, and
`windows-latest` with Python 3.12–3.14 (`.github/workflows/test.yml`,
unchanged by this plan). The title subprocess test asserts the published
title through `setproctitle.getproctitle()` on every platform and no
longer asserts a host reason. macOS exercises the padded deferred handoff
at the unchanged 72-byte length; Linux exercises the stock setter. The
span is not measured in CI (owner ruling 4): the one-time measurement
replaces the CI step this plan used to add, is run by the implementer once
each on macOS, Linux, and Windows as §4 "Rewritable span measurement"
describes, and is verified by its lines in the execution log and the
"measured on <date>: <platform>: <bytes rewritable>" entries in [OBS.4]. A
pass on one local OS is not the completion claim; the CI matrix is.

Runtime observation after landing: `ps ax | grep weft-` shows titles of the
form `weft-<ctx>-<13 chars>:<name>:<status>`, and `weft task tid <13 chars>`
prints the full TID of the same task.

## 8. Rollback

One commit; `git revert <sha>` restores the ten-digit form, the search, and
the ambiguity error. Rollback is safe in both directions:

- stored `short`/`tid_short` values written by either version are nonempty
  strings, valid for both versions' readers, and neither version resolves by
  the stored value;
- a task running across the revert keeps its title until exit; both
  versions' liveness hosts report it `live` (the new host as
  `identity_match` without reading the title, the reverted host as
  `identity_match_title_unconfirmed` because its regex does not match the
  encoded segment), so no verdict differs; the revert also restores the
  title regex and the `expected_tid` parameter;
- queue names never changed, so no queue data migrates either way.

Rollout: one coordinated release ([WT-2a.9]). No transition window, no dual
decoder. The only cross-version state is a live process title from the
previous version, which is a hint (D13). External consumers of
`tid_short`/`manager_tid_short` values or of ten-digit `ps` patterns are
told by the CHANGELOG. One-way doors: none; the change is reversible.

## 9. Out of Scope

- Renaming `short`, `tid_short`, `manager_tid_short`, the `tid_short`
  attribute, or the `{tid_short}` placeholder (a shape break with a
  restart hazard; not needed for losslessness; owner ruling 2026-11-03:
  field names stay).
- Accepting the encoded TID in Python-only strict surfaces such as
  `runtime_liveness` ([PY-2]). `WeftClient.task` is not one of them: it is
  widened (owner ruling 1).
- Unifying the leading-`T` rule: the `task` commands keep removing
  repeated leading `T`s (`lstrip("T")`) before calling `normalize_tid`,
  which removes one; narrowing them would be a new behavior change the
  rulings did not ask for.
- Dropping `probe_runtime_liveness`'s now-unforwarded `tid` parameter.
- `weft queue …` commands, which take queue names, not TIDs.
- [LIVENESS.R5] beyond its title clause; TID minting; the timestamp
  generator.
- A CI workflow step, test, or gate for the rewritable span (owner ruling
  4).
- Changing the default pipeline name shape beyond its encoded suffix.
- Tightening `weft result`'s existing digits-only acceptance.

## 10. Review Protocol

- Author family: Anthropic (Claude). Reviewer family: a different family via
  `skills/call-agent/SKILL.md` (Tier-1), reading this plan, its
  `## Proposed Spec Delta` and `## Proposed Theory Delta`, and the files in
  §3. Use the recommended prompt in
  `docs/agent-context/runbooks/writing-plans.md` §8, and ask the reviewer to
  check the application of the owner rulings under "Owner decisions" in §4
  (every item is ruled except item 6, the [ALT-WT-022] endorsement, which
  is deferred to closeout; the 2026-11-03 rulings 1–6 are applied: the
  widening through one canonicalization function and one decoder with its
  structural test, no [REV-WT-*] record, the one-time span measurement,
  the title-corroboration removal and its register class, and the excluded
  letters). The reviewer does not reopen ruled items; it checks that the
  plan applies them as quoted and reports any design fork they leave.
- Class 5+P with hardening: review of plan and delta completes before Task
  1; review of completed work before Task 8's commit, which is also the +P
  pre-landing different-family review.

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 18 corrections — host.py line span; only `expected_tid` caller (manager_runtime calls named); reducer filter line 196; `weft/core/task_state.py` dropped from edits (docstring makes no short-ID claim); D2 no-match outcome is `InvalidTID`; D8 drops nonexistent `task events` and notes 13-digit decimal inputs; test_host already asserts `live`; exact-maximum test wording; property test sets `tid_short` and names what it proves; moved consumer test's expected name; `task_tid` reverse branch keeps its `None` contract; 10 remaining `resolve_full_tid` call sites; stop probe peeks `T0123456789.ctrl_in`; README line 848 `Short-ID` sentence added to Task 7; mapping paragraph spans six lines; execution-log constant count and four line numbers; owner forks consolidated under "Owner decisions"; two fresh-eyes findings for the owner (`WeftClient.task` widening, title-regex scope reading); cross-family round 1: done (next entry)
- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass and the owner's title ruling): BLOCKED on question 1. F1 (18-character test names truncate at 17), F2 (constant deleted before its retained user), F3 (consumed result re-read), F4 (backlinks reintroduce 'short-ID'), F5 (span measurement under-specified: handoff phase, synchronization, lower-bound semantics, CI retrieval), F6 (wrong prior-version witness; fold of 1779000000000000001 is 6174316406), F7 (producer-boundary assertions), F8 (staging list), F9 (prune selector path): all P2, accepted and applied; residual widen/20-character prose fixed; [ALT-WT-022] consequence shortened. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1–F7, F8–F9 confirmed): no blocker; all nine closed with live evidence (`encoded-title` is 13 characters and the observability expectation slices to `PROCESS_TITLE_NAME_LENGTH`; the old constant survives Task 3 for helpers/__init__.py:39,146; isolated submissions per spelling; backlinks free of 'short-ID'; the two-phase span measurement is runnable as specified; the fold of 1779000000000000001 is 6174316406; producer assertions at pipelines.py:411, manager.py:808, task_monitor.py:4737; staging list and I-1 destination complete; prune path corrected). Review loop closed; the plan waits on the owner's remaining decisions (field names; case-insensitive decoding; the D8 widening incl. WeftClient.task; [REV-WT-*] record; [ALT-WT-022] endorsement; the CI step for the span measurement).
- owner rulings applied 2026-11-03: field names stay (the existing `short`, `tid_short`, and `manager_tid_short` names are kept; a future new field carrying the encoded form beside the TID would be `short_tid`, and this plan adds none); decoding accepts upper, lower or mixed case and canonicalizes to uppercase at the input boundary; the TID is opaque everywhere else (plan 10 ruling of the same day). The D8 widening, the [REV-WT-*] wording, the [ALT-WT-022] endorsement and the CI step stay open. Cross-family round 1 on the change (codex 0.161.0, 2026-11-03): PASS, one P2 (F1: the CLI equivalence test could not prove canonicalization happens at `_command_tid`, since `_task_status_record` re-resolves; direct `_command_tid` assertions added to an existing command test) applied without a further round, plus one qualification (the manager and prune selector mappings normalize nothing today; Task 6 adds it under owner decision 4). The round confirmed the field-name inventory (no renamed or new field), the opacity rule (the only production decomposition is the retiring fold at helpers/__init__.py:144), the 71-character title arithmetic, and that owner decisions 4–7 are unchanged by the rulings.
- owner rulings applied 2026-11-03 (items 1–6): widen every surface including `WeftClient.task` through one canonicalization function and one decoder, enforced by a structural test (+P); no [REV-WT-*] record; [ALT-WT-022] endorsement deferred to closeout ('Not now'); the CI span step removed, the span is measured once per platform and documented; title corroboration removed from the liveness host (the scoped process identity and the runtime handle remain the evidence; the title is observability only); I, L, O, U stay excluded, not aliased. Cross-family round on these changes pending.
- Cross-family review round 1 on the six rulings (codex 0.161.0, 2026-11-03): BLOCKED on F1–F3 (the structural test's package-wide heuristics overclaimed; no behavior test for the widened manager and prune surfaces; the client test file missing from the staging list): accepted and applied (bounded per-boundary checks with their limits stated; selection-observing tests for the three surfaces and a mixed-case client row; staging list corrected). F4 (the host fake's `cmdline()` now raises), F5 (a Windows readback of zero is 'unavailable', not 'below budget'), F6 (stale test reference and counts), F7 (the universal boundary claim qualified for the strict `runtime_liveness` and `--reverse` boundaries): accepted and applied. The round confirmed D14's class C (no reachable case where the title decided liveness) and the completeness of the spec inventory. Round 2 (codex 0.161.0, 2026-11-03, scoped to F1–F7): no blocker; all seven closed with live evidence (the bounded checks ignore an unrelated status fold and detect a fold on result.py:42's `raw_tid`; Manager lookups use the supplied TID and pruning uses exact filter membership, so the selection tests observe a discarded normalization; `WeftClient.task` at _client.py:323 stores the normalizer's result; the host catch at host.py:44 lets `AssertionError` escape; 25 importing test files leave 23 after two removals; [PY-2]'s canonical-full-TID requirement at 14:222 is preserved by the strict exceptions). Review loop on the six rulings closed; the plan waits only on the owner's endorsement of the [ALT-WT-022] text at closeout.
- cross-plan correction applied 2026-11-03 (owner's outside reviewer): the prune `task` selector normalization lives in the shared prune capability that both the CLI adapter and series plan 11's `client.system.prune` call (`cmd_system_prune` today, `_prune_families` after plan 11), which the structural inventory now locates by its `task_filters=` binding rather than by function name; a client-path selection case added to `test_force_deletes_active_ping_in_selected_scope`, skipped visibly until plan 11 lands; landing-order coupling with plan 11 recorded as hidden coupling 8 and in Task 6. Plan metadata gate and `bin/check-doc-paths` re-run.
- implementation 2026-11-03 (series plan 12, Anthropic implementer, on `main` at `af98c73b`): Tasks 0–8 executed; owner item 6 closed as accepted by the owner (2026-11-03: "I accept the theory edits"); the theory blocks land in a separate `Theory:` commit after the landing commit (Deviation Log), which moves `Status:` to `promoted`. Three deviations recorded in the Deviation Log (span pending on Linux and Windows; commit split; out-of-range status path). Owed before `completed`: the [WT-7] keyed possession probe (owner-written key, different-family answerer) and the different-family review of the completed work (§10), both with the series lead.

## Execution log

Plan-authoring evidence at `b7eed9f9`, from command output in this
worktree.

Production short-form sites (`grep -rn "tid_short_form\|TASKSPEC_TID_SHORT_LENGTH" weft/ integrations/ extensions/ bin/ --include=*.py`):
28 `tid_short_form` lines in 11 files, plus 3 `TASKSPEC_TID_SHORT_LENGTH`
lines outside its definition (the title sum, the helpers import, and the
fold); none in `integrations/`, `extensions/`, or
`bin/`.

```text
weft/_constants.py:84:TASKSPEC_TID_SHORT_LENGTH: Final[int] = 10
weft/_constants.py:907:    + TASKSPEC_TID_SHORT_LENGTH
weft/core/pipelines.py:38:from weft.helpers import tid_short_form
weft/core/pipelines.py:411:    pipeline_name = pipeline.name or f"pipeline-{tid_short_form(pipeline_tid)}"
weft/core/tasks/base.py:98:    tid_short_form,
weft/core/tasks/base.py:274:        self.tid_short = tid_short_form(self.tid)
weft/core/monitor/task_monitor.py:225:from weft.helpers import closing_queue_iterator, iter_queue_entries, tid_short_form
weft/core/monitor/task_monitor.py:4737:                manager_tid_short=tid_short_form(manager_tid),
weft/liveness/host.py:15:from weft.helpers import tid_short_form
weft/liveness/host.py:50:        short_tid = re.escape(tid_short_form(expected_tid))
weft/cli/app.py:46:    tid_short_form,
weft/cli/app.py:1809:    typer.echo(tid_short_form(result) if reverse is not None else result)
weft/commands/system.py:77:    tid_short_form,
weft/commands/system.py:207:        short = tid_short_form(full)
weft/commands/system.py:1182:        tid_short_form(tid)
weft/commands/tasks.py:82:    tid_short_form,
weft/commands/tasks.py:185:            return tid_short_form(value)
weft/commands/tasks.py:974:            tid_short=tid_short_form(tid),
weft/commands/tasks.py:1213:        tid_short=tid_short_form(tid),
weft/commands/tasks.py:1580:        tid_short=tid_short_form(tid),
weft/commands/tasks.py:1700:        tid_short=tid_short_form(tid),
weft/commands/_task_snapshot_reducer.py:34:from weft.helpers import tid_short_form
weft/commands/_task_snapshot_reducer.py:193:        short = tid_short_form(tid)
weft/commands/_task_snapshot_reducer.py:520:            tid_short=tid_short_form(tid),
weft/commands/events.py:40:from weft.helpers import tid_short_form
weft/commands/events.py:147:            tid_short=tid_short_form(normalized_tid),
weft/commands/task_monitor.py:43:from weft.helpers import tid_short_form, write_file_atomically
weft/commands/task_monitor.py:276:            tid_short_form(tid)
weft/commands/task_monitor.py:416:            "tid_short": tid_short_form(reduced.tid),
weft/helpers/__init__.py:39:    TASKSPEC_TID_SHORT_LENGTH,
weft/helpers/__init__.py:132:def tid_short_form(tid: str) -> str:
weft/helpers/__init__.py:146:    return f"{(grain + counter * 2_441_406) % 10**10:0{TASKSPEC_TID_SHORT_LENGTH}d}"
```

Candidate-discovery and ambiguity sites (`grep -rn "ambigu" weft/ --include=*.py`,
filtered to TID resolution; `grep -rn "resolve_full_tid\|_resolve_tid_filters\|_read_tid_states" weft/`):

```text
weft/commands/tasks.py:152:    """Resolve one full or unambiguous derived short TID.
weft/commands/tasks.py:164:            f"Ambiguous short TID {candidate}: {', '.join(matches)}"
weft/commands/system.py:204:    """Group retained task-state names by derived short form [CLI-1.2.3]."""
weft/commands/system.py:234:            f"Ambiguous short TID {candidate}: {', '.join(matches)}"
weft/commands/tasks.py:2565-2568: "# Ambiguity is batch-fatal before any control write." preflight loop
weft/commands/_task_snapshot_reducer.py:196: short-form filter match
resolve_full_tid: definition at weft/commands/tasks.py:149 and 11 call sites (lines 144, 195, 378, 890, 1123, 1381, 2135, 2231, 2252, 2354, 2568)
_read_tid_states: weft/commands/system.py:201 (definition), 231; weft/commands/tasks.py:161
_resolve_tid_filters: weft/commands/system.py:223 (definition); no production caller
```

Other "ambiguous" matches in `weft/` (Postgres config, namespace-ambiguous
incumbents, spec-store type, reservation migration, task-monitor reserved
preservation, retention) are unrelated to TIDs and are not touched.

Test files using `tid_short_form` (`grep -rc "tid_short_form" tests/ | grep -v ":0$"`):
112 lines in 25 files.

```text
tests/cli/test_cli_list_task.py:2
tests/cli/test_status.py:8
tests/commands/test_realtime_events.py:2
tests/commands/test_result_observation_connections.py:2
tests/commands/test_run.py:2
tests/commands/test_runtime_liveness_observation.py:2
tests/commands/test_runtime_prune.py:3
tests/commands/test_spawn_observation_connections.py:2
tests/commands/test_status.py:18
tests/commands/test_task_commands.py:8
tests/commands/test_task_evidence.py:4
tests/commands/test_task_owned_observation.py:3
tests/commands/test_task_snapshot_reducer.py:4
tests/commands/test_tid_mapping_contracts.py:7
tests/core/test_serve_log.py:2
tests/core/test_service_helper_connections.py:2
tests/helpers/task_observation.py:2
tests/liveness/test_analysis.py:2
tests/liveness/test_host.py:2
tests/system/test_short_tid.py:12
tests/tasks/test_liveness_monitor.py:2
tests/tasks/test_task_endpoints.py:2
tests/tasks/test_task_execution.py:2
tests/tasks/test_task_monitor.py:12
tests/tasks/test_task_observability.py:5
```

Tests that exercise the short form's semantics (deleted or rewritten by
Tasks 4–6; the rest of the list above only builds fixture values):

- `tests/system/test_short_tid.py` (8 tests; whole file deleted; one moves to
  `tests/system/test_tid_encoding.py`)
- `tests/commands/test_tid_mapping_contracts.py`:
  `test_short_collision_names_all_candidates`,
  `test_ambiguous_batch_sends_no_control`,
  `test_resolution_derives_short_and_ignores_newer_malformed_mapping`,
  `test_full_selector_excludes_other_colliding_task`,
  `test_short_resolution_uses_namespace_even_with_malformed_only_rows`, and
  the `collision` fixture
- `tests/commands/test_task_commands.py:607`
  `test_canonical_task_tid_returns_full_tid_and_rejects_ambiguous_selection`
- `tests/commands/test_task_snapshot_reducer.py:134` (short-form filter)
- `tests/cli/test_cli_list_task.py:69` (`task tid --reverse`)
- `tests/liveness/test_host.py:31` (title match)
- `tests/tasks/test_task_observability.py:538`, `:580`, `:585` (title
  prefix and digits-only regex)
- `tests/core/test_process_title.py:208` (ten-character `tid_short` literal
  with an exact-length assertion)
- `tests/tasks/test_task_execution.py:2214` (thread names)

Fixtures writing `"short": tid[-10:]` (left unchanged):
`tests/core/test_service_helper_connections.py:85`,
`tests/core/test_heartbeat_helpers.py:296`, `tests/cli/test_cli_manager.py:553`.

Budget evidence: `PROCESS_TITLE_STATUS_LENGTH` = 9,
`PROCESS_TITLE_MAX_LENGTH` = 71, `PROCESS_TITLE_HANDOFF_LENGTH` = 72
(computed by importing `weft._constants`); a 13-character TID with name 20
gives 74 (computed; not adopted, the owner ruled A, so the name constant
drops to 17 and the title stays 71). A spawn child's argv span on this
macOS host is 159 bytes including NULs (measured with a `multiprocessing`
spawn child and `psutil.Process().cmdline()`); macOS only, which is why
the ruling stands until the Task 4 measurement reports every platform.

Implementation entries (comprehension answers, RED assertions, gate
results) are appended below by the implementer.

### Implementation, 2026-11-03 (series plan 12, on `main` at `af98c73b`)

Implementer: Anthropic (Claude), dispatched by the series lead. Plan 11
landed first, so the prune `--task` normalization went directly into
`weft/commands/prune.py::_prune_families` (hidden coupling 8) and the
client-path prune case runs live, with no skip.

**Task 0, comprehension answers (recorded before the first edit).**

1. `inspect_host_process(pid, create_time)` returns `("live",
   "identity_match")` once the scoped `(pid, create_time)` identity matches a
   non-zombie process. After this change the host takes no `expected_tid`
   and never reads `cmdline()`, so the old `weft-proj-0123456789:...` title
   plays no part. No verdict changes: before, the title could only choose
   between two `live` reasons, and `probe_runtime_liveness` reads only
   `.evidence`. Re-checked at `af98c73b`: the host callers are
   `weft/liveness/analysis.py::probe_runtime_liveness` (the only
   `expected_tid=` caller), `weft/core/manager_runtime.py` (two calls),
   `weft/core/manager.py` (one call added by plan 4, no `expected_tid`, reads
   `.evidence`), and `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
   (no `expected_tid`). Matches the recorded answer.
2. No queue name changes; every TID-bearing queue name keeps the 19-digit
   full TID. The encoding appears only in renderings. `decode_tid` is
   arithmetic and reads no queue (pinned by
   `tests/commands/test_tid_mapping_contracts.py::test_encoded_selector_decodes_without_reading_state`,
   which asserts no `weft.state.tasks.*` queue exists). Matches.

Index row: `docs/plans/README.md` had the `draft` row;
`./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py` passed.
Plan 6 touched `weft/core/monitor/task_monitor.py`; `grep -n` at
`af98c73b` found the import at line 219 and the `manager_tid_short` producer
at line 4732 (the plan's 225 and 4737), edited in place.

**Task 1, spec promotion (worktree).** Every spec Replacement block was
applied verbatim by a scratch script that copies each block from this plan
into its quoted span (07: [OBS.4] opening, [OBS.5], [OBS.6], [OBS.8],
[LIVENESS.R5], Related Plans; 01: [CC-3.2] sentence and the Related Plans
list after [CC-3.5]; 10: [CLI-1.2.3] bullet and paragraph, mapping, Related
Plans; 14: [PY-2] clause, the new `client.task(tid)` paragraph before
"Custody-only point snapshots", Related Plans). Every quoted span matched
exactly once. The two theory blocks were held for the separate `Theory:`
commit (Deviation Log). Promotion baseline: `af98c73b` + worktree; then the
landing commit SHA (Spec Baseline).

**Task 2, RED.** `./.venv/bin/python -m pytest tests/system/test_tid_encoding.py`
failed at collection with
`ImportError: cannot import name 'decode_tid' from 'weft.helpers'`. The
plan names `encode_tid`; the import line is sorted (`decode_tid,
encode_tid`), so Python reports the first missing name. Same cause.

**Task 3, GREEN.** `TASKSPEC_TID_ENCODED_LENGTH = 13` and
`TASKSPEC_TID_ENCODING_ALPHABET` added beside `TASKSPEC_TID_SHORT_LENGTH`;
`PROCESS_TITLE_NAME_LENGTH = 17` with the stated docstring; the title sum
uses the new width, so `PROCESS_TITLE_MAX_LENGTH` = 71 and
`PROCESS_TITLE_HANDOFF_LENGTH` = 72 (asserted by the title property test and
`test_every_defined_title_status_fits_the_handoff_capacity`). `encode_tid`
and `decode_tid` follow the reference algorithm. The grep for literal 20-
character name limits in `tests/` found none. The 33 codec tests passed.

**Task 4, RED then GREEN.** RED run (after the test edits, before the
producer switch): 9 failed, 427 passed, 3 skipped over the Task 4 files;
the failures were the producer assertions (`ValueError: not a full or
encoded task ID` from `decode_tid` on a ten-digit `manager_tid_short` and on
the default pipeline name), `test_consumer_publishes_and_matches_encoded_title`
(`AssertionError: weft-weft-har-2891786950:encoded-title:running:waiting`),
the observability title tests (`'weft-ctxrm-rf-1661318425:...' !=
'weft-ctxrm-rf-1HS5X1AAWD458:...'`), the `short` row tests (`'3497255500' ==
'1HS5X1AAVFGWR'`), and the exact-maximum test (`assert 68 == 71`, the real
formatter still emitting a ten-character TID). GREEN: every listed
`tid_short_form` call replaced by `encode_tid` (also
`weft/commands/system.py::_read_tid_states`, deleted in Task 5); the
reverse branch of `task_tid` catches `ValueError`; `tid_short_form`,
`TASKSPEC_TID_SHORT_LENGTH` and `tests/system/test_short_tid.py` deleted;
`weft/liveness/host.py` keeps only the identity checks and returns `("live",
"identity_match")`; the `expected_tid=tid` argument is gone from
`weft/liveness/analysis.py`. The 22 fixture files were edited mechanically
(the plan's 23 counted `tests/tasks/test_task_observability.py`, which got
its semantic edits separately). The three `"short": tid[-10:]` fixtures
are unchanged. Stop-if check: no reader resolves by the stored `short`; no
rename; no mock; no reachable title-only liveness case. Verify command:
920 passed, 5 skipped; both greps print nothing.

**Rewritable span measurement (one-time; not a test; owner ruling 4).**
Script text, copied here before the first run so every platform runs the
same text (kept outside the repository at the session scratchpad
`series/impl/plan12/span/span_measurement.py`):

```python
"""One-time rewritable argv span measurement (TID display-encoding plan, Task 4).

Not a test; never committed, collected by pytest, or run in CI. Run from the
repository root after `. ./.envrc` and `uv sync --all-extras`:
    ./.venv/bin/python <scratch path>/span_measurement.py
"""

from __future__ import annotations

import multiprocessing
import sys
from multiprocessing.connection import Connection

import psutil

PROBE_LENGTH = 1024
TITLE_BUDGET = 71
_VOCAB = "weft-abcdefghijklmnopqrstuvwxyz0123456789_ABCDEFGHJKMNPQRSTVWXYZ:"
PROBE = "".join(_VOCAB[i % len(_VOCAB)] for i in range(PROBE_LENGTH))


def _child(conn: Connection) -> None:
    from weft.core import process_title

    owner = process_title._ProcessTitle()
    owner.set(PROBE)
    conn.send("phase1")
    if conn.recv() == "exit":
        return
    if sys.platform == "darwin":
        owner.set("weft-" + "c" * 8 + "-" + "0" * 13 + ":" + "n" * 17 + ":cancelled:" + "d" * 15)
        owner._deadline = 0
        owner.tick()
        owner.set(PROBE)
        conn.send("phase2")
        conn.recv()


def _survived(joined: str) -> int:
    best = 0
    for k in range(1, PROBE_LENGTH + 1):
        if PROBE[:k] in joined:
            best = k
        else:
            break
    return best


def _report(pid: int, phase: int) -> int:
    cmdline = psutil.Process(pid).cmdline()
    joined = " ".join(cmdline)
    survived = _survived(joined)
    shown = ">=1024" if survived == PROBE_LENGTH else str(survived)
    print(
        f"span-measurement platform={sys.platform} phase={phase} "
        f"survived={shown} joined={len(joined)}",
        flush=True,
    )
    if sys.platform == "win32":
        print("span-readback-unavailable platform=win32", flush=True)
        return 0
    if survived < TITLE_BUDGET:
        print(f"span-below-title-budget phase={phase}", flush=True)
        return 1
    return 0


def main() -> int:
    ctx = multiprocessing.get_context("spawn")
    parent, child_conn = ctx.Pipe()
    proc = ctx.Process(target=_child, args=(child_conn,))
    proc.start()
    status = 0
    try:
        assert parent.recv() == "phase1"
        status |= _report(proc.pid, 1)
        if sys.platform == "darwin":
            parent.send("continue")
            assert parent.recv() == "phase2"
            status |= _report(proc.pid, 2)
    finally:
        try:
            parent.send("exit")
        except OSError:
            pass
        proc.join(timeout=10)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=10)
    return status


if __name__ == "__main__":
    sys.exit(main())
```

macOS run, 2026-11-03, `af98c73b` + worktree, macOS 27.0.1, Python 3.14.9,
`./.venv/bin/python <scratch path>/span_measurement.py` from the repository
root after `. ./.envrc`:

```text
span-measurement platform=darwin phase=1 survived=>=1024 joined=1028
span-measurement platform=darwin phase=2 survived=71 joined=75
```

Exit 0. Phase 2 reports 71 survived characters after the handoff padded to
`PROCESS_TITLE_HANDOFF_LENGTH` (72 with the NUL), the figure the plan
expected by construction; recorded without reinterpretation. Both phases
are at or above the 71-character budget.

Linux: pending: owner-run. Procedure: on a Linux machine or container with
the repository at the landing commit, `. ./.envrc`, `uv sync --all-extras`,
write the script text above to a scratch path outside the repository, and
run `./.venv/bin/python <scratch path>/span_measurement.py` from the
repository root; it runs phase 1 only and prints one `span-measurement`
line; a `span-below-title-budget` line and exit 1 mean fewer than 71
characters survived (a finding for the owner, not a gate).

Windows: pending: owner-run. Same steps on a Windows machine with
`.venv\Scripts\python` in place of `./.venv/bin/python`; phase 1 only; the
script prints the phase line, then `span-readback-unavailable
platform=win32`, and exits 0 whatever the readback shows. A readback of zero
is recorded as "measured on <date>: win32: readback unavailable on this
platform; the title budget is not measurable here by this method", never as
below budget. Not through `workflow_dispatch` (ruling 4).

The owner replaces the two pending entries in the [OBS.4] line (and in the
Replacement block above) with the printed figures in the stated form.

**Task 5.** `_read_tid_states`, `_resolve_tid_filters` and the
`list_task_state_tids` import left `weft/commands/system.py`;
`resolve_full_tid(raw)` and `_command_tid(raw)` call
`normalize_tid(raw.strip().lstrip("T"))`; the 10 call sites dropped their
`ctx`/`broker` arguments and the four `_command_tid` sites their `context`
and `allow_unknown_full`; the ambiguity preflight loop is deleted; the
reducer filter is `tid not in tid_filters`. Stop-if check: no production
caller of the deleted helpers; no batch-control test other than the deleted
ambiguity tests changed. Verify: 172 passed, 1 failed, the failure being
`test_encoded_selector_decodes_without_reading_state` (`assert None ==
'1760000000987654321'`), green after Task 6 as the plan says.

**Task 6, RED.** Structural test, before the boundary changes:

```text
AssertionError: weft/commands/result.py::_normalize_tid: no normalize_tid call on 'raw_tid'
  weft/commands/manager.py::cmd_manager_stop: no normalize_tid call on 'tid'
  weft/commands/manager.py::cmd_manager_status: no normalize_tid call on 'tid'
  weft/commands/prune.py::_prune_families: no single loop over the --task selectors
  decode_tid call sites: []
```

The two `task` resolvers already passed (a) because Task 5 had rewired
them through `normalize_tid` in the plan's order; `WeftClient.task` passed
as expected. CLI equivalence: the four encoded spellings failed (`Task
1hS5XcGrNgG00 not found`, exit 2); the probes failed on `task tid --reverse
9999999999999999999` (exit 1 with `ValueError: invalid message ID`
traceback, hidden coupling 5). Client case table: 4 failed (`InvalidTID:
invalid task id '1JZKD3PKK0000'`). Manager stop `[encoded]`: `assert None ==
'{"command": "STOP"}'`. CLI manager status: `(1, '', 'Manager
1HS5XJYDY3800 not found')`. Prune: `AssertionError: cli` / `assert 0 == 1`.
Direct `_command_tid` assertions: `InvalidTID: Invalid task ID:
'1HA9C2Q2XX0Z7'`.

**Task 6, GREEN.** `normalize_tid` strips, removes one `T`, and calls
`decode_tid`; `result._normalize_tid` tries `normalize_tid` and falls back to
its digits-only rule; `cmd_manager_stop` (explicit TID) and
`cmd_manager_status` replace `tid` with `normalize_tid(tid)` and keep the
not-found path on `InvalidTID`; `_prune_families` maps each `task` selector
through `normalize_tid` in one loop before `task_filters=`; `cmd_task_tid
--reverse` raises `InvalidTID` unless `encode_tid` succeeds; the CLI help
strings say "Full or encoded TID" and "Return the encoded TID for a full
TID". No import cycle (`tasks.py`, `result.py`, `manager.py` and `prune.py`
import `normalize_tid` at module top). The direct `_command_tid` assertions
live in `tests/commands/test_task_commands.py::test_canonical_task_control_records_invalid_explicit_tid_and_continues`.
Verify command plus the three selection files, `tests/commands/test_task_commands.py`
and `tests/cli/test_cli_result.py`: 590 passed, 2 skipped.

Hand-checked mutations (each applied alone, the named test run, the file
restored from a copy):

```text
CAUGHT: extra decode_tid call in _command_tid (structural b) -> test_tid_arguments_share_one_canonicalizer_and_one_decoder | E       AssertionError: decode_tid call sites: [('weft/commands/submission.py', 99, True), ('weft/commands/tasks.py', 2355, False)] / E       assert not ["decode_tid call sites: [('weft/commands/submission.py', 99, True)
CAUGHT: raw_tid.upper() in result._normalize_tid (structural a) -> test_tid_arguments_share_one_canonicalizer_and_one_decoder | E       AssertionError: weft/commands/result.py::_normalize_tid: local .upper() on 'raw_tid' / E       assert not ["weft/commands/result.py::_normalize_tid: local .upper() on 'raw_tid'"]
CAUGHT: cmd_manager_status stops calling normalize_tid (structural a) -> test_tid_arguments_share_one_canonicalizer_and_one_decoder | E       AssertionError: weft/commands/manager.py::cmd_manager_status: no normalize_tid call on 'tid' / E       assert not ["weft/commands/manager.py::cmd_manager_status: no normalize_tid call on 'tid'"]
CAUGHT: cmd_manager_stop discards normalize_tid result -> test_stop_command_writes_stop_for_active_manager | E       assert None == '{"command": "STOP"}' / E        +  where None = read_one()
CAUGHT: prune discards normalize_tid result -> test_force_deletes_active_ping_in_selected_scope | E           AssertionError: cli / E           assert 0 == 1
CAUGHT: _command_tid returns candidate unchanged -> test_canonical_task_control_records_invalid_explicit_tid_and_continues | E           AssertionError: assert '1HA9C2Q2XX0Z7' == '1777000000000000999' / E
```

Also checked by hand: `cmd_manager_status` discarding `normalize_tid`'s
result fails `tests/cli/test_cli_manager.py::test_manager_start_and_status`
with `(1, '', 'Manager 1HS5XJYDY3800 not found')`.

**Task 7.** README: the Encoded TID bullet, the `ps` example, and the
`Short-ID` sentence as specified; also the ten-digit
`weft-proj-0001234567:file-watcher:running` example under the services
section became `weft-proj-1JZKD3PKK0800:file-watcher:running` (not listed
in Task 7; a stale ten-digit rendering in the same file, so documentation
sync). CHANGELOG `[Unreleased]` `### Changed` entry as specified, also
naming the 17-character name limit. The Task 7 grep prints nothing.

**Task 8.** Touched functions carry `Spec:` lines naming [OBS.5] and
[CLI-1.2.3] (`encode_tid`, `decode_tid`, `normalize_tid`,
`resolve_full_tid`, `_command_tid`, `result._normalize_tid`); the host
docstring cites [LIVENESS.R5] and [CC-3.2]; the `task_tid` docstring now
says "encoded-to-full". The [CLI-1.2.3] and [OBS.5] mapping claims name
exactly these owners. Gates: run from the landing tree on 2026-11-03:
focused §7 command (`tests/system/test_tid_encoding.py tests/liveness
tests/commands/test_tid_mapping_contracts.py tests/cli/test_cli_list_task.py
tests/core/test_process_title.py tests/tasks/test_task_observability.py`):
exit 0. Fast suite (`./.venv/bin/python -m pytest -q -p no:cacheprovider`):
the first run failed one test,
`tests/commands/test_submission_observation.py::test_custody_is_not_probed_for_noncanonical_or_out_of_range_identity[9999999999999999999]`
(`ValueError: invalid message ID` from `encode_tid` in
`_known_status_record`; fixed in `_task_status_record`, Deviation Log
row "[OBS.5] (D9)"); the rerun exited 0 (6,466 passed, 47 skipped). All
tests (`./.venv/bin/python -m pytest -m ""`): 6,469 passed, 58 skipped, 1
failed: `tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
(`phase: manager_convergence_timeout`), a Manager-reuse stress test that
reads no TID rendering; it passed 3 of 3 runs alone at the same tree, so it
is recorded as load flakiness under the test-suite-structure plan's
exposure note, not as a finding of this change. mypy: "Success: no issues
found in 484 source files". `ruff check .`: "All checks passed!". `ruff
format --check .`: the same three Markdown code blocks the baseline
flagged (two plan files' blocks and this plan's reference algorithm), no
Python file. `bin/check-doc-paths`: "check-doc-paths: OK". `pytest
tests/specs`: exit 0. Both §7 greps print nothing.

Audit refresh (I-1). The refreshed row, in the column format of the
theory-versus-code audit table in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`; every other
row is carried forward by reference to that table, which is not edited:

| Identifier | Claim (one clause) | Contract (spec code) | Evidence in tree | Classification | Plan / fork |
|---|---|---|---|---|---|
| I-1 → [WT-2a.1] | Every unit of work has one hybrid-timestamp TID and every rendering of it is lossless | IMMUT.3; OBS.5; [SB-0.2] | `tests/specs/manager_architecture/test_tid_correlation.py::test_spawn_request_timestamp_matches_tid`; `tests/system/test_tid_encoding.py::test_encoding_round_trips_every_tid_in_either_case` (round trip over 0 through 2^63 − 1) and `::test_title_carries_the_complete_encoded_tid_within_budget`; `tests/system/test_tid_encoding.py::test_tid_arguments_share_one_canonicalizer_and_one_decoder`; the §7 grep gate prints nothing (no `tid_short_form`, candidate search, ambiguity error, or title regex remains) | aligned | 12 TID display encoding (this plan) |

[WT-7] keyed possession probe at class-5 completion: owed. It needs an
owner-written key and a different-family answerer that did not author the
plan; neither is available to the implementer, so `Status:` stays
`promoted`, not `completed`, until the probe key and result are in the
Review Log. The completed-work different-family review (§10) is also owed
to the series lead.
