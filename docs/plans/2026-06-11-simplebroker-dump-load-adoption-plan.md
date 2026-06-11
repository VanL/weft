# SimpleBroker Dump Load Adoption Plan

Status: completed
Source specs: docs/specifications/10-CLI_Interface.md [CLI-6]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.2], [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-6]; docs/specifications/00-Quick_Reference.md
Superseded by: none

## 1. Goal

Move `weft system dump` and `weft system load` to SimpleBroker's versioned
`simplebroker-dump` v1 NDJSON format and primitives while preserving Weft's
system-level semantics: runtime-only queue exclusion, dry-run import preview,
alias-conflict preflight, exact message ID preservation, and SQLite snapshot
rollback. Do not add `weft queue dump` or `weft queue load` in this slice.

## 2. Source Documents

- `docs/specifications/10-CLI_Interface.md` [CLI-6]: `weft system dump/load`
  behavior, exit-code expectations, and maintenance-command ownership.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.2],
  [SB-0.4]: SimpleBroker owns queue mechanics, message IDs, broker target
  resolution, and backend-neutral command behavior.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]: spawn-request
  message IDs are task TIDs and must survive dump/load round trips.
- `docs/specifications/00-Quick_Reference.md`: queue inventory and
  `weft.state.*` runtime-only persistence boundary.
- `README.md`: user-facing examples and operational notes for `weft system
  dump/load`.
- `../simplebroker/simplebroker/_dump.py`: source implementation for
  `dump_lines()` and `load_lines()`.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  plan, hardening, and review workflow.

The existing `docs/plans/2026-04-06-simplebroker-backend-generalization-plan.md`
is historical context for backend-neutral import behavior. It is not the
current execution plan for this format change.

## 3. Context and Key Files

Files to modify:

- `weft/commands/_dump_support.py`
- `weft/commands/_load_support.py`
- `tests/commands/test_dump_load.py`
- `tests/commands/test_dump_load_sqlite_only.py`
- `tests/cli/test_cli_system.py` if CLI-level format expectations need an
  assertion
- `README.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/plans/README.md`

Read first:

- `../simplebroker/simplebroker/_dump.py`: confirms `message.id` is the exact
  message ID and `load_lines()` applies through `insert_messages()`.
- `weft/commands/_dump_support.py`: currently owns output path resolution,
  runtime-state exclusion, summary text, and claimed-row reporting.
- `weft/commands/_load_support.py`: currently owns dry-run parsing,
  alias-conflict detection, exact-ID import, and SQLite rollback.
- `weft/commands/queue.py`: shows why raw queue subcommands are mostly
  SimpleBroker passthrough and why this slice should not add Weft-specific
  dump/load there.
- `weft/cli/app.py`: Typer wrappers for `system dump` and `system load` should
  stay thin.

Current structure:

- `weft queue *` is a raw queue inspection and mutation surface with context
  injection. It should not gain a Weft-specific dump/load wrapper in this
  slice, because Weft dump/load excludes runtime queues and has import
  preflight semantics that are system-level maintenance behavior.
- `weft system dump` writes to a file path, not stdout, and emits a human
  summary. It must continue to do both.
- `weft system load --dry-run` must parse and report without writes.
- `weft system load` must preserve included message IDs. For
  `weft.spawn.requests`, the message ID is the task TID.
- Existing premature WIP edits in the command/test files must be audited
  against the concrete checklist below before more implementation work
  continues.

Premature WIP audit checklist:

- Accept the direction of `weft/commands/_dump_support.py` using
  `simplebroker.dump_lines()` with a `weft.state.*` exclude glob, subject to
  tests and docs.
- Accept the direction of tests asserting `header` records and message `id`
  fields, but add the missing same-target existing-alias regression.
- Rework `weft/commands/_load_support.py`: do not keep a raw parsed
  `dump_lines` apply list that still contains no-op aliases. Rename the field
  to `apply_lines` or build it at execute time so only aliases to create are
  passed to `load_lines()`.
- Rework or remove `ImportReport.aliases_to_update`; Weft import does not
  rewrite alias targets. The states are create, no-op, or conflict.
- Keep the spec backlinks and plan README row already added; verify they are
  present rather than adding duplicates.

Comprehension checks before editing:

1. In SimpleBroker v1 dump records, which field carries Weft's old
   `timestamp` value? Answer: `id`.
2. Why must `weft.state.*` be excluded on both dump and load? Answer: those
   queues carry runtime-only service, mapping, streaming, endpoint, and
   pipeline evidence; restoring them would import stale live-state claims.
3. Why is `weft queue dump/load` out of scope? Answer: the queue app is raw
   queue passthrough, while dump/load has Weft-specific system maintenance
   rules.

## 4. Invariants and Constraints

- Runtime-only queues matching `weft.state.*` must stay excluded from dump and
  load.
- Message IDs must be preserved exactly. Do not load by rewriting IDs through
  ordinary `write()`.
- `weft.spawn.requests` dump/load round trips must keep the queue row ID equal
  to the child task TID.
- Command layers must not hand-write backend SQL for queue rows.
- Use SimpleBroker public primitives, preferably `dump_lines()` and
  `load_lines()`, plus public broker inspection for Weft preflight.
- Preserve `system load --dry-run` and alias-conflict exit code `3`.
- Preserve SQLite snapshot rollback for apply failures.
- Alias import is not an update mechanism. Existing aliases with the same
  target are no-ops; existing aliases with a different target are conflicts.
- Do not add backward compatibility for Weft's old `meta` plus
  `message.timestamp` dump format. The user explicitly stated there are no
  users of the current capability.
- Do not add new dependencies, new abstraction layers, or unrelated queue
  cleanup behavior.
- Do not add `weft queue dump` or `weft queue load` unless a separate spec
  change approves a raw broker passthrough surface.
- Keep tests broker-backed. Do not mock SimpleBroker queues, broker
  connections, or message-ID import behavior.

Error-path priority:

- Invalid dump format is fatal.
- Alias conflicts are fatal before writes and return exit code `3`.
- Duplicate message IDs at the destination are fatal. Weft does not make load
  idempotent; the operator-facing error should clearly say the import failed
  because exact IDs could not be inserted, and SQLite rollback must still run.
- Runtime queue records in an input dump should be skipped with a warning or
  removed from the normalized load stream before calling `load_lines()`.
- Claimed-row summary probe failure remains best-effort; it must not fail an
  otherwise successful dump.

Rollback and rollout:

- This is an intentional incompatible file-format change. Rollback is code
  rollback, not reader compatibility.
- Because there are no users of the old format, rollout does not need a
  dual-reader window.
- If the implementation reveals an active old-format consumer, stop and
  re-plan. Do not silently add partial compatibility.

Review gates:

- Self-driven fresh-eyes plan review is required before implementation is
  resumed.
- External plan review is required because this changes a persisted format and
  crosses command, spec, test, and README layers.
- Completed-work review is required before reporting done if the final code
  differs materially from this plan.

## 5. Tasks

1. Normalize plan and documentation traceability.
   - Outcome: this plan exists, `docs/plans/README.md` has a matching draft
     row and count, and source specs link back to the plan.
   - Files to touch: this plan, `docs/plans/README.md`,
     `docs/specifications/10-CLI_Interface.md`,
     `docs/specifications/04-SimpleBroker_Integration.md`,
     `docs/specifications/05-Message_Flow_and_State.md`.
   - Stop if: the specs imply `weft queue dump/load` is required.
   - Done when: plan metadata tests can discover the plan and the spec
     backlinks are present.
   - Current audit result: this task is implemented in the WIP. Verify rather
     than duplicating rows or links.

2. Decide and document the queue subcommand boundary.
   - Outcome: no `weft queue dump/load` is added in this slice, and the
     rationale is captured in README/spec text if needed.
   - Files to touch: `README.md`, `docs/specifications/10-CLI_Interface.md`
     only if current wording becomes ambiguous after the format change.
   - Reuse: existing `weft system dump/load` commands.
   - Do not: add hidden aliases from `weft queue dump` to `weft system dump`.
   - Done when: `weft queue` command list stays unchanged and tests do not
     expect new queue subcommands.

3. Refactor `system dump` onto SimpleBroker dump primitives.
   - Outcome: `cmd_dump()` writes SimpleBroker `simplebroker-dump` v1 lines to
     the existing output file path while excluding `weft.state.*` and reporting
     exported message, queue, alias, and omitted claimed-row counts.
   - Files to touch: `weft/commands/_dump_support.py`,
     `tests/commands/test_dump_load.py`, possibly
     `tests/cli/test_cli_system.py`.
   - Read first: `../simplebroker/simplebroker/_dump.py::dump_lines`.
   - Reuse: `simplebroker.dump_lines(db, exclude=["weft.state.*"])`.
   - Do not: reimplement message iteration with fixed `peek_many(limit=...)`
     unless SimpleBroker's primitive cannot express the needed behavior.
   - Stop if: the implementation needs private SimpleBroker APIs.
   - Done when: dump tests assert a `header` first line with
     `format="simplebroker-dump"`, message records use `id`, runtime queues
     are absent, and claimed rows are still reported as omitted.

4. Refactor `system load` onto SimpleBroker load primitives.
   - Outcome: `cmd_load()` accepts only SimpleBroker v1 dumps, builds the same
     dry-run/import report, filters any `weft.state.*` records before apply,
     preserves alias-conflict exit code `3`, and applies a normalized
     `apply_lines` stream through `simplebroker.load_lines()`.
   - Files to touch: `weft/commands/_load_support.py`,
     `tests/commands/test_dump_load.py`,
     `tests/commands/test_dump_load_sqlite_only.py`.
   - Read first: `../simplebroker/simplebroker/_dump.py::load_lines` and
     existing `_load_support.py` rollback code.
   - Reuse: `simplebroker.load_lines()` for actual message and alias apply
     after Weft preflight passes.
   - Important normalization: after `_enrich_import_plan()` classifies aliases,
     build or refresh an `apply_lines` list containing the original header,
     only aliases whose alias name is in `report.aliases_to_create`, and all
     included message records. Do not pass same-target existing aliases through
     to `load_lines()`, because SimpleBroker's `add_alias()` is an
     insert-style operation and may reject them even though Weft preflight
     considers them a no-op.
   - Alias update rule: remove `aliases_to_update` or leave it permanently
     unused with an explicit comment/docstring that Weft import never rewrites
     existing alias targets.
   - Do not: add compatibility for old `meta` or `timestamp` records.
   - Stop if: `load_lines()` cannot be used without losing dry-run or conflict
     preflight. If that happens, record the reason and re-plan before
     hand-rolling apply.
   - Done when: dry-run does not write, apply imports messages under exact
     IDs, alias conflicts stop before writes, runtime queue input is skipped,
     and SQLite rollback still restores state after apply failure.

5. Update user-facing docs.
   - Outcome: README and specs describe the SimpleBroker dump format accurately
     enough for operators, including that message IDs appear as `id` in the
     file.
   - Files to touch: `README.md`,
     `docs/specifications/10-CLI_Interface.md`,
     `docs/specifications/04-SimpleBroker_Integration.md`,
     `docs/specifications/05-Message_Flow_and_State.md`,
     `docs/specifications/00-Quick_Reference.md` only if queue tables need a
     local wording adjustment.
   - Stop if: docs start promising a stable archival or legal evidence format.
     The specs already say task logs are operational evidence, not forensic
     truth.
   - Done when: docs match the implemented wire shape and still say
     `weft.state.*` is excluded.

6. Verify and review.
   - Outcome: targeted tests and static checks relevant to this slice pass,
     and review findings are resolved.
   - Commands:
     - `./.venv/bin/python -m pytest tests/commands/test_dump_load.py -q`
     - `./.venv/bin/python -m pytest tests/commands/test_dump_load_sqlite_only.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q`
     - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
     - `./.venv/bin/ruff check weft tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py tests/cli/test_cli_system.py`
     - `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
   - Stop if: tests require a mocked broker to prove core behavior.
   - Done when: each requested behavior has a test or inspection evidence line
     and review findings are either addressed or explicitly rejected with a
     reason.

## 6. Testing Plan

Use real broker-backed tests only.

- Update `tests/commands/test_dump_load.py` for the new file format:
  `header` first line, message `id`, round-trip queue contents, exact
  spawn-request ID preservation, alias-conflict preflight, dry-run preview,
  runtime-state exclusion, and same-target existing aliases treated as a
  no-op rather than a write failure.
- Update `tests/commands/test_dump_load_sqlite_only.py` to keep the rollback
  proof under the new format.
- Keep or add a CLI-level assertion in `tests/cli/test_cli_system.py` that
  `weft system dump` writes SimpleBroker-format content while excluding
  runtime queues.
- Run `tests/specs/test_plan_metadata.py` after adding the plan and README row.

Red-green note: ideal ordering is failing-test-first for the format
expectations, but premature implementation edits already exist in the working
tree. Before continuing, audit those edits against this plan and make the
tests fail or pass for the intended reasons rather than treating the current
diff as authoritative.

What not to mock:

- Do not mock `simplebroker.dump_lines()`, `simplebroker.load_lines()`,
  `insert_messages()`, queues, broker stats, or SQLite rollback. Use real
  temporary broker contexts through existing helpers.

Observable proof:

- The dump file starts with a SimpleBroker header.
- Dumped message records use `id`.
- Loading a dump preserves spawn-request row IDs.
- `weft.state.*` rows are not restored.
- Alias conflicts leave destination aliases and queues unchanged.
- Existing same-target aliases do not cause apply failure and are not rewritten.
- Duplicate destination message IDs fail clearly and do not silently rewrite
  IDs.
- SQLite apply failure restores the pre-import state.

## 7. Verification and Gates

Per-task gates are listed in the task section. Final gate before reporting
done:

```bash
./.venv/bin/python -m pytest tests/commands/test_dump_load.py -q
./.venv/bin/python -m pytest tests/commands/test_dump_load_sqlite_only.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py tests/cli/test_cli_system.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If the local virtualenv is missing, first run `. ./.envrc` and `uv sync
--all-extras`, then use the in-repo binaries. Do not assume global `pytest`,
`ruff`, or `mypy`.

## 8. Independent Review Loop

Self-review is required after drafting this plan and before implementation
resumes.

External review is required because the change is a persisted format change
touching command code, tests, README, and specs. Preferred reviewer: a
different available agent family. If only Codex-family tooling is available,
record that limitation and still run a distinct plan review pass.

Review prompt:

> Read `docs/plans/2026-06-11-simplebroker-dump-load-adoption-plan.md`, the
> governing spec sections, `../simplebroker/simplebroker/_dump.py`,
> `weft/commands/_dump_support.py`, and `weft/commands/_load_support.py`.
> Look for errors, bad ideas, and latent ambiguities. Do not implement. Could
> you implement this confidently and correctly if asked?

Every review finding must be handled explicitly: update the plan/code/docs,
explain why the current path is still correct, or mark the finding out of
scope.

## 9. Out of Scope

- `weft queue dump`
- `weft queue load`
- legacy Weft dump-format compatibility
- new broker APIs
- queue cleanup or pruning behavior
- task monitor retention behavior
- manager or task execution-path changes
- public client API changes
- redaction or secret-management changes for dump output

## 10. Fresh-Eyes Review

Self-review status: completed on 2026-06-11 before implementation resumed.

Findings:

- P1: The first draft said to call `load_lines()` after preflight but did not
  say to remove existing same-target aliases from the apply stream. That would
  let SimpleBroker's insert-style `add_alias()` reject a no-op alias and fail
  a valid Weft import. Plan updated in Task 4 and the testing plan.
- P2: Runtime alias records need explicit filtering as well as runtime queue
  message records, because SimpleBroker aliases match on alias or target name.
  The original draft mentioned this only as a review question; Task 4's
  runtime filtering requirement now covers records before apply.
- P3: The dump summary can stay accurate for exported messages/queues/aliases
  by counting records yielded by `dump_lines()`, while claimed-row reporting
  remains a separate best-effort stats probe. This preserves current user
  semantics without hand-iterating messages.
- P4: External review found that "audit premature WIP" was not actionable. The
  plan now includes an explicit WIP audit checklist in Section 3.
- P5: External review found duplicate-ID behavior was not explicit. Section 4
  now states that duplicate destination message IDs are fatal, not idempotent.
- P6: External review found `ImportPlan.dump_lines` could be confused with
  SimpleBroker's export primitive. Task 4 now requires the normalized apply
  stream to be named `apply_lines` or built at execute time.

Residual risk:

- `dump_lines()` is a logical export rather than a point-in-time snapshot under
  concurrent writers. That matches SimpleBroker's documented primitive and the
  current Weft dump contract, but docs should avoid implying snapshot
  isolation.

External review status:

- Qwen review completed on 2026-06-11. It would sign off after the same-target
  alias filter mechanics and WIP audit actionability were fixed. Both findings
  were accepted and the plan was updated. Secondary findings on alias update
  dead code, duplicate-ID diagnostics, naming clarity, and reconstructed JSON
  ordering were accepted as implementation guidance.
- Claude review job was cancelled after the Qwen review satisfied the external
  review gate.

Completed-work review status:

- Claude completed a read-only code review on 2026-06-11. It found no
  correctness regressions in the implementation files and tests. Two
  low-severity observations were accepted: move the partial-write risk flag
  until after the broker opens, and add an explicit old Weft `meta` plus
  `message.timestamp` rejection test. Both were implemented.

The external reviewer should check especially:

- whether using `load_lines()` can accidentally update aliases that preflight
  should leave alone
- whether runtime alias records need explicit filtering as well as runtime
  queue records
- whether the dump summary can stay accurate without reimplementing queue
  iteration
- whether rejecting old-format dumps is clearly documented and tested
- whether the plan gives enough detail for a zero-context engineer to avoid
  adding `weft queue dump/load`
