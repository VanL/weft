# CLI and Client Surface Parity, and the Shell Refusal Test

Status: promoted
Source specs: docs/specifications/09-Implementation_Plan.md [IP-1]; docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X1]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2], [PY-3], [PY-4]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-6]; docs/program-theory.md [WT-4] §4.4
Superseded by: none

Class: 5+P, with hardening applied. Normative spec text changes: the [IP-1]
client API parity matrix moves `weft system task-monitor` and
`weft system prune` from `cli_only` to client namespace methods and adds a
`cli_only` row for `weft init`, and [PY-2] and [PY-3] gain contract text.
The plan also edits `docs/program-theory.md`: owner ruling F2 (2026-11-03)
adds a declared `weft init` exception to the [WT-4] §4.4 row "The client
mirrors the CLI". A theory edit is class 5 by the theory's own rule (it is
"revised only through a class-5 plan"), and the spec changes are material,
so the base class is 5. A risky trigger also fires: the public Python API
changes (new `client.system` methods), which is a "public contract
changing" trigger in `docs/agent-context/runbooks/writing-plans.md` "When
Hardening Is Mandatory". The hardening checklist therefore applies, as does
review before implementation begins. The plan is +P under [DOM-15]: F3 (a)
adds `test_every_cli_verb_has_a_client_counterpart`, a standing gate
derived from the CLI that changes how every future CLI verb is verified (it
fails CI until the verb has a client counterpart or a declared `cli_only`
entry). The owner's 2026-11-03 principle, "If a test exists to put a
mechanical gate on some aspect of the theory, that is valid", makes the
gate valid; it does not make it less process-changing. Effective
requirements are class 5's plus pre-landing review from a different agent
family. (The draft said "not +P" on the ground that the gate checks a
product contract; that reasoning missed that the gate binds how future
verbs are verified, and it is withdrawn.) F3 (a) was confirmed by the owner
on 2026-11-03 ("You can use the derived parity gate"), so the gate and the
+P modifier stand.
Plan type: implementation with spec and theory revision. Promotion strategy
B (atomic) for every touched spec file and for the theory row; see
`## Proposed Spec Delta`.

**Owner approval for slice C** (new public client methods), required by
`CLAUDE.md` §8 ("Ask first: Changes to public API"), was given by the F1
ruling of 2026-11-03 ("we have met the recorded reason for deferring; we
should have parity"); no renewed approval is requested.
Slice S (the shell refusal test) changes no public API and can land after
the independent review alone.

This is plan 11 of the 2026-11 cleanup series. Its drift row is the audit
row 4.4-j "The client mirrors the CLI" in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (Slice C
audit table, marked `drifted`). The shell refusal test comes from the same
plan's §9 fork 2 (c), which assigns "the refusal test goes to cleanup plan
11".

## 1. Goal

Make the Python client cover the same verbs as the CLI, as
`docs/program-theory.md` [WT-4] §4.4 "The client mirrors the CLI" requires:
"The Python client and the CLI expose the same mental model and verbs over
one shared command-capability layer that both adapters call." A mechanical
parity check over all 41 registered CLI verbs (see `## Execution log`) finds
three verbs with no client counterpart: `weft init`, `weft system
task-monitor`, and `weft system prune`. Under the owner's 2026-11-03
rulings (§3), this plan adds the two `client.system` methods over the
existing command capabilities and declares `weft init` CLI-only, both in
[IP-1] and as an exception in the [WT-4] §4.4 theory row. It also adds a
test that derives the CLI-to-client mapping, so the same drift fails CI the
next time it happens. Separately, the plan adds the missing firing test for the
negative half of [WT-2a.10] ("a command runs as an argv list, never through
a shell"): `WeftClient.submit_command(..., shell=True)` must raise before
any queue write.

## 2. Premise corrections (read before the tasks)

The task brief that produced this plan described the two system verbs as
unintended drift. The spec does not support that reading:

1. **The governing spec explicitly marks the two system verbs `cli_only`.**
   `docs/specifications/09-Implementation_Plan.md` [IP-1], the "Client API
   parity matrix", has the rows "Foreground task monitor scan | `cli_only` |
   `weft system task-monitor` | Operator maintenance and cleanup
   diagnostics, not a stable task-client API." and "Runtime-state and
   retention prune commands | `cli_only` | `weft system prune` |
   Destructive/report/archive maintenance remains scoped to CLI until
   promoted with typed result contracts." The gap is therefore a conflict
   between the adopted theory ([WT-4] §4.4) and a spec that records a
   deliberate exception, not code that fell out of step with its spec. The
   reconciliation audit row 4.4-j cites [PY-1], [PY-2], and spec 11 but
   not [IP-1]. The decision rule in `docs/program-theory.md` [WT-4] §4.4
   ("Specs are what is; theory is where we go") makes revising [IP-1] a
   legitimate class-5 change. Because it reverses a recorded spec position,
   it needs the owner's decision (fork F1 below).
2. **The prune exception's stated precondition has been met.** [PY-2] now
   pins typed result contracts for both verbs: `SystemPruneResult(...)` and
   `TaskMonitorResult(...)` / `CommandStream[TaskMonitorSummary]`, under
   "Public command types". The [IP-1] reason for prune ("until promoted with
   typed result contracts") therefore no longer holds as written.
3. **A third gap exists.** `weft init` has no client counterpart and no
   [IP-1] row. The brief named only the two system verbs.
4. **[PY-1] does not change.** [PY-1] owns the `__all__` inventories. The
   new methods sit on an existing class (`SystemNamespace`), and
   their result types stay importable from `weft.commands`, the same way
   `SystemTidyResult` is today (it is returned by `client.system.tidy()`
   but is not in `weft.client.__all__`). The matrix lives in [IP-1], not
   [PY-1]. Only [IP-1], [PY-2], [PY-3], and [CLI-X1] change, plus the
   [WT-4] §4.4 theory row (F2(b)).
5. **The existing parity guard cannot detect drift.**
   `tests/core/test_client.py::test_client_api_parity_guard_matches_current_public_matrix`
   checks only that each method in the hand-written
   `CLIENT_API_PARITY_EXPECTATIONS` table exists (`hasattr`). The table is
   never derived from the CLI registrations, so a CLI verb with no client
   method is invisible to it. Extending the table is still the first RED, as
   the brief asked. Fork F3 adds the derived direction.

Points 1 to 3 hold at baseline `b7eed9f9`; the evidence is in
`## Execution log`.

## 3. Owner forks (decided 2026-11-03)

Owner ruling, 2026-11-03, verbatim: "Init is an exception. Otherwise, we
have met the recorded reason for deferring; we should have parity."

- **F1: Promote task-monitor and prune out of `cli_only`. Decided: (a).**
  Add `client.system.task_monitor` and `client.system.prune`, and revise
  the two [IP-1] rows as drafted in `## Proposed Spec Delta`. The recorded
  reason for deferring is the [IP-1] prune row's condition, "remains scoped
  to CLI until promoted with typed result contracts". It is met: [PY-2]
  "Public command types" now pins `SystemPruneResult(...)` and
  `TaskMonitorResult(...)` / `CommandStream[TaskMonitorSummary]` (§2 point
  2). The task-monitor row's claim, "not a stable task-client API", is
  retired by the same ruling. History: the alternative (b), keeping both
  `cli_only` under a declared theory exception, was not chosen.
- **F2: `weft init`. Decided: (b), with the theory exception granted.**
  `weft init` stays CLI-only. The [IP-1] matrix gains a `cli_only` row with
  the reason "Creates the project a client binds to; embedders call
  `weft.commands.cmd_init`, which returns `InitResult`." Because that keeps
  a CLI verb without a client counterpart, the plan also carries the
  declared exception against [WT-4] §4.4 "The client mirrors the CLI" as a
  proposed theory edit (the `docs/program-theory.md` Replacement block in
  `## Proposed Spec Delta`), which lands atomically with the [IP-1] row.
  The RECONSIDERATION REQUESTED raised by cross-family round 1 (F4) is
  answered by the ruling "Init is an exception." History: the alternative
  (a), a static method `WeftClient.init` over `cmd_init`, was the drafted
  recommendation and was not chosen; its task and delta text are deleted.
- **F3: Derived parity gate. Decided: (a), as the recommended default.**
  Add
  `tests/architecture/test_import_boundaries.py::test_every_cli_verb_has_a_client_counterpart`,
  which derives CLI leaf paths with the existing `_leaf_paths` helper. Any
  new CLI verb then fails CI until it has a client counterpart or a
  declared `cli_only` entry. F3 was first applied as this plan's
  recommended default under the parity ruling (the owner's words of
  2026-11-03 named `init` and parity, not the gate), because "we should
  have parity" is enforced only by a gate derived from the CLI itself; the
  owner then confirmed it on 2026-11-03: "You can use the derived parity
  gate." F3 (a) is ruled, and the gate, the [CLI-X1] sentence naming it,
  and the +P modifier stand. History: the alternative (b), relying only on
  the hand-written guard, was not chosen.

Not forks of this plan: the §12 observations (shape divergence inside
existing verbs; the stale `weft/cli/validate_taskspec.py` row in [CLI-X1])
are reported to the owner for later plans and are not decided here.

The tasks below assume F1(a), F2(b), and F3(a), as ruled. The F2
difference from the drafted assumption (F2(a)) is propagated: there is no
`WeftClient.init`; instead the [IP-1] delta adds the `weft init`
`cli_only` row and the theory delta adds the [WT-4] §4.4 clause. The
change from the drafted F2(a) is recorded in `## Revision Log`
(`docs/agent-context/runbooks/writing-plans.md` "Plan Lifecycle and
Retirement"), and the variants that were not chosen are deleted from the
delta, with one sentence of history each. The promoted-plan
Replacement-sync check in `bin/check-doc-paths` requires each Replacement
block to be contained in its target.

## 4. Source Documents

- `docs/program-theory.md` [WT-4] §4.4, row "The client mirrors the CLI";
  [WT-2a.10] "A Unix citizen", which quotes the owner's 2026-11-02 shell
  statement: "Weft does not support shell=True. The only way someone could
  get a shell is if they construct a task with a command line that creates
  a shell."
- `docs/specifications/09-Implementation_Plan.md` [IP-1]: the client API
  parity matrix, which is the governing exception list.
- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] (public surfaces;
  unchanged), [PY-2] (commands surface contract, signature rules, the
  "Client system dump, load, and tidy preserve the supplied resolved
  context" paragraph, typed errors and CLI exits), [PY-3] (client
  submission; `submit_command` override semantics), and [PY-4] (layering).
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` [CLI-X1] (verb to
  module table and the 41-verb bijection).
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] (`weft run`), and
  [CLI-6] (system maintenance commands, cited by `weft/commands/prune.py`
  and `weft/commands/tidy.py`).
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`: audit row
  4.4-j and §9 item 2 (c). This plan is complete and fully landed, and it is
  cited for provenance only.
- Guidance: `CLAUDE.md` §1.1, §4.11, §5.1, §8;
  `docs/agent-context/decision-hierarchy.md` [DOM-15];
  `docs/agent-context/runbooks/writing-plans.md`;
  `docs/agent-context/runbooks/hardening-plans.md`;
  `docs/agent-context/runbooks/adversarial-acceptance-probes.md`.

## 5. Context and Key Files

Current structure (verified at `b7eed9f9`):

- `weft/cli/app.py` registers 41 leaf verbs across `app`, `queue_app`,
  `alias_app`, `spec_app`, `task_app`, `manager_app`, and `system_app`.
  Each Typer callback calls exactly one `weft.commands.cmd_*` facade export
  ([PY-2], [PY-4]). This is enforced by
  `tests/architecture/test_import_boundaries.py::test_cli_verb_names_are_a_bijection_with_command_exports`.
- `weft/commands/__init__.py` lazily exports the 41 `cmd_*` functions,
  including `cmd_system_prune` (from `weft.commands.prune`) and
  `cmd_system_task_monitor` (from `weft.commands.task_monitor`).
- `weft/client/_namespaces.py::SystemNamespace` has `status`, `tidy`,
  `dump`, `builtins`, and `load`. Every method except `builtins` (which
  calls the context-free `builtins.list_builtins()`) passes
  `self.client.context`, a resolved `WeftContext`, to a context-taking
  command helper:
  `system.system_status`, `tidy.tidy_system`, `dump.dump_system`,
  `load.load_system`. The `cmd_*` functions take `context: Path | None` and
  call `build_context(spec_context=context)` themselves. The context-taking
  helpers exist so that the client's resolved Config, broker target, and
  directory name are preserved ([PY-2] "Client system dump, load, and tidy
  preserve the supplied resolved context").
- `weft/commands/prune.py`: `cmd_system_prune(*, family, context=None,
  apply=False, force=False, queue=None, min_age=..., keep_recent_per_key=...,
  keep_recent_per_task=..., task=None, retention_class=None, archive=None,
  limit=None, report=None) -> SystemPruneResult`. It validates options
  through `_validate_public_prune_options`, then calls `run_runtime_prune`
  and/or `run_retention_prune`. **Each of those calls
  `build_context(spec_context=config.context_path)` internally.** There is no
  resolved-context seam today.
- `weft/commands/task_monitor.py`: `cmd_system_task_monitor(*, context=None,
  follow=False, sink="stdout", log_dir=None, checkpoint=None,
  no_checkpoint=False, since=None, limit=None) -> TaskMonitorResult |
  CommandStream[TaskMonitorSummary]`. It builds `TaskMonitorConfig` and
  returns either `_TaskMonitorSummaryStream(config)` or
  `run_task_monitor(config)`. **`run_task_monitor` calls
  `build_context(spec_context=config.context_path)` internally.** There is
  no resolved-context seam today.
- `weft/commands/init.py::cmd_init(directory=None, *, autostart=True) ->
  InitResult`. It does not take a context; it creates one.
- `weft/commands/submission.py::_prepare_command` raises
  `NotImplementedError("submit_command(..., shell=True) is not supported")`
  before `shlex.split` and before any TaskSpec is built.
  `weft/client/_client.py::WeftClient.submit_command` forwards `shell` to
  it. `grep -rn "shell=True" tests/` returns 0 lines at baseline.
- Argv construction already has a firing test:
  `tests/specs/taskspec/test_process_target.py::test_command_args_appended_to_process_target`.

Files to modify (all exist; no new files):

- `weft/client/_namespaces.py`: `SystemNamespace.prune`,
  `SystemNamespace.task_monitor`
- `weft/commands/prune.py`: `prune_system`, `_prune_families`, and a
  `resolved_context` keyword on `run_runtime_prune` and
  `run_retention_prune`
- `weft/commands/task_monitor.py`: `task_monitor_system`,
  `_task_monitor_outcome`, and a `resolved_context` keyword on
  `run_task_monitor` and `_TaskMonitorSummaryStream`
- `tests/core/test_client.py`
- `tests/specs/taskspec/test_process_target.py` (slice S schema assertion)
- `tests/architecture/test_import_boundaries.py` (F3(a))
- `tests/commands/test_retention_prune.py`
- `tests/commands/test_task_monitor.py`
- `docs/specifications/09-Implementation_Plan.md`,
  `docs/specifications/11-CLI_Architecture_Crosswalk.md`,
  `docs/specifications/14-Python_API_Surfaces.md`
- `docs/program-theory.md` ([WT-4] §4.4 row "The client mirrors the CLI";
  F2(b) exception clause)
- `CHANGELOG.md` (`## [Unreleased]`, `### Added`)

Read first: the files above, plus `weft/commands/dump.py` and
`weft/commands/tidy.py`. They are the resolved-context pattern to copy:
`cmd_system_dump` and `dump_system` share `_dump_resolved_context`. Also
read `weft/commands/_boundary.py::typed_command_errors`,
`tests/commands/test_dump_load.py::test_client_dump_preserves_resolved_directory_and_broker`
(the alternate-context test pattern to copy), and
`tests/helpers/weft_harness.py::WeftTestHarness`.

Shared paths, which must not be duplicated:

- Option validation stays in `weft/commands/prune.py::_validate_public_prune_options`.
- The scan, checkpoint, and sink logic stays in
  `weft/commands/task_monitor.py::run_task_monitor`.
- Typed-error translation stays in `weft/commands/_boundary.py::typed_command_errors`.

### Comprehension questions (answer in the execution log before the first edit)

1. *Why must `client.system.prune` not be written as
   `prune.cmd_system_prune(context=self.client.context.root, ...)`?*
   Expected answer: `cmd_system_prune` rebuilds a context from the path
   with `build_context`. That discards the client's resolved `Config`,
   which may carry an alternate broker target or a non-default
   `WEFT_DIRECTORY_NAME`. [PY-2] requires client system maintenance to use
   the supplied resolved context. The path-based version would pass a
   happy-path test and fail the alternate-broker test in Task 4, which is
   why that test exists.
2. *Which modules may the new client code import, and how does a
   `CommandUsageError` raised by `client.system.prune(family="bogus")`
   relate to the CLI?* Expected answer: the new client code imports
   `weft.commands.*` modules (`prune`, `task_monitor`) and
   `weft._constants`, never `weft.core.*` or `weft.cli` ([PY-4]; enforced by
   `tests/architecture/test_import_boundaries.py`). The error is the same
   typed error that `cmd_system_prune` raises. The CLI maps it to exit code
   2, and that mapping does not change.

A wrong answer is a blocker: stop and re-read the files before editing.

## 6. Invariants and Constraints

Must not change:

- The one-way dependency graph `cli -> commands -> core` and
  `client -> commands -> core` ([PY-4]). The client never imports
  `weft.core.pruning` or `weft.core.monitor` directly.
- The 41-verb CLI bijection ([PY-2], [CLI-X1]). No CLI verb, flag, or
  default is added, removed, or renamed. `weft.commands.__all__` is
  unchanged: the new helpers `prune_system` and `task_monitor_system` are
  module-level helpers like `dump_system` and are not facade exports.
- `weft.client.__all__` (42 names at baseline) is unchanged.
- The signatures, defaults, validation, results, and typed errors of
  `cmd_system_prune`, `cmd_system_task_monitor`, and `cmd_init`.
- CLI exit codes: 0 success; 2 for `CommandUsageError`, `InvalidTID`,
  `TaskNotFound`, and `SpecNotFound`; 124 for `CommandTimeoutError`; 1 for
  any other `CommandError`; 3 for the spec-pinned `system load` alias
  conflict ([PY-2] "Typed errors and CLI exits"; [IMPL.1]).
- Prune stays a dry run unless `apply=True`. `force` still requires
  `apply`. Retention `apply` still requires `archive` unless `force` is set.
- `submit_command(..., shell=True)` keeps raising `NotImplementedError`
  before any TaskSpec is built or any queue is written. The exception type
  is pinned as it is today; turning it into `CommandUsageError` would be a
  behavior change and is out of scope.

Hidden couplings:

- `run_runtime_prune`, `run_retention_prune`, and `run_task_monitor` are
  called with a single positional config argument at 47 call sites in
  existing tests (`grep -rn "run_runtime_prune(\|run_retention_prune(\|run_task_monitor(" tests/ | wc -l`
  prints 47).
  The new `resolved_context` parameter must be keyword-only and default to
  `None`. When it is `None`, behavior must be exactly what it is today.
- `_TaskMonitorSummaryStream.__next__` re-calls `run_task_monitor` on every
  poll. It must pass the resolved context through on every call, not only
  on the first.
- Series plan 12 normalizes the prune `task` selectors in the body that
  Task 4 moves into `_prune_families`; that normalization belongs to the
  shared body, never to `cmd_system_prune` or `client.system.prune`, in
  either landing order (owner's outside reviewer, 2026-11-03; Task 4).
- The prune `details["runtime_state"]["config"]` and
  `details["retention"]["config"]` values come from `asdict(config)`, which
  includes `context_path`. On the client path, set
  `context_path=context.root` so the report names the real root rather than
  `None`.

Review gates: no new execution path (the client and the CLI share
`_prune_families` and `_task_monitor_outcome`); no new dependency; no CLI
shape change; no drive-by refactor (the observations in §12 stay out); no
mocks for broker or queue behavior in the new tests; independent
cross-family review before implementation (class 5 with hardening).

Difference register (no capability diminution):

| Kind | Item |
|------|------|
| A (added) | `client.system.prune`, `client.system.task_monitor` |
| N (no change) | Every CLI verb, flag, output, and exit code; every `cmd_*` signature; both `__all__` inventories; `weft init` stays CLI-only and `weft.commands.cmd_init` stays its embedding path (F2(b)) |
| C (changed) | None observable. Internal: three helpers gain a keyword-only `resolved_context=None`. Documents: [IP-1] gains a `weft init` `cli_only` row and the [WT-4] §4.4 theory row gains the matching declared exception (F2(b)) |
| D (diminished) | None |

## 7. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/09-Implementation_Plan.md` | B (atomic, landing with slice C) | [IP-1] parity matrix: the "Connect to a Weft context" row (a `weft init` `cli_only` row is added after it), and the four rows from "System status/tidy/dump/load/builtins" to "Runtime-state and retention prune commands" |
| `docs/specifications/14-Python_API_Surfaces.md` | B (slice C) for [PY-2]; B (slice S) for [PY-3] | [PY-2] client system paragraph and its `Implementation:` paragraph; [PY-3] closing sentence of the submit-override paragraph |
| `docs/specifications/11-CLI_Architecture_Crosswalk.md` | B (slice C) | [CLI-X1] paragraph after the table |
| `docs/program-theory.md` | B (atomic, landing with slice C in the same commit as the [IP-1] `weft init` row) | [WT-4] §4.4 table row "The client mirrors the CLI" |

Strategy B is chosen because each spec delta names symbols (`prune_system`,
`task_monitor_system`) that exist only once the code lands. Landing the
text first would create error-class debt. The theory clause and the [IP-1]
`weft init` row land together so that the theory never admits an exception
the spec does not record, and the spec never records one the theory does
not admit. All spec edits, the theory edit, the code, the tests, and the
backlinks land in one commit per slice.
In the worktree, apply the spec edits first in each slice (Task 2 and
Task 7), before any code that cites them.

### [IP-1] — row 1

This replaces the single row that opens "| Connect to a Weft context |" and
ends "no alternate runtime. |". The row is kept unchanged and one
`cli_only` row is added after it (F2(b), owner ruling 2026-11-03). History:
the drafted F2(a) row, listing `WeftClient.init` as a `client` static
method, was not chosen and is deleted.

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
| Connect to a Weft context | `client` | `weft.client.connect`, `WeftClient.from_context`, `WeftClient.from_weft_context` | Thin context resolution over `WeftContext`; no alternate runtime. |
| Initialize a project | `cli_only` | `weft init` | Creates the project a client binds to; embedders call `weft.commands.cmd_init`, which returns `InitResult`. |
```

### [IP-1] — system and exception rows

This replaces the four contiguous rows that open "| System
status/tidy/dump/load/builtins |" and end "until promoted with typed result
contracts. |". Clause audit of the removed text: the system row is kept
verbatim. The "Manager diagnostics" row is kept verbatim; it is a flag-level
exception, not a verb gap, and stays `cli_only`. The task-monitor row's
claim ("not a stable task-client API") is retired by F1(a). The prune row's
condition ("until promoted with typed result contracts") was met by [PY-2]
"Public command types". The prune row's safety content (destructive,
report, archive) is kept as the clause "dry run unless `apply=True`; same
... reports, and archive".

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
| System status/tidy/dump/load/builtins | `namespace` | `client.system.*` | Public maintenance helpers with typed results. |
| Foreground task monitor scan | `namespace` | `client.system.task_monitor` | Same scan, checkpoint, and sink behavior as `weft system task-monitor`, on the client's resolved context; returns `TaskMonitorResult`, or `CommandStream[TaskMonitorSummary]` iff `follow=True`. |
| Runtime-state and retention prune | `namespace` | `client.system.prune` | Same family validation, reports, and archive as `weft system prune`, on the client's resolved context; a dry run unless `apply=True`; returns `SystemPruneResult`. |
| Manager diagnostics | `cli_only` | `weft manager list --diagnostic` | Operator/debug output lacks a stable typed result contract. |
```

### [PY-2] — client system paragraph

This replaces the paragraph that opens "Client system dump, load, and tidy
preserve the supplied resolved" and ends "the context the session was
submitted with.", together with the following `Implementation:` paragraph
that opens "Implementation: `weft/commands/dump.py::dump_system`" and ends
"to the task-control owner.". The "Correction plan:" line after it is kept.
Clause audit: the dump default path, the load default input, and the
`RunSession.stop` context clause are all kept verbatim. The dump/load/tidy
implementation clauses are kept verbatim.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```markdown
Client system dump, load, tidy, prune, and task-monitor preserve the
supplied resolved context, including its broker target and artifact
directory. Without an explicit output, the export is `weft_export.jsonl` in
that context's Weft directory; without an explicit input, load reads that
same file. Task-monitor derives its default checkpoint and disk-sink
directory from that same context. A `RunSession.stop` likewise controls the
task on the context the session was submitted with.

`client.system.prune(*, family, apply=False, force=False, queue=None,
min_age=RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS,
keep_recent_per_key=RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY,
keep_recent_per_task=RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK,
task=None, retention_class=None, archive=None, limit=None, report=None)`
and `client.system.task_monitor(*, follow=False, sink="stdout",
log_dir=None, checkpoint=None, no_checkpoint=False, since=None,
limit=None)` take exactly the parameters, defaults, validation, typed
errors, and return branches of `cmd_system_prune` and
`cmd_system_task_monitor`, except `context`, which the client supplies.
`weft.client.__all__` is unchanged: these result types remain importable
from `weft.commands`, as `SystemTidyResult` is.

Implementation: `weft/commands/dump.py::dump_system` and `cmd_system_dump`
share the resolved-context materializer; `weft/commands/load.py` and
`weft/commands/tidy.py` share theirs the same way;
`weft/commands/prune.py::prune_system` and `cmd_system_prune` share
`_prune_families`; `weft/commands/task_monitor.py::task_monitor_system` and
`cmd_system_task_monitor` share `_task_monitor_outcome`; and
`weft/commands/run.py::_LiveRunSession.stop` passes its session context
to the task-control owner.
```

History: the drafted block also specified `WeftClient.init` and its
implementation clause; F2(b) removed both.

### [PY-3] — shell refusal (slice S)

This replaces the two-line sentence that opens "`ValueError`. Each
`submit(...)`, `submit_spec(...)`, and `submit_pipeline(...)`" and ends
"raises exactly what its `prepare*` counterpart raises.". The sentence is
kept and one sentence is appended. This is a clarification-only delta: the
behavior already matches the code.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):
```markdown
`ValueError`. Each `submit(...)`, `submit_spec(...)`, and `submit_pipeline(...)`
raises exactly what its `prepare*` counterpart raises.
`submit_command(...)` never runs a shell: a string command is split with
`shlex.split` into argv, and `shell=True` raises `NotImplementedError` before
any TaskSpec is built or any queue is written ([CLI-1.1.1]; program theory
[WT-2a.10]).
```

### [CLI-X1] — client counterpart rule

This replaces the paragraph that opens "Every registered CLI verb maps
bijectively to the public" and ends "and exit adaptation.". The paragraph is
kept verbatim and one paragraph is appended.

Replacement (`docs/specifications/11-CLI_Architecture_Crosswalk.md`):
```markdown
Every registered CLI verb maps bijectively to the public
`weft.commands.cmd_<full_cli_path>` export defined by [PY-2]. The owning
command function contains semantic validation and orchestration; Typer
callbacks contain only input parsing, formatting, stream routing, and exit
adaptation.

Every registered CLI verb also has a `weft.client` counterpart over the
same command capability, listed in the client API parity matrix in
`09-Implementation_Plan.md` [IP-1]; a verb without one appears there as
`cli_only` with its reason. `tests/architecture/test_import_boundaries.py`
derives the verb list from the Typer app and fails when a verb has neither.
```

History: under F3(b), not chosen, the last sentence of the appended
paragraph would have been dropped, because without the gate it would be
false; F3 (a) was confirmed on 2026-11-03, so the sentence stands.

### [WT-4] §4.4 — `weft init` exception (theory edit, slice C)

This replaces the single table row in `docs/program-theory.md` [WT-4]
§4.4 that opens "| The client mirrors the CLI |" and ends
"`11-CLI_Architecture_Crosswalk.md`; [PY-1] |". Current text, byte-exact
at `b7eed9f9`:

```text
| The client mirrors the CLI | The Python client and the CLI expose the same mental model and verbs over one shared command-capability layer that both adapters call. | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
```

Clause audit: the principle sentence, the Evidence cell, and the Contract
cell are kept verbatim. One clause is added before the sentence's final
period: the declared exception the owner granted on 2026-11-03 ("Init is an
exception."). Promotion: strategy B, atomic, in the slice C commit together
with the [IP-1] `weft init` `cli_only` row above. No [REV-WT-*] record is
proposed, because the clause declares one exception to an unchanged
principle rather than replacing a current account; the owner may ask for
one.

Replacement (`docs/program-theory.md`):
```markdown
| The client mirrors the CLI | The Python client and the CLI expose the same mental model and verbs over one shared command-capability layer that both adapters call, except `weft init`, which creates the project a client binds to; embedders call `weft.commands.cmd_init`. | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
```

Backlinks (same commit as each slice): append
`- [CLI and client surface parity](../plans/2026-11-02-cli-client-surface-parity-plan.md)`
under `## Related Plans` in `docs/specifications/09-Implementation_Plan.md`,
`docs/specifications/11-CLI_Architecture_Crosswalk.md`, and
`docs/specifications/14-Python_API_Surfaces.md`. Slice S adds only the spec
14 backlink; slice C adds the other two.

### [IP-1] and [CLI-X1] — implementation mappings (completed-work review round 1, F2)

Added after promotion (see `## Revision Log`, 2026-11-03 row 2). Neither
section declared an `_Implementation mapping_`, so every code citation of
[IP-1] or [CLI-X1], the four new ones included, drew backstitch BSC003. The
mappings record traceability that `CLAUDE.md` requires; they add no
normative behavior. The [IP-1] line is placed at the end of the
"Current Ownership [IP-1]" list (the parity matrix itself sits in [IP-1.1],
"Public Python Client Surface", but the code and the promoted text cite it as
[IP-1]); it is inserted, nothing is replaced. The [CLI-X1] line is appended
after the paragraph that ends "fails when a verb has neither.".

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
_Implementation mapping_: `weft/client/_namespaces.py::SystemNamespace.prune` over `weft/commands/prune.py::prune_system`; `weft/client/_namespaces.py::SystemNamespace.task_monitor` over `weft/commands/task_monitor.py::task_monitor_system`; the [IP-1.1] client API parity matrix's verb coverage is enforced by `tests/architecture/test_import_boundaries.py::test_every_cli_verb_has_a_client_counterpart`.
```

Replacement (`docs/specifications/11-CLI_Architecture_Crosswalk.md`):
```markdown
_Implementation mapping_: `weft/cli/app.py` registers the verbs; `tests/architecture/test_import_boundaries.py::test_every_cli_verb_has_a_client_counterpart` enforces the client-counterpart rule.
```

## 8. Rollback

All changes are additive. Rollback is `git revert` of the slice C commit,
which restores the two system `cli_only` rows, removes the `weft init`
`cli_only` row and the [WT-4] §4.4 exception clause together, and removes
the two methods; slice S reverts independently. Nothing persisted changes: no queue name, payload,
TaskSpec field, or file format. Once a release ships with the new methods,
removing them is a breaking public-API change. That one-way door is the
reason the owner approval gate exists. Rollout: one release, with an
`### Added` entry in `CHANGELOG.md` under `## [Unreleased]`. No deployment
ordering applies.

## 9. Tasks

### Slice S — shell refusal test (independent of F1 to F3; no public API change)

1. **Pin the shell refusal at the client seam.**
   - Outcome: a firing test proves that `shell=True` is refused before any
     TaskSpec is built and before any spawn request is written, and a pure
     schema test proves the TaskSpec boundary itself rejects `shell`.
   - Files to touch: `tests/core/test_client.py`;
     `tests/specs/taskspec/test_process_target.py`;
     `docs/specifications/14-Python_API_Surfaces.md` ([PY-3] Replacement
     above, plus the backlink).
   - Read first: `weft/commands/submission.py::_prepare_command`;
     `weft/client/_client.py::WeftClient.submit_command`;
     `tests/core/test_client.py::test_client_pipeline_and_command_raise_raw_type_error_for_unknown_override`
     (the neighbouring pattern).
   - Test: add `test_submit_command_rejects_shell_before_any_spawn_write`
     next to `test_submit_command_returns_task_with_completed_result`.
     Inside `with WeftTestHarness() as harness:`, create
     `client = WeftClient(path=harness.root)`. Use
     `with pytest.raises(NotImplementedError, match=r"shell=True"):
     client.submit_command(["echo", "hi"], shell=True)`. Then
     `assert client.queues.peek(WEFT_SPAWN_REQUESTS_QUEUE, all_messages=True) == []`,
     importing `WEFT_SPAWN_REQUESTS_QUEUE` from `weft._constants`. In the
     same test, add a second case with a schema-invalid command target:
     `with pytest.raises(NotImplementedError, match=r"shell=True"):
     client.submit_command([""], shell=True)`, followed by the same empty
     spawn-queue assertion. Without `shell`, `[""]` passes the
     `if not argv` check and then fails TaskSpec validation (`name` and
     `process_target` violate `min_length=1`; authoring probe in
     `## Execution log`), so `NotImplementedError` on this input proves the
     refusal precedes TaskSpec construction. Do not start a manager. The
     docstring cites [PY-3] and [WT-2a.10].
   - Schema test: add `test_spec_section_rejects_shell_as_unsupported_field`
     next to `test_process_target_requires_string` in
     `tests/specs/taskspec/test_process_target.py`. Use `with
     pytest.raises(ValidationError) as excinfo: SpecSection(type="command",
     process_target="echo", shell=True)`, then `assert [(err["type"],
     err["loc"]) for err in excinfo.value.errors()] == [("extra_forbidden",
     ("shell",))]`. Pure: no broker, no harness. The docstring cites
     [WT-2a.10] and `CLAUDE.md` §4.11 ("reject unsupported fields
     explicitly"). The argv half already has a firing test,
     `tests/specs/taskspec/test_process_target.py::test_command_args_appended_to_process_target`.
     Do not duplicate it.
   - Red proof: these are characterization tests of existing intended
     behavior, so they pass on first run. Prove each can fail with one local
     mutation at a time, record each failure line in the execution log, and
     restore by Edit; never use `git checkout`:
     - *Guard removed:* delete the `if shell:` guard in `_prepare_command`.
       The `["echo", "hi"]` case fails with `Failed: DID NOT RAISE <class
       'NotImplementedError'>`.
     - *Guard moved after TaskSpec validation:* move the `if shell:` block
       below the `validate_taskspec_payload(...)` call. The `["echo", "hi"]`
       case still passes; the `[""]` case fails because a TaskSpec
       `ValidationError` propagates instead of `NotImplementedError`.
     - *Schema guard removed:* delete `extra="forbid"` from
       `SpecSection.model_config` (or add a `shell` field). The schema test
       fails with `Failed: DID NOT RAISE <class
       'pydantic_core._pydantic_core.ValidationError'>`.
   - Constraints: no mocks; no change to `weft/` code.
   - Done when: both tests pass and the three mutation failures are logged.

### Slice C — client parity (owner approval given with the F1 ruling, 2026-11-03)

2. **RED: extend the hand-written guard, and add the derived gate (F3(a)).**
   - Files to touch: `tests/core/test_client.py`,
     `tests/architecture/test_import_boundaries.py`.
   - In `CLIENT_API_PARITY_EXPECTATIONS`, add `"prune"` and
     `"task_monitor"` to the `"system"` set. Nothing is added to the
     `"client"` set: under F2(b) there is no `WeftClient.init`.
   - Expected RED from
     `test_client_api_parity_guard_matches_current_public_matrix`:
     `assert not missing` fails with
     `missing == ['system.prune', 'system.task_monitor']`.
   - F3(a): add `test_every_cli_verb_has_a_client_counterpart`. Build it
     from the existing `_leaf_paths(typer.main.get_command(app))` helper
     and two module-level tables that **partition** the 41 derived `cmd_*`
     names. `CLIENT_COUNTERPARTS: dict[str, tuple[str, ...]]` maps a
     `cmd_*` name to one or more dotted attribute paths rooted at
     `WeftClient`, `Task`, or a namespace class (for example,
     `"cmd_run": ("WeftClient.submit", "WeftClient.submit_spec",
     "WeftClient.submit_pipeline", "WeftClient.submit_command")` and
     `"cmd_task_tid": ("TasksNamespace.resolve_tid",)`); its rows are the
     `## Execution log` parity rows for every verb not in `CLI_ONLY_VERBS`.
     `CLI_ONLY_VERBS: dict[str, str]` maps each declared exception to its
     [IP-1] reason. The test makes two assertions. First, the two key sets
     are disjoint and `set(CLIENT_COUNTERPARTS) | set(CLI_ONLY_VERBS) ==
     derived` (so their sizes sum to 41). Second, every counterpart tuple
     is non-empty (a verb mapped to `()` would otherwise pass both
     assertions with no counterpart and no declared exception; the
     cross-family round of 2026-11-03 reproduced that loophole, F1) and
     every dotted path in `CLIENT_COUNTERPARTS` resolves via `getattr`.
   - Table contents under the rulings F1(a), F2(b). This table is the
     single statement of the partition; every other section refers to it:

     | Owner outcome | `CLIENT_COUNTERPARTS` | `CLI_ONLY_VERBS` | Expected derived-gate RED (second assertion) |
     |---------------|-----------------------|------------------|----------------------------------------------|
     | F1(a), F2(b) | 40: all but `cmd_init` | `cmd_init`, mapped to the [IP-1] reason "Creates the project a client binds to; embedders call `weft.commands.cmd_init`, which returns `InitResult`." | fails on `SystemNamespace.prune`, `SystemNamespace.task_monitor` |

     History: the draft tabulated all four F1/F2 combinations; the three
     rows not chosen are deleted.
   - Record every RED (or mutation) output in the execution log.

3. **Apply the slice C spec and theory edits in the worktree.** Apply the
   [IP-1] (two blocks), [PY-2], and [CLI-X1] Replacement blocks, and the
   [WT-4] §4.4 `docs/program-theory.md` Replacement block (the `weft init`
   exception, which must land in the same commit as the [IP-1] `cli_only`
   row), plus the spec 09 and spec 11 backlinks. No code cites these
   sections yet.

4. **Prune: resolved-context seam, then the client method.**
   - Files to touch: `weft/commands/prune.py`,
     `weft/client/_namespaces.py`, `tests/commands/test_retention_prune.py`.
   - Test first:
     `test_client_prune_preserves_resolved_directory_and_broker` in
     `tests/commands/test_retention_prune.py`. Copy the context setup from
     `tests/commands/test_dump_load.py::test_client_dump_preserves_resolved_directory_and_broker`:
     a root default context, an alternate project, and
     `dataclasses.replace(configured, broker_target=alternate.broker_target,
     broker_config=alternate.broker_config,
     database_path=alternate.database_path)`. Seed the three task-log rows
     from `test_task_log_dry_run_reports_superseded_rows_without_deleting`
     (helpers `_write_json` and `_read_ids`) into the replaced context only.
     Call `WeftClient.from_weft_context(context).system.prune(family="task-log",
     min_age=0)` and assert that `isinstance(result, SystemPruneResult)`,
     `result.applied is False`, `result.families == ("task-log",)`, and
     `result.candidates == 2`. Assert that all three seeded IDs are still
     present. Finally assert that `cmd_system_prune(family="task-log",
     context=root, min_age=0).candidates == 0` (the seed reached only the
     supplied context's broker, so the `candidates == 2` assertion can only
     pass if the client scanned that broker).
     The runtime branch resolves its context separately, so the same test
     then covers it on the same replaced context. Seed one stale streaming
     marker as `tests/commands/test_runtime_prune.py::test_streaming_prune_deletes_terminal_owner_marker_only`
     does: a `WEFT_STREAMING_SESSIONS_QUEUE` row for a fresh TID, plus a
     `completed` `WEFT_GLOBAL_LOG_QUEUE` row for that TID, both written into
     the replaced context only (use that file's `_write_json` shape). Use a
     TID distinct from the task-log seeds. Call
     `client.system.prune(family="runtime-state", queue=["streaming"],
     min_age=0)` and assert `result.families == ("runtime-state",)`,
     `result.candidates == 1`, and that the marker is still present. Then
     assert `cmd_system_prune(family="runtime-state", queue=["streaming"],
     context=root, min_age=0).candidates == 0`. Mutations each half catches:
     dropping `resolved_context=` from the `run_retention_prune` call in
     `_prune_families` fails the task-log half (`candidates == 0`, not 2);
     dropping it from the `run_runtime_prune` call fails the runtime half
     (`candidates == 0`, not 1). Run both mutations once, record both
     failure lines in the execution log, and restore by Edit.
     Expected RED: `AttributeError: 'SystemNamespace' object has no
     attribute 'prune'`.
   - Implementation, with exact signatures:
     - `run_runtime_prune(config, *, report_path=None, resolved_context:
       WeftContext | None = None)` and the same keyword on
       `run_retention_prune`. Use `context = resolved_context if
       resolved_context is not None else
       build_context(spec_context=config.context_path)`. Change nothing
       else.
     - Move the body of `cmd_system_prune` (from validation through the
       `SystemPruneResult` return) into a private
       `_prune_families(*, resolved_context: WeftContext | None, context:
       Path | None, family: str, apply: bool, force: bool, queue:
       Sequence[str] | None, min_age: float, keep_recent_per_key: int,
       keep_recent_per_task: int, task: Sequence[str] | None,
       retention_class: Sequence[str] | None, archive: Path | None, limit:
       int | None, report: Path | None) -> SystemPruneResult`. It passes
       `resolved_context=` to both run helpers.
       `cmd_system_prune` keeps its decorator and exact signature and calls
       it with `resolved_context=None`. Cross-plan coupling (owner's
       outside reviewer, 2026-11-03): series plan 12
       (`docs/plans/2026-11-02-tid-display-encoding-plan.md`, Task 6)
       normalizes the `task` selectors (the encoded short form accepted in
       any case and canonicalized through `submission.normalize_tid`) at
       the point where they become `task_filters`, and its structural gate
       locates that function by the `task_filters=` binding, so the
       extraction here needs no inventory edit. If plan 12 landed first,
       the moved body already carries `normalize_tid` and the extraction
       moves it unchanged into `_prune_families`; if this plan lands first,
       plan 12 adds it inside `_prune_families`. Neither wrapper
       (`cmd_system_prune`, `client.system.prune`) normalizes on its own,
       so the client path never bypasses normalization. Plan 12's
       client-path selection case (a second run of
       `test_force_deletes_active_ping_in_selected_scope` through
       `client.system.prune(..., task=[encode_tid(tid)])`) un-skips once
       this task lands.
     - Add `@typed_command_errors def prune_system(context: WeftContext, *,
       family: str, apply: bool = False, force: bool = False, queue:
       Sequence[str] | None = None, min_age: float =
       RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS, keep_recent_per_key: int =
       RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY, keep_recent_per_task: int
       = RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK, task: Sequence[str] |
       None = None, retention_class: Sequence[str] | None = None, archive:
       Path | None = None, limit: int | None = None, report: Path | None =
       None) -> SystemPruneResult`. It calls `_prune_families` with
       `resolved_context=context` and `context=context.root`. Its docstring
       carries `Spec: docs/specifications/14-Python_API_Surfaces.md
       [PY-2]`. Add it to the module `__all__` only if the module declares
       one; `prune.py` does not, so add nothing.
     - `SystemNamespace.prune(self, *, <the same keyword parameters and
       defaults as prune_system>) -> SystemPruneResult` returns
       `prune.prune_system(self.client.context, ...)`. Extend the existing
       `from weft.commands import ...` line with `prune`. Import the three
       default constants from `weft._constants`.
   - Constraints: no `**kwargs`; no second validation path; no import of
     `weft.core.pruning` from the client.
   - Done when: the new test and all existing tests in
     `tests/commands/test_runtime_prune.py`,
     `tests/commands/test_retention_prune.py`, and
     `tests/cli/test_cli_system.py` pass.

5. **Task monitor: resolved-context seam, then the client method.**
   - Files to touch: `weft/commands/task_monitor.py`,
     `weft/client/_namespaces.py`, `tests/commands/test_task_monitor.py`.
   - Test first:
     `test_client_task_monitor_preserves_resolved_directory_and_broker`.
     Use the same replaced-context setup as Task 4, with `config=load_config({"WEFT_DIRECTORY_NAME":
     ".client-state"})` on the root. Write one valid completed task-log row
     into the replaced context only, using the payload shape of
     `test_foreground_scan_filters_malformed_rows_before_counts_and_checkpoint`
     (`_taskspec_payload`). Call
     `client.system.task_monitor(sink="disk", since=0)` with checkpointing
     on, then assert:
     `result.tids_seen == 1`; `result.log_path.parent ==
     context.logs_dir / TASK_MONITOR_LOG_SUBDIR` (inside `.client-state`);
     and the checkpoint file exists at
     `default_checkpoint_path(context)`. Add a second test,
     `test_client_task_monitor_follow_returns_closable_summary_stream`, on
     the same replaced context. Seed one completed row for TID A into the
     supplied context and one decoy completed row for a different TID into
     the root default context. Call `stream =
     client.system.task_monitor(follow=True, no_checkpoint=True, since=0)`
     and assert that `next(stream)` is a `TaskMonitorSummary` whose
     `record["tid"]` is A. Then seed TID B into the supplied context and
     assert that the next `next(stream)` (a second `run_task_monitor` poll)
     yields B. Call `stream.close()` twice. Seed before each `next`, because
     the stream sleeps between polls when nothing is found; the decoy makes a
     dropped `resolved_context` fail on the TID instead of hanging.
     Expected RED: `AttributeError: 'SystemNamespace' object has no
     attribute 'task_monitor'`.
   - Implementation, with exact signatures:
     - `run_task_monitor(config: TaskMonitorConfig, *, resolved_context:
       WeftContext | None = None) -> TaskMonitorResult`. Use `ctx =
       resolved_context if resolved_context is not None else
       build_context(spec_context=config.context_path)`, inside the
       existing `try`.
     - `_TaskMonitorSummaryStream.__init__(self, config, *,
       resolved_context: WeftContext | None = None)`. Store it and pass it
       on every `run_task_monitor` call in `__next__`.
     - Private `_task_monitor_outcome(config: TaskMonitorConfig, *,
       resolved_context: WeftContext | None) -> TaskMonitorResult |
       CommandStream[TaskMonitorSummary]`. It returns
       `_TaskMonitorSummaryStream(config, resolved_context=...)` iff
       `config.follow`, else `run_task_monitor(config,
       resolved_context=...)`. `cmd_system_task_monitor` keeps its exact
       signature and calls it with `resolved_context=None`.
     - `@typed_command_errors def task_monitor_system(context: WeftContext,
       *, follow: bool = False, sink: TaskMonitorSinkName = "stdout",
       log_dir: Path | None = None, checkpoint: Path | None = None,
       no_checkpoint: bool = False, since: int | None = None, limit: int |
       None = None) -> TaskMonitorResult | CommandStream[TaskMonitorSummary]`.
       It builds `TaskMonitorConfig(context=context.root, ...)` and returns
       `_task_monitor_outcome(config, resolved_context=context)`. The
       docstring carries the [PY-2] `Spec:` line.
     - `SystemNamespace.task_monitor(self, *, <the same keyword parameters
       and defaults>) -> TaskMonitorResult |
       CommandStream[TaskMonitorSummary]` delegates to
       `task_monitor.task_monitor_system(self.client.context, ...)`.
       Import `TaskMonitorResult`, `TaskMonitorSummary`, and
       `TaskMonitorSinkName` from `weft.commands.task_monitor`, and
       `CommandStream` from `weft.commands.types`.
   - Constraints: no `@overload` on `follow`. The command returns the
     union, and the mirror does too; narrowing both is a separate change.
     `monitor_name` stays unexposed, as in the command.
   - Done when: the new tests, all of `tests/commands/test_task_monitor.py`,
     and `tests/cli/test_cli_system.py` pass.

6. **Removed by owner ruling F2(b), 2026-11-03.** `WeftClient.init` is not
   added; `weft init` stays CLI-only, declared by the [IP-1] `cli_only` row
   and the [WT-4] §4.4 exception that Task 3 applies. History: this task
   specified the F2(a) static method and its test; the number is kept so
   cross-references stay stable.

7. **Adversarial acceptance probes for the new methods.**
   - Files to touch: `tests/core/test_client.py` (one parametrized probe
     test per method family, each named with `probe` so the §11 `-k`
     filter selects it). Each probe asserts the exact error class or
     field, and matches message text by substring only
     (`docs/agent-context/runbooks/adversarial-acceptance-probes.md`
     "Assertion Style"). The probes use a real broker via `WeftTestHarness`
     or `prepare_project_root` and no mocks:
     - **P1, no traceback / typed errors only (floor 1).**
       `client.system.prune(family="bogus")` raises `CommandUsageError`
       matching `unknown prune family`. `prune(family="runtime-state",
       force=True, apply=True)` raises `CommandUsageError`.
       `prune(family="task-log", apply=True)` raises `CommandUsageError`
       matching `--archive is required`.
       `client.system.task_monitor(sink="bogus")` raises
       `CommandUsageError` (pass the literal with
       `# type: ignore[arg-type]`), and `task_monitor(limit=-1)` raises
       `CommandUsageError`. None of these raises a bare `ValueError`,
       `KeyError`, or `OSError`.
     - **P2, exit-class honesty (floor 2).** Covered by P1:
       `pytest.raises(CommandUsageError)` pins the class the CLI maps to 2,
       and the existing
       `tests/cli/test_cli_system.py::test_system_prune_rejects_invalid_options`
       stays green unchanged. No separate assertion is added.
     - **P3, degenerate and malformed structured input.** Write a checkpoint
       file containing `{}` (parses, required keys missing), and another
       containing the truncated `{"schema_version":`. Call
       `task_monitor(checkpoint=path)`. Each raises `CommandExecutionError`
       whose message contains the checkpoint path.
     - **P4, unwritable output.** Call `task_monitor(sink="disk",
       log_dir=<a regular file>)`. It raises `CommandExecutionError`, and no
       JSONL file is created. Call `prune(family="task-log", min_age=0,
       report=<regular file>/sub/report.json)`. It returns a result whose
       `details["retention"]["errors"]` contains `failed to write report`.
       It does not raise, which is the spec'd report boundary already
       pinned for the command by
       `tests/commands/test_retention_prune.py::test_retention_apply_report_error_is_classified_after_delete`.
       A regular file stands in for a read-only directory so the probe
       behaves the same when run as root and on Windows.
     - **P5, missing sub-path.** Seed one task-log row (a checkpoint is
       written only when the scan yields a timestamp), then call
       `task_monitor(checkpoint=<missing dir>/cp.json)`.
       It succeeds and creates the checkpoint, because a missing
       checkpoint means "no prior run" in `_load_checkpoint`. The case is
       classified deliberately and pinned as such.
     - **P6, declared contracts fire (floor 5; config honesty).** Write
       `test_client_system_methods_mirror_command_signatures`. Using
       `inspect.signature`, the parameters of `SystemNamespace.prune` minus
       `self` must equal the parameters of `cmd_system_prune` minus
       `context`, in names, kinds, and defaults; the same holds for
       `task_monitor` against `cmd_system_task_monitor`. Any keyword not in the
       signature raises `TypeError`, because no method accepts `**kwargs`
       ([PY-2]).
     - Not applicable, with reasons: grammar mimicry (the methods parse no
       grammar); one bad input in a batch (no batch input; the per-task
       floor is unchanged because the scan path is shared); concurrency
       determinism (no new concurrency; the client and the CLI share one
       body); self-application (the README smoke commands `weft run`,
       `weft status`, and `weft result` do not change, and the final
       `tests/` gate in §11 covers them).
   - Done when: every probe passes. A probe that fails is a bug in the
     slice, and the probe must not be weakened.

8. **Traceability reconciliation and CHANGELOG.**
   - Add `Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]` and
     [IP-1] to the docstrings of the two new client methods. The
     `weft/client/_namespaces.py` module docstring already cites [IP-1]
     (confirm with `grep -n "IP-1" weft/client/_namespaces.py`), so it needs
     no edit.
   - `CHANGELOG.md` `## [Unreleased]` → `### Added`:
     "`client.system.prune` and `client.system.task_monitor` mirror `weft
     system prune` and `weft system task-monitor` on the client's resolved
     context ([IP-1], [PY-2])."
   - Rerun `bin/check-doc-paths`, `bin/check-dom15-fixtures`, and the
     backstitch gate in §11 Final gates.
   - Land slice C as one commit, by explicit file-list staging of the files
     in §5.

9. **Class-5 completion obligations ([WT-7]).** `docs/program-theory.md`
   [WT-7] "Possession probe, keyed" applies "At each release or class-5
   completion", and this plan is class 5. Both obligations use the existing
   mechanism; add no new audit framework.
   - *Keyed possession probe.* "the owner writes the key first: one
     hypothetical feature and where it belongs, one category error and the
     entry that refuses it, and the class of defect an audit of a named
     subsystem should find." An agent that did not author this plan, from a
     different family where one is available (via `skills/call-agent/SKILL.md`),
     answers from `docs/program-theory.md` alone and is graded against the
     key. Record the key and the result in `## Review Log`; the drawn key
     item is then spent. On FAIL, classify after disposition as a document
     deficiency, a key fault, or agent reasoning, and change the theory only
     for a document deficiency. "A self-graded probe is not a probe": the
     implementer may not grade it.
   - *Audit table refresh.* [WT-7]: "Refresh the `[WT-0]` theory-versus-code
     audit table at the same trigger, carrying unchanged rows forward by
     reference and re-auditing touched claims." The table is
     `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
     "### Audit table". This plan touches two rows: 4.4-j "The client
     mirrors the CLI" and I-10 → [WT-2a.10]. Re-audit both against the
     landed tree and record the refreshed rows in `## Execution log` under
     "Audit refresh"; every other row is carried forward by reference to
     that table. Expected outcome: I-10 stays `aligned` and its evidence
     gains the slice S tests. 4.4-j becomes `aligned` under the rulings
     F1(a) and F2(b): the two system verbs gain client counterparts, and
     `weft init` is covered by the [WT-4] §4.4 exception that lands with
     its [IP-1] `cli_only` row in the slice C commit. If that exception has
     not landed, 4.4-j stays `drifted`, naming this plan's theory edit.
   - Done when: the key, the graded result, and the two refreshed rows are
     recorded, and no row is re-graded without re-auditing it.

Stop and re-evaluate if any of the following happens: the client method
wants to import `weft.core.*`; a `cmd_*` signature or result must change; a
test needs a mock around queues or brokers; or the alternate-context test
can only pass by rebuilding a context from a path.

## 10. Testing Plan

- Harnesses: `WeftTestHarness` (`tests/helpers/weft_harness.py`) for the
  client round-trips and the shell refusal;
  `tests/helpers/test_backend.py::prepare_project_root` plus `build_context`
  and `dataclasses.replace` for the resolved-context tests. This is the
  established real-broker pattern in `tests/commands/test_dump_load.py`.
- Must not be mocked: queues, brokers, `build_context`, and the prune and
  monitor scans. Monkeypatching delegation (as
  `test_client_control_sweeps_delegate_to_structured_owner` does) is not an
  acceptable substitute for the alternate-context tests.
- RED order: Task 2 (guard, then derived gate) → Task 4 → Task 5.
  Each RED's exact failure line goes into the execution log.
- Invariants protected: the resolved-context tests protect [PY-2]; P2 and
  the unchanged `tests/cli/test_cli_system.py` protect the exit classes;
  P6 protects "mirrors the command capability";
  `test_cli_verb_names_are_a_bijection_with_command_exports` protects the
  41-verb bijection.
- Tempting to skip, but in scope: the follow-stream test, because the
  context must survive every poll, not only the first.
- Out of scope: retention `apply` with an archive through the client. The
  body is shared and covered by the command tests; P1 covers the guard.

## 11. Verification

Per task:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_client.py -q -k "parity_guard or shell or system_methods or probe"
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest tests/commands/test_retention_prune.py tests/commands/test_runtime_prune.py -q
./.venv/bin/python -m pytest tests/commands/test_task_monitor.py tests/commands/test_dump_load.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_system.py tests/specs/taskspec/test_process_target.py -q
```

Final gates, run from current state before claiming done. Scope: the bare
`pytest -m ""` discovers only `tests/`, because `pyproject.toml:139` sets
`testpaths = ["tests"]`. The integration and extension roots are outside it
and run through their own existing runners, the commands in
`.github/workflows/test.yml` (lines 306, 337, 364, 391) and
`bin/release.py` (`DJANGO_INTEGRATION_TEST_COMMAND`,
`DOCKER_EXTENSION_TEST_COMMAND`, `MACOS_SANDBOX_EXTENSION_TEST_COMMAND`,
`MICROSANDBOX_EXTENSION_TEST_COMMAND`). This plan changes no code under
those roots, but they import `weft.client` and `weft.commands`, so the four
runners are part of the full-suite claim. An extension root whose platform
or extra is unavailable locally is reported as not run, never as passed.

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""   # tests/ only (testpaths)
./.venv/bin/python -m pytest -q -n 0 integrations/weft_django/tests
./.venv/bin/python -m pytest -q -n 0 extensions/weft_docker/tests
./.venv/bin/python -m pytest -q -n 0 extensions/weft_macos_sandbox/tests
./.venv/bin/python -m pytest -q -n 0 extensions/weft_microsandbox/tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
../backstitch/.venv/bin/backstitch check --repo-root . --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --test-root tests --format json
grep -rn "shell=True" tests/   # expect >= 1 line (the new test)
git log --oneline -3            # completion claims are verified, not asserted
```

Success means every command exits 0; for backstitch, compare against a
baseline rerun and add no new issue. Parity acceptance is the derived test:
`tests/architecture/test_import_boundaries.py::test_every_cli_verb_has_a_client_counterpart`
passes with the `CLIENT_COUNTERPARTS` / `CLI_ONLY_VERBS` partition from the
Task 2 table (`cmd_init` is the one `cli_only` entry), and
`test_client_api_parity_guard_matches_current_public_matrix` passes with the
Task 2 additions. The authoring parity script in `## Execution log` is evidence
only; it is not rerun. The Task 9 [WT-7] probe and audit refresh are
recorded. `docs/plans/README.md` must carry this plan's
index row: `tests/specs/test_plan_metadata.py::test_plan_readme_indexes_every_plan_file`
and `bin/check-doc-paths` fail without it.

## 12. Out of Scope (observed, not fixed)

- Shape divergence inside verbs that already have counterparts. These are
  verb-level parity matches but not shape matches:
  `client.system.dump()` returns `Path`, where `cmd_system_dump` returns
  `SystemDumpResult`; `client.system.builtins()` returns
  `list[dict[str, Any]]`, where `cmd_system_builtins` returns
  `tuple[BuiltinSpecRecord, ...]`; and `client.system.load(input_file=)`
  takes a different parameter name from `cmd_system_load(input=)`. These
  are a candidate owner decision for a later plan.
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` [CLI-X1] names
  `weft/cli/validate_taskspec.py` in the `spec` row, and that file does not
  exist (`weft/cli/` contains only `__init__.py`, `app.py`, `run.py`).
  `bin/check-doc-paths` does not scan `weft/` paths. Report it to the
  owner; do not fix it in this plan.
- Changing `shell=True` to raise `CommandUsageError`, or removing the
  `shell` parameter.
- `@overload` narrowing of the task-monitor return type.

## 13. Fresh-eyes checklist for the reviewer

Does the [WT-4] §4.4 clause read as one declared exception rather than a
weakening of the principle, and does it land atomically with its [IP-1]
row? Is `resolved_context` the right seam, compared with passing a `WeftContext`
inside the config dataclasses? (The config dataclasses are public types
with fixed fields under [PY-2], so the plan does not change them.) Does the
derived gate duplicate the hand-written guard? (No: the guard pins the
client inventory, including client-only extras such as
`TasksNamespace.stats` and `QueuesNamespace.write_endpoint` (the
cross-family round of 2026-11-03 corrected an earlier example,
`TasksNamespace.runtime_liveness`, which the guard does not include); the
gate pins the CLI-to-client direction. The guard is only partly redundant
and is not deleted; removing its entries that the gate now covers is an
optional simplification.)

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9`: docs/specifications/09-Implementation_Plan.md,
  docs/specifications/11-CLI_Architecture_Crosswalk.md,
  docs/specifications/14-Python_API_Surfaces.md, and
  docs/specifications/10-CLI_Interface.md, at plan authoring time.
  Plan type: implementation with spec revision.
- Promotion baseline identifier: slice S `6054aea1`; slice C is the commit that
  flips this plan to `promoted` (the commit after `6054aea1`).

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 12 corrections — §5 SystemNamespace.builtins takes no context; comprehension Q2 admits `weft._constants` imports; Task 2 `CLI_ONLY_VERBS` contents under F1(b); [CLI-X1] delta names the sentence to drop under F3(b); Task 4 default-broker assertion relabelled as a seeding check; Task 5 follow test uses the replaced context, a decoy row, and a second poll so a dropped context fails instead of hanging; Task 6 no longer asserts a repeat init returns `created=False` (false on default SQLite, `created` tracks `broker.toml`); P2 folded into P1 (duplicate assertion); P5 seeds a row so a checkpoint is written; probe tests named for the §11 `-k` filter; the backstitch gate Task 8 cited is now a §11 command; §3 states the §12 observations are not forks of this plan; cross-family round 1: recorded below

- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED on question 1. F1 (runtime-prune context branch untested), F2 (ignored `autostart=False` undetected), F3 (contradictory gate tables and scratch-script acceptance), F5 (shell test does not reach the TaskSpec boundary), F6 ([WT-7] class-5 closeout obligations omitted), F7 (`testpaths` scope overstated): all P2, accepted and applied. F4 (P2): F2(b) now states the theory-exception consequence, RECONSIDERATION REQUESTED for the owner; fork unchanged. Round 2 (codex 0.161.0, 2026-11-02, scoped to the applied dispositions of F1–F3 and F5–F7, F4 wording only): no blocker; all six closed with live-code evidence (runtime-prune branch isolated by the streaming marker; `cmd_init` persists the supplied `autostart`; the 41-verb partitions are disjoint under all four fork combinations; `SpecSection(shell=True)` yields exactly `extra_forbidden` at `("shell",)`; [WT-7] obligations named; `testpaths` scope stated). F2(b) states RECONSIDERATION REQUESTED; the owner forks F1–F3 remain open. Review loop closed; the plan waits on the owner's rulings.
- owner rulings applied 2026-11-03: F1 (a) promote `task_monitor` and `prune`; F2 (b) `weft init` stays CLI-only with the declared [WT-4] §4.4 exception granted as a theory Replacement block (class 5); F3 (a) the derived parity gate, applied as the recommended default under the parity ruling, +P. Cross-family round 1 on the change (codex 0.161.0, 2026-11-03): PASS, two P2 applied without a further round (F1: the derived gate accepted an empty counterpart tuple; it now asserts every tuple is non-empty before resolving paths. F2: the hand-written guard's cited client-only example was wrong; corrected to `TasksNamespace.stats` and `QueuesNamespace.write_endpoint`, and the guard is kept, not deleted). The round confirmed the deferral premise ([IP-1]'s "until promoted with typed result contracts" is met by [PY-2]), the byte-exact theory and spec quotes, the 41-verb enumeration with 40 counterparts and `cmd_init` as the sole verb-level exception, class 5+P, and that the slice C approval line is satisfied by the recorded F1 ruling rather than a renewed request.
- owner confirmation applied 2026-11-03: F3 (a), the derived parity gate, confirmed ("You can use the derived parity gate"); the provisional wording is withdrawn; no item of this plan waits on the owner beyond the §12 observations routed to later plans and the [WT-7] closeout requirements. Reviewed baseline: `b7eed9f9` + worktree (the plan text closed by cross-family round 2, 2026-11-02).
- implementation 2026-11-03 (base `4372e20e`): slice S landed as `6054aea1`; slice C landed in the commit that promotes this plan. The [WT-4] §4.4 theory Replacement block was applied verbatim inside the slice C commit, atomically with its [IP-1] `cli_only` row as §7 requires; the owner accepted the series' theory edits on 2026-11-03 ("I accept the theory edits"), and the owner's 2026-11-03 ruling "Init is an exception" is the edit's source. Evidence and deviations: `## Execution log`. The [WT-7] keyed possession probe (Task 9) waits on the owner's key; the plan stays `promoted` until it is recorded.
- Cross-family completed-work review round 1 (codex 0.161.0, 2026-11-03, range 4372e20e..b85b798b): production diff confirmed (six Replacement blocks verbatim; `_prune_families` extraction byte-identical; Task 5 oracle stronger than planned; derived gate complete; theory row atomic per §7). BLOCKED on F1 (the §11 full-suite run was made but not recorded) and F2 (four new BSC003 warnings without disposition): accepted; the slow-suite result recorded, [IP-1] and [CLI-X1] given implementation mappings by Replacement (Revision Log row) and the backstitch baseline comparison recorded. F3 (function-local test imports) moved to module scope. Round 2 pending.
- cross-plan correction applied 2026-11-03 (owner's outside reviewer): `_prune_families` is the shared prune capability where series plan 12's `task` selector normalization lives, whichever plan lands first; the extraction carries or receives it, and neither adapter normalizes on its own; plan 12's client-path selection case un-skips once `client.system.prune` exists. Recorded in Task 4 and the hidden couplings. Plan metadata gate and `bin/check-doc-paths` re-run.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-03 | `b7eed9f9` + worktree | Header class line; §1; §3; §5; §6 register; §7 delta ([IP-1] row 1, [PY-2], [CLI-X1] note, new [WT-4] §4.4 block, strategy table); §8; §9 Tasks 2, 3, 6, 7, 8, 9; §10; §11; §13; Execution log | Owner rulings 2026-11-03 applied. F2 changed from the drafted assumption (a) to (b): `WeftClient.init` removed; the [IP-1] `weft init` `cli_only` row and a [WT-4] §4.4 theory Replacement block added, landing atomically. Class raised from 5 to 5+P because the F3 (a) derived gate changes how future CLI verbs are verified. Unchosen variants deleted from the delta with one sentence of history each. | Yes: done, cross-family round 1 on the change (codex 0.161.0, 2026-11-03): PASS, recorded in `## Review Log` (corrected 2026-11-03 at implementation; the cell had still said "pending") |
| 2026-11-03 | `b85b798b` | §7 (new "[IP-1] and [CLI-X1] — implementation mappings" block); Execution log; Review Log | Completed-work review round 1, F2: the four new BSC003 warnings came from [IP-1] and [CLI-X1] declaring no implementation mapping; two `_Implementation mapping_` lines added by Replacement and applied verbatim. F1 (slow-suite record) and F3 (module-scope test imports) applied in the same commit. | Yes: round 2 pending |

## Execution log

Authoring evidence at `b7eed9f9`, 2026-11-02. Counts come from command
output.

CLI verbs, derived by grep over the registrations in `weft/cli/app.py`
(the multi-line `@app.command(\n "run", ...)` decorator is matched as an
empty name and relabelled `root run`):

```text
$ grep -oE '^@(app|queue_app|alias_app|spec_app|task_app|manager_app|system_app)\.command\(\s*"?[a-z-]*' weft/cli/app.py | sed ... > cli_raw.txt
$ grep -c . cli_raw.txt
41
$ grep -c "\.command(" weft/cli/app.py weft/cli/run.py
weft/cli/app.py:41
weft/cli/run.py:0
$ python -c "from weft import commands; print(len([n for n in commands.__all__ if n.startswith('cmd_')]))"
41
```

Client surfaces, derived by grep over `weft/client/`:

```text
$ grep -n "^class \|    def [a-z]" weft/client/_namespaces.py weft/client/_client.py | grep -v "def _"
_client.py WeftClient: close, from_context, from_weft_context, submit, prepare, submit_spec,
  prepare_spec, submit_pipeline, prepare_pipeline, submit_command, task
TasksNamespace: list, stats, status, runtime_liveness, terminal_snapshot, ping,
  ack_terminal_snapshot, watch, resolve_tid, stop, stop_many, kill, kill_many
QueueAliasesNamespace: add, list, remove
QueuesNamespace: read, write, write_endpoint, peek, move, list, exists, stats, resolve,
  watch, delete, broadcast
ManagersNamespace: start, serve, stop, list, status
SpecsNamespace: create, list, show, delete, validate, generate
SystemNamespace (line 446): status, tidy, dump, builtins, load
```

Parity diff. The script maps each CLI verb to its expected client
attribute by the naming rule in [PY-2] (hyphens to underscores; `task tid`
maps to `resolve_tid`; root `status` maps to `client.system.status`;
`result` to `Task.result`; `run` to the four `submit*` methods), and checks
each with `hasattr`. The script lives in the session scratchpad and is not
part of the repository and is not an acceptance gate. Task 2's
`CLIENT_COUNTERPARTS` and `CLI_ONLY_VERBS` tables partition the same rows
as its fork table states.

```text
OK   weft queue read -> client.queues.read
OK   weft queue write -> client.queues.write
OK   weft queue peek -> client.queues.peek
OK   weft queue move -> client.queues.move
OK   weft queue list -> client.queues.list
OK   weft queue exists -> client.queues.exists
OK   weft queue stats -> client.queues.stats
OK   weft queue resolve -> client.queues.resolve
OK   weft queue watch -> client.queues.watch
OK   weft queue delete -> client.queues.delete
OK   weft queue broadcast -> client.queues.broadcast
OK   weft queue alias add -> client.queues.aliases.add
OK   weft queue alias list -> client.queues.aliases.list
OK   weft queue alias remove -> client.queues.aliases.remove
OK   weft spec create -> client.specs.create
OK   weft spec list -> client.specs.list
OK   weft spec show -> client.specs.show
OK   weft spec delete -> client.specs.delete
OK   weft spec validate -> client.specs.validate
OK   weft spec generate -> client.specs.generate
OK   weft task list -> client.tasks.list
OK   weft task status -> client.tasks.status
OK   weft task ping -> client.tasks.ping
OK   weft task stop -> client.tasks.stop
OK   weft task kill -> client.tasks.kill
OK   weft task tid -> client.tasks.resolve_tid
GAP  weft init -> WeftClient.init
OK   weft system tidy -> client.system.tidy
GAP  weft system task-monitor -> client.system.task_monitor
GAP  weft system prune -> client.system.prune
OK   weft status -> client.system.status
OK   weft result -> Task.result
OK   weft run -> WeftClient.submit, WeftClient.submit_spec, WeftClient.submit_pipeline, WeftClient.submit_command
OK   weft manager start -> client.managers.start
OK   weft manager serve -> client.managers.serve
OK   weft manager stop -> client.managers.stop
OK   weft manager list -> client.managers.list
OK   weft manager status -> client.managers.status
OK   weft system dump -> client.system.dump
OK   weft system builtins -> client.system.builtins
OK   weft system load -> client.system.load
verbs=41 gaps=3
```

Client-only extras, which have no CLI verb (not gaps; [WT-4] §4.4 asks the
client to cover the CLI, not the reverse): `TasksNamespace.stats`,
`runtime_liveness`, `terminal_snapshot`, `ack_terminal_snapshot`, `watch`,
`stop_many`, `kill_many`; `QueuesNamespace.write_endpoint`; and the
`prepare*` methods.

Spec exception evidence:

```text
$ grep -n "cli_only" docs/specifications/09-Implementation_Plan.md
173:| Manager diagnostics | `cli_only` | `weft manager list --diagnostic` | ...
174:| Foreground task monitor scan | `cli_only` | `weft system task-monitor` | ...
175:| Runtime-state and retention prune commands | `cli_only` | `weft system prune` | ...
```

Shell refusal evidence:

```text
$ grep -n "shell" weft/commands/submission.py
969:    shell: bool = False,
975:    if shell:
976:        raise NotImplementedError("submit_command(..., shell=True) is not supported")
$ grep -rn "shell=True" tests/ | wc -l
0
# probe: WeftClient(path=<fresh root>).submit_command(["echo","hi"], shell=True)
raised NotImplementedError submit_command(..., shell=True) is not supported
spawn rows []
# probe (2026-11-02, cross-family round 1 dispositions; pure, no manager):
# SpecSection(type="command", process_target="echo", shell=True)
SpecSection: [('extra_forbidden', ('shell',))]
# submission._prepare_command(ctx, [""])
ValidationError: 2 validation errors for TaskSpec (name: String should have at least 1 character; ...)
# submission._prepare_command(ctx, [""], shell=True)
NotImplementedError submit_command(..., shell=True) is not supported
```

Baseline tests green:
`tests/core/test_client.py::test_client_api_parity_guard_matches_current_public_matrix`,
`tests/architecture/test_import_boundaries.py::test_cli_verb_names_are_a_bijection_with_command_exports`,
`tests/specs/taskspec/test_process_target.py::test_command_args_appended_to_process_target`
(3 passed).

`weft.client.__all__` length at baseline: 42.

Owner decisions F1 to F3: ruled 2026-11-03; recorded in §3 with the owner's words (F1 (a), F2 (b) with the theory exception, F3 (a) as the recommended default).
Comprehension answers (implementer, 2026-11-03, before the first edit, at `4372e20e`):

1. `client.system.prune` must not call `prune.cmd_system_prune(context=self.client.context.root, ...)`
   because `cmd_system_prune` hands the path to `run_runtime_prune` and
   `run_retention_prune`, and each calls `build_context(spec_context=config.context_path)`.
   That rebuilds a fresh context from the root and discards the client's resolved
   `Config` (an alternate `broker_target` / `broker_config` / `database_path`, or a
   non-default `WEFT_DIRECTORY_NAME`). [PY-2] requires client system maintenance to use
   the supplied resolved context. A path-based mirror passes a happy-path test on the
   default broker and fails Task 4's alternate-broker test, which seeds only the
   replaced context's broker.
2. The new client code imports only `weft.commands.*` modules (`prune`, `task_monitor`,
   `weft.commands.types`) and `weft._constants`; never `weft.core.*` and never
   `weft.cli` ([PY-4]; `tests/architecture/test_import_boundaries.py::test_internal_import_boundaries`).
   `client.system.prune(family="bogus")` raises the same `CommandUsageError` that
   `cmd_system_prune` raises, because both reach `_validate_public_prune_options`
   through the shared `_prune_families`; the CLI maps that class to exit code 2, and the
   mapping does not change.

### Implementation, 2026-11-03 (base `4372e20e`)

Context: series plan 6 landed before this plan with the owner's always-on
services redirect (`c9369ee6`, `1b319004`, `4372e20e`). This plan touches
neither `weft/commands/system.py` nor the `weft status` surface, so nothing
here builds on or changes that shape. Series plan 10 already corrected the
stale `weft/cli/validate_taskspec.py` reference that §12 reports: the [CLI-X1]
`spec` row now names `weft/commands/specs.py`, `weft/cli/app.py` (verified at
`4372e20e`); nothing is redone here.

Task 1 (slice S). Added
`tests/core/test_client.py::test_submit_command_rejects_shell_before_any_spawn_write`
and
`tests/specs/taskspec/test_process_target.py::test_spec_section_rejects_shell_as_unsupported_field`;
applied the [PY-3] Replacement block and the spec 14 backlink. Both tests are
characterization tests and passed on first run (`2 passed`). Mutation proofs,
one at a time, each restored from a byte copy of the original file taken
before the mutation (no `git checkout`):

```text
# Guard removed (deleted `if shell:` in weft/commands/submission.py::_prepare_command)
E           Failed: DID NOT RAISE NotImplementedError
tests/core/test_client.py:606: Failed
# Guard moved below validate_taskspec_payload(...): the ["echo", "hi"] case passes,
# the [""] case fails on the TaskSpec error
E       pydantic_core._pydantic_core.ValidationError: 2 validation errors for TaskSpec
FAILED tests/core/test_client.py::test_submit_command_rejects_shell_before_any_spawn_write
# Schema guard removed (deleted extra="forbid" from SpecSection.model_config)
E       Failed: DID NOT RAISE ValidationError
tests/specs/taskspec/test_process_target.py:27: Failed
```

The installed pytest prints the class name without the plan's
`<class '...'>` wrapper; the failure lines are otherwise as the plan predicted.

Task 2 (RED). `CLIENT_API_PARITY_EXPECTATIONS["system"]` gained `prune` and
`task_monitor`; `tests/architecture/test_import_boundaries.py` gained the
module-level `CLIENT_COUNTERPARTS` (40 rows) and `CLI_ONLY_VERBS`
(`cmd_init` with the [IP-1] reason) tables and
`test_every_cli_verb_has_a_client_counterpart`, inserted after
`test_cli_verb_names_are_a_bijection_with_command_exports` without reordering
the file. The gate resolves each dotted path with `hasattr` against a root
table of `WeftClient`, `Task`, and the six namespace classes.

```text
E       AssertionError: assert not ['system.prune', 'system.task_monitor']
E       AssertionError: assert not ['SystemNamespace.prune', 'SystemNamespace.task_monitor']
```

Task 3. Applied verbatim: the two [IP-1] Replacement blocks, the [PY-2]
block, the [CLI-X1] block, and the [WT-4] §4.4 theory row; backlinks added to
specs 09 and 11 at the top of each `## Related Plans` list (both lists run
newest first), and the spec 14 backlink placed by slice S at the end of its
list was moved to the top for the same reason.

Task 4. RED before the namespace method existed:

```text
E       AttributeError: 'SystemNamespace' object has no attribute 'prune'
```

`run_runtime_prune` and `run_retention_prune` gained keyword-only
`resolved_context: WeftContext | None = None`; the body of `cmd_system_prune`
moved unchanged into `_prune_families` (the two run-helper calls gain
`resolved_context=`; nothing else changed, and no `normalize_tid`, which
series plan 12 adds); `prune_system` and `SystemNamespace.prune` added. GREEN
`1 passed`. Mutations, each restored from a byte copy:

```text
# resolved_context= dropped from the run_retention_prune call in _prune_families
>       assert result.candidates == 2
E       AssertionError: assert 0 == 2
# resolved_context= dropped from the run_runtime_prune call in _prune_families
>       assert runtime.candidates == 1
E       AssertionError: assert 0 == 1
```

Task 5. `SystemNamespace.task_monitor` was added together with
`SystemNamespace.prune`, so the RED landed one layer lower than the plan
predicted:

```text
E       AttributeError: module 'weft.commands.task_monitor' has no attribute 'task_monitor_system'
```

`run_task_monitor` and `_TaskMonitorSummaryStream` gained the keyword
(passed on every poll), plus `_task_monitor_outcome` and
`task_monitor_system`. GREEN `2 passed`. Deviation: the plan's
`result.tids_seen == 1` cannot hold, because the scan's own `TaskMonitor`
writes a `task_initialized` row for itself into `weft.log.tasks` before
scanning (probe on an empty project: `tids_seen == 1` with no seed). The test
instead asserts `summaries_emitted == 1` and that the one `task_summary` record
carries the seeded TID; the log-path and checkpoint assertions are as planned.
Mutations:

```text
# resolved_context= dropped from the __next__ poll in _TaskMonitorSummaryStream
E       AssertionError: assert '1778084345905438729' == '1778084345905438721'   (the decoy, no hang)
# resolved_context= dropped from the non-follow branch of _task_monitor_outcome
E       AssertionError: assert 0 == 1   (summaries_emitted)
```

Task 7. Probes in `tests/core/test_client.py`:
`test_client_system_prune_probe_raises_typed_usage_errors` (P1/P2, three
cases), `test_client_system_task_monitor_probe_raises_typed_usage_errors`
(P1/P2, two cases), `test_client_system_task_monitor_probe_rejects_malformed_checkpoint`
(P3, two cases), `test_client_system_probe_unwritable_outputs` (P4),
`test_client_system_task_monitor_probe_creates_missing_checkpoint_dir` (P5),
`test_client_system_methods_mirror_command_signatures` (P6, two cases), and
`test_client_system_methods_probe_reject_unknown_keyword` (P6 `TypeError`).
All pass on first run; none was weakened. §11 `-k "parity_guard or shell or
system_methods or probe"`: `14 passed`.

Task 8. Method docstrings carry the [PY-2] `Spec:` line and [IP-1]; the
module docstring already cited [IP-1] (line 5). CHANGELOG `### Added` entry
added under `## [Unreleased]`. `bin/check-doc-paths` OK;
`bin/check-dom15-fixtures` OK. Backstitch against a rerun at `4372e20e`:
errors 36 -> 36; warnings 1206 -> 1210; infos 720 -> 735 (after dropping two
optional test-docstring [IP-1] citations). The four new warnings are all
BSC003 "code backlink cites [IP-1] / [CLI-X1] but that section declares no
implementation mapping": `SystemNamespace.prune` and
`SystemNamespace.task_monitor` (Task 8 requires the [IP-1] citation; the
baseline already carries eight such [IP-1] warnings) and
`test_every_cli_verb_has_a_client_counterpart` ([IP-1], [CLI-X1], the
sections the gate enforces). The infos are BSC008 (new symbols cite [PY-2]).
Clearing the warnings needs an `Implementation:` mapping in [IP-1] and
[CLI-X1], which no Replacement block drafts; reported, not invented.

Task 9 ([WT-7]). Audit refresh, re-audited against the landed tree; every
other row of
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` "### Audit
table" is carried forward by reference:

| Row | Claim | Contract | Evidence (landed) | Status |
|-----|-------|----------|-------------------|--------|
| I-10 → [WT-2a.10] | Weft is a Unix citizen: argv not shell, stdin is data, exit codes are the status API | [CLI-1.1.1]; [PY-3] | `weft/commands/submission.py::_prepare_command` (`shell=True` refused before `shlex.split` and TaskSpec construction); `tests/core/test_client.py::test_submit_command_rejects_shell_before_any_spawn_write`; `tests/specs/taskspec/test_process_target.py::test_spec_section_rejects_shell_as_unsupported_field`; `tests/specs/taskspec/test_process_target.py::test_command_args_appended_to_process_target`; the two exit-code tests the table already lists | aligned |
| 4.4-j The client mirrors the CLI | The client and the CLI expose the same verbs over shared `weft/commands/` capabilities, except `weft init` | [PY-1]; [PY-2]; [IP-1]; [CLI-X1] | `SystemNamespace.prune` / `SystemNamespace.task_monitor` over `prune_system` / `task_monitor_system`, sharing `_prune_families` / `_task_monitor_outcome` with the `cmd_*` exports; `tests/architecture/test_import_boundaries.py::test_every_cli_verb_has_a_client_counterpart` (40 counterparts, `cmd_init` the one `cli_only` entry, matching the [IP-1] row and the [WT-4] §4.4 exception landed in the same commit) | aligned |

Keyed possession probe: not run. It needs the owner's key written first and
a different-family answerer; neither is available to the implementer, who may
not grade it. Open for the owner's closeout; the plan stays `promoted` until
the key and graded result are recorded in `## Review Log`.

Owner decisions: F1 to F3 were ruled before implementation (§3); no item
was open for the implement instruction to close. The theory edit was accepted
by the owner on 2026-11-03 ("I accept the theory edits").

Deviations from the plan text: (1) Task 5 `tids_seen` assertion replaced, as
above; (2) Task 5 RED is on `task_monitor_system`, not on the namespace
attribute; (3) backlink placement as in Task 3; (4) the theory row lands in
the slice C commit (the plan's atomic rule) rather than a separate
"Theory:" commit; (5) backstitch adds four BSC003 warnings, as in Task 8.

Slice C gates (worktree before the slice C commit): fast suite
`./.venv/bin/python -m pytest -q -p no:cacheprovider` exit 0; mypy "Success:
no issues found in 484 source files"; `ruff check .` "All checks passed!";
`ruff format --check weft tests bin` clean; `bin/check-doc-paths` OK (the
promoted-plan Replacement-sync check now covers this plan);
`bin/check-dom15-fixtures` OK; `tests/specs/test_plan_metadata.py` passed;
the four integration and extension roots (`integrations/weft_django/tests`,
`extensions/weft_docker/tests`, `extensions/weft_macos_sandbox/tests`,
`extensions/weft_microsandbox/tests`) each exit 0 with `-n 0`;
`grep -rn "shell=True" tests/` now returns lines (the slice S test).
`weft.client.__all__` and `weft.commands.__all__` are unchanged (pinned by
`tests/architecture/test_import_boundaries.py::test_official_python_surface_inventories_are_exact`).

Completed-work review round 1 dispositions (2026-11-03):

- F1. The §11 full suite was run after the slice C commit, at `b85b798b`:
  `./.venv/bin/python -m pytest -m "" -q -p no:cacheprovider`, exit 0, with no
  `FAILED` or `ERROR` line (the repository's quiet setting prints no count
  line; the last output line is a `SKIPPED` summary). The fix commit changes
  only test-module import placement and documents, so that result stands for
  the unchanged production code; the fast suite is re-run on the fix.
- F2. `_Implementation mapping_` lines added to [IP-1] and [CLI-X1] (§7
  block). Backstitch, `4372e20e` baseline rerun against the fixed tree:
  errors 36 -> 36; warnings 1206 -> 1196; infos 720 -> 741. No new error or
  warning; the four BSC003 warnings are gone, and so are ten baseline BSC003
  warnings on module-level [IP-1] / [CLI-X1] citations. New infos: 15 BSC008
  "code cites [PY-2] but is not in that section's implementation mapping"
  on this plan's new [PY-2] citations (the new prune and task-monitor
  helpers and tests); 8 BSC008 on baseline module-level [IP-1] / [CLI-X1]
  citations that were BSC003 warnings and are now info because the module is
  not named symbol by symbol; 2 baseline BSS007 infos ("section has no
  implementation mapping") are gone. Clearing the 15 [PY-2] infos would
  need new entries in an existing [PY-2] mapping, which the review did not
  ask for; reported, not added.
- F3. The three function-local imports in
  `test_every_cli_verb_has_a_client_counterpart` moved to module scope in
  `tests/architecture/test_import_boundaries.py`; no import cycle forced them
  (the file's fresh-import laziness probes run in subprocesses and still
  pass).
