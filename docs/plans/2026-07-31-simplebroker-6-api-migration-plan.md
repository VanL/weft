# SimpleBroker 6.0 API Migration Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.2], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-4], [CLI-4.1], [CLI-6]
Superseded by: none

Class: 5 — spec-changing. The dependency upgrade crosses the queue-command,
project-discovery, watcher, message-size, and dump/load surfaces. SimpleBroker
6.0 reserves exact message ID zero, so Weft's load validation contract must
explicitly reject zero before writes. Hardening applies because the public load
contract changes.

## Goal

Make Weft consume only the supported SimpleBroker 6.0 Python surfaces, prove
that Weft's command delegation satisfies the new keyword-only bindings, reject
reserved exact message ID zero at load validation, and preserve all other
queue, context-discovery, watcher, and dump/load behavior.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.2],
  [SB-0.4] governs queue-command delegation, message-ID use, broker-target
  discovery, and watcher use.
- `docs/specifications/10-CLI_Interface.md` [CLI-4], [CLI-4.1], [CLI-6]
  governs the operator-visible queue and dump/load contracts.
- `../simplebroker/CHANGELOG.md` 6.0.0 records the keyword-only command-layer
  change and the project-config helper exports added to `simplebroker.ext`.
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-1], [SB-API-2],
  [SB-API-6], [SB-API-10] defines the supported root, `ext`, and command
  surfaces. It keeps `simplebroker.project` valid for existing callers but
  directs new project-config imports to `simplebroker.ext`; unlisted submodules
  such as `simplebroker.watcher` and `simplebroker.db` are not public surfaces.
- `../simplebroker/docs/specs/13-message-identity.md` [SB-ID-1], [SB-ID-4]
  reserves message ID zero as the checkpoint origin and admits exact insertion
  only for positive IDs.
- `docs/agent-context/engineering-principles.md` §§3–6 and
  `docs/agent-context/runbooks/testing-patterns.md` Patterns 4 and 9 govern
  synchronized consumer updates and firing contract tests.

## Context and Key Files

Files to modify:

- `weft/context.py`
- `weft/core/queue_wait.py`
- `weft/helpers/__init__.py`
- `weft/commands/_load_support.py`
- `tests/tasks/test_tasks_simple.py`
- `tests/architecture/test_import_boundaries.py`
- `tests/commands/test_dump_load.py`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/10-CLI_Interface.md`
- this plan and `docs/plans/README.md`

Read first:

- `weft/commands/queue.py`: thin wrapper around `simplebroker.commands`.
- `weft/context.py`: owns Weft-scoped project discovery and broker-target
  resolution.
- `weft/core/queue_wait.py`: owns queue-backed command/result wait fallback.
- `tests/tasks/test_tasks_simple.py`: contains the legacy `BrokerDB`-input test.
- `tests/architecture/test_import_boundaries.py`: owns external import-boundary
  guardrails.
- `weft/helpers/__init__.py`: falls back through the unexported
  `simplebroker.commands.MAX_MESSAGE_SIZE` attribute when a supplied mapping
  omits the key, even though the public resolved config exposes
  `BROKER_MAX_MESSAGE_SIZE`.
- `weft/commands/_load_support.py`: validates dump IDs as non-negative, so
  reserved ID zero passes dry-run validation and fails only when SimpleBroker
  6.0 attempts exact insertion.

Current structure:

- `weft/commands/queue.py` already passes every option on `cmd_read`,
  `cmd_peek`, `cmd_move`, `cmd_watch`, and `cmd_list` by keyword. The 6.0
  signature change therefore needs verification, not a production-code edit.
- `weft/context.py` imports two project-config helpers through the legacy
  `simplebroker.project` path even though 6.0 exposes the same objects on
  `simplebroker.ext`.
- `weft/core/queue_wait.py` imports `QueueWatcher` from the implementation
  submodule instead of the supported package root.
- `tests/tasks/test_tasks_simple.py` imports the unlisted concrete `BrokerDB`.
  Its current test passes that object to `Consumer`, but `_resolve_db_target()`
  ignores unsupported object types and falls back to the current context, so
  the test does not prove the behavior its name claims.

Comprehension checks before editing:

1. Which SimpleBroker 6.0 surfaces own root watchers, extension/discovery
   helpers, and CLI-equivalent commands?
2. Does any proposed change alter the broker target, queue names, command
   output, or waiter lifecycle rather than only the import/binding contract?
3. Does load reject reserved exact ID zero before any alias or message write?

## Invariants and Constraints

- Preserve queue names, message IDs, delivery behavior, exit codes, stdout and
  stderr rendering, and Weft context precedence, except that dump message ID
  zero must now fail during validation because SimpleBroker 6.0 reserves it.
- Preserve the existing broker target passed to every SimpleBroker command and
  watcher. No second context-resolution or queue-wait path may appear.
- Keep every option after the required command operands keyword-bound.
- Use only SimpleBroker's supported root, `simplebroker.ext`, and
  `simplebroker.commands` surfaces in Weft-owned code and tests. Do not replace
  one legacy submodule reach with another.
- Derive a missing message-size value through public
  `simplebroker.resolve_config`; do not duplicate SimpleBroker's default in a
  Weft constant or depend on a non-exported command-module attribute.
- Load must reject ID zero before apply begins and before aliases or messages
  are written. Positive exact IDs and legacy zero selectors outside import
  remain unchanged.
- Replace the ineffective `BrokerDB` test with a public `BrokerTarget` input
  test that proves the supplied target is retained; do not preserve unsupported
  duck-typed input as an accidental contract.
- Do not change the user-owned `pyproject.toml` and `uv.lock` upgrade beyond
  preserving their SimpleBroker 6.0.0 and simplebroker-pg 3.5.0 floors.
- No dependency, public Weft API shape, CLI option, queue name, persistence
  format, or durable execution path changes. The only intended behavior delta
  is pre-write rejection of an SB6-invalid dump ID.
- Tests must use the installed/local SimpleBroker 6.0 implementation. Do not
  mock the upstream signatures or import exports being verified.
- Stop and re-plan if a required behavior cannot be expressed through the
  public 6.0 surfaces or if migration changes runtime semantics.

## Spec Baseline

- `834839bfa1488858fbc797a669906f6aa7f82fd9` —
  `docs/specifications/04-SimpleBroker_Integration.md` and
  `docs/specifications/10-CLI_Interface.md` at plan authoring time.
- Plan type: implementation with spec revision.
- Promotion baseline: `834839bfa1488858fbc797a669906f6aa7f82fd9` plus
  the current worktree diff for
  `docs/specifications/04-SimpleBroker_Integration.md` and
  `docs/specifications/10-CLI_Interface.md`. The reviewed strategy-B delta is
  promoted atomically with its tests and reciprocal `_load_support.py`
  reference; verify against `git diff 834839bf --` for those paths.

## Proposed Spec Delta

Promotion strategy: **B — atomic**. The requirement text, validation code,
firing tests, plan backlinks, and reciprocal code references land in one slice.

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/04-SimpleBroker_Integration.md` | B — atomic | [SB-0.2], Operational Notes |
| `docs/specifications/10-CLI_Interface.md` | B — atomic | [CLI-6] |

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2]

Insert after the current message-ID use list:

> SimpleBroker reserves message ID `0` as its lower-bound/checkpoint origin.
> Weft may select legacy zero-ID rows for recovery where SimpleBroker permits
> it, but it must not create or import a message with ID `0`.

Add to Operational Notes after the exact-ID import paragraph:

> Dump message records must carry positive integer IDs. Load rejects reserved
> ID `0` during validation, before aliases or messages are written.

### `docs/specifications/10-CLI_Interface.md` [CLI-6]

Insert after the `system load --dry-run` / `system load` bullet:

> - dump message records must carry a positive integer `id`; `system load`
>   rejects reserved ID `0` during validation before any writes, including in
>   dry-run validation

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Tasks

1. Add failing public-surface and reserved-ID gates.
   - Files: `tests/architecture/test_import_boundaries.py`.
   - Encode a deliberately narrower Weft policy than SimpleBroker's compatibility
     promise: new Weft consumer code uses package root, `simplebroker.ext`, and
     `simplebroker.commands`, even though upstream keeps legacy
   `simplebroker.project` imports valid.
   - Validate imported names and module-alias attribute reaches against each
     surface's `__all__`, while allowing the public `commands` module import
     from the package root. This must catch forms such as
     `from simplebroker import db` and `sb_commands.MAX_MESSAGE_SIZE`, not only
     dotted import statements.
   - Require dotted-module imports to use an explicit alias, so the guard can
     validate later attribute and `getattr` reaches without an unresolved
     `simplebroker.commands.*` chain. Add synthetic firing tests for aliased,
     unaliased, attribute, and `getattr` forms.
   - Scan production, test, integration, extension, and `bin/` Python sources
     so a test-only dependency on an unlisted implementation module cannot
     survive.
   - Observe the test fail on the existing `simplebroker.project`,
     `simplebroker.watcher`, and `simplebroker.db` imports before migration.
   - Files: `tests/commands/test_dump_load.py`.
   - Add a dump containing an alias plus a message with `id: 0`. Prove current
     validation accepts it, then require a clean validation error and verify
     apply mode writes neither aliases nor messages.
   - Stop if first-party backend package imports or sibling checkout source are
     accidentally included; the boundary is Weft-owned consumer code only.
   - Verify: `./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q`.

2. Promote the reserved-ID contract and migrate every discovered consumer.
   - Files: `weft/context.py`, `weft/core/queue_wait.py`,
     `weft/helpers/__init__.py`, `weft/commands/_load_support.py`,
     `tests/tasks/test_tasks_simple.py`,
     `docs/specifications/04-SimpleBroker_Integration.md`, and
     `docs/specifications/10-CLI_Interface.md`.
   - Import project-config discovery helpers from `simplebroker.ext`.
   - Import `QueueWatcher` from `simplebroker` root.
   - Replace the misleading `BrokerDB` case with a `BrokerTarget` case and
     assert the Consumer's resolved target equals the supplied target.
   - Resolve a missing broker message limit through the public SimpleBroker
     config resolver instead of `commands.MAX_MESSAGE_SIZE`.
   - Tighten dump validation from non-negative to positive IDs and use an
     error message that names the positive-integer requirement.
   - Add [SB-0.2] to `weft/commands/_load_support.py`'s module spec references
     so the promoted requirement has a reciprocal code owner.
   - Do not alter command behavior or add compatibility fallbacks.
   - Verify the architecture gate turns green plus the context, queue-wait,
     and task-simple tests.

3. Verify command-layer and dump/load compatibility, then close traceability.
   - Files: `docs/specifications/04-SimpleBroker_Integration.md`, this plan,
     `docs/plans/README.md`; production command code changes only if inspection
     finds a positional option missed by the inventory.
   - Exercise the real queue command suite against SimpleBroker 6.0.0. This is
     the post-change proof for the upstream keyword-only binding; no mock may
     substitute for it.
   - Add the plan backlink to both touched specs and reconcile implementation
     mappings. Mark the plan completed only after current-state verification,
     traceability verification, and independent completed-work review.
   - Stop if any command option remains positional or output/exit behavior
     changes.

## Testing Plan

Red-green proof:

- The new architecture test must first fail on the three current unlisted or
  legacy submodule imports and the non-exported command attribute, then pass
  after migration.
- The new dump/load test must first fail because reserved ID zero is accepted
  by preflight, then pass with validation occurring before any write.
- A separate pre-change runtime failure for `cmd_read` cannot be produced
  because Weft has passed its optional arguments by keyword since before 6.0;
  the root-cause proof is `inspect.signature()` on the installed 6.0 command
  functions plus source inspection of all five Weft call sites. The real
  `tests/commands/test_queue.py` suite is the required post-change correction
  proof.

Targeted commands:

```bash
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest tests/context/test_context.py tests/core/test_queue_wait.py tests/tasks/test_tasks_simple.py -q
./.venv/bin/python -m pytest tests/commands/test_queue.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_queue.py -q
./.venv/bin/python -m pytest tests/system/test_helpers.py -q
./.venv/bin/python -m pytest tests/commands/test_dump_load.py tests/commands/test_dump_load_sqlite_only.py -q
```

Final gates:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft tests/architecture/test_import_boundaries.py tests/tasks/test_tasks_simple.py
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
../backstitch/.venv/bin/backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --format json
```

Backstitch has no Weft-local configuration, and its sibling defaults point at
paths that do not exist in this repository. The explicit-root scan is therefore
the applicable probe. The current corpus has known pre-existing traceability
debt: the post-change scan reports 45 errors and 977 warnings. Completion does
not claim that backlog is cleared. It requires no error or warning diagnostic
against this plan or `weft/commands/_load_support.py`, plus green repository
plan-metadata/spec-hygiene tests and a reciprocal mapping check for [SB-0.2],
[SB-0.4], and [CLI-6]. Save the JSON report outside the repository.

Observable success: imports resolve through supported 6.0 surfaces; the real
queue commands retain their exit/output behavior; explicit and discovered
contexts resolve the same targets; queue waiters still start and close; and the
Consumer retains an explicitly supplied public `BrokerTarget`.
Dump dry-run and apply both reject ID zero before writes while positive-ID
round trips remain green.

## Rollout and Rollback

No ordered data or process rollout is required. The dependency floors already
select SimpleBroker 6.0.0. The code migration and that floor must ship together;
rolling code back requires rolling the dependency declaration back as well.
There are no persisted payload, identifier, or queue-format changes and no
one-way door. Existing dumps containing ID zero cannot be restored under
SimpleBroker 6.0 either before or after this Weft change; this migration makes
that incompatibility fail early and atomically. Runtime observation after install is the queue command smoke path
(`weft queue write`, `read`, `peek`, `move`, `list`) plus context discovery from
a nested project directory.

## Independent Review Loop

- Plan review: a separate agent reads this plan, the governing spec sections,
  the proposed spec delta, SimpleBroker's 6.0 changelog/API and message-ID
  specs, and the intended files. It
  answers PASS/BLOCKED on implementability and regression risk.
- Completed-work review: a separate agent reviews the final diff for missed
  SimpleBroker imports, positional command options, bogus tests, spec/plan
  drift, and missing verification.
- Each finding receives an explicit accept/decline disposition in this plan's
  review record before completion.

## Review Record

Plan review: PASS on the Class 5 revision.

Findings and dispositions:

- Accepted: `tests/commands/test_queue.py` does not fire every delegated
  keyword-only path. Added `tests/cli/test_cli_queue.py` to targeted proof.
- Accepted: the import gate alone would not prove the replacement message-size
  fallback. Added a direct missing-key test in `tests/system/test_helpers.py`.
- Accepted: reciprocal [SB-0.2] ownership in `_load_support.py` was implicit.
  Made the module-docstring update an explicit Task 2 requirement.
- Accepted: “every invalid exact ID” overclaimed the zero-only migration.
  Narrowed the comprehension check to reserved zero.

Reviewer found no blocking issue and independently confirmed that all five
affected command calls are already keyword-bound. The reviewer judged the
narrower Weft import/export gate proportionate, provided its allowed names are
derived from installed `__all__` surfaces rather than duplicated manually.

Completed-work review round 1: BLOCKED.

Findings and dispositions:

- Accepted: the import guard did not inspect reaches through an ordinary
  unaliased dotted import. Dotted SimpleBroker module imports now require an
  explicit alias, and synthetic cases fire the accepted and rejected forms.
- Accepted: the listed Backstitch command inherited the sibling repository's
  wrong default roots. Replaced it with the explicit Weft-root invocation and
  recorded the known corpus debt plus the slice-local no-new-diagnostic gate.
- Accepted: the `BrokerTarget` test checked only backend and normalized path.
  It now supplies sentinel backend options and asserts full target equality.
- Accepted: formatter-only rewrites outside the new architecture guard were
  reverted.

Completed-work review round 2: PASS.

The reviewer confirmed that all round-one findings are resolved and found no
new blocker. The import guard catches the dotted-import escape and fires its
synthetic probes; the explicit-root Backstitch slice filter has no error or
warning against this plan or `_load_support.py`; the full `BrokerTarget` is
retained; and unrelated formatter churn is absent.

Final verification evidence (2026-07-31):

- installed SimpleBroker: `6.0.0`; `uv lock --check` passed
- full suite: 2,478 passed, 3 skipped
- full repository mypy target: 195 source files, no issues
- targeted Ruff checks and `git diff --check`: passed
- plan metadata/spec hygiene: 8 passed
- explicit-root Backstitch: 344 sections, 45 known errors, 977 known warnings;
  zero error/warning diagnostics against this plan or `_load_support.py`

## Out of Scope

- SimpleBroker implementation or documentation changes.
- New queue features, aliases, timestamp parsing changes, or exact-ID behavior.
- Re-IDing legacy zero-ID dumps; callers must intentionally transform such
  input before restore.
- Refactoring Weft queue commands away from `simplebroker.commands`.
- Broad dependency lockfile cleanup unrelated to the user-provided 6.0 update.

## Fresh-Eyes Review

Completed on the Class 5 draft before external review. Findings:

- The first draft described the three-surface import allowlist as upstream's
  entire compatibility promise, but `simplebroker.project` remains valid.
  Updated Task 1 to state that this is a narrower Weft policy and to check
  imported names/attribute reaches, including AST forms a dotted-path scan
  would miss.
- The first draft named a nonexistent local `./bin/backstitch`. Updated the
  final gate to the sibling checkout executable; completed-work review then
  exposed that its default roots were for the sibling repository, so the gate
  now supplies Weft's roots explicitly and records the known debt baseline.
- The source audit found reserved exact ID zero and the unexported message-size
  reach after the Class 3 draft. Escalated to Class 5, added the exact proposed
  spec delta and atomic promotion slice, plus a pre-write regression.

Residual risk: the architecture gate must distinguish exported symbols from
submodule imports without rejecting the public `commands` module. Synthetic
firing cases cover the supported forms and permanent corpus scanning covers
the repository consumers.
