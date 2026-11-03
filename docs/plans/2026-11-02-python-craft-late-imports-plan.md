# Python Craft: Late Imports and the Expert-Developer Test (owner decision first)

Status: draft
Source specs: None for behavior — code craft under existing contracts; edits CLAUDE.md §4.2 (a third permitted late-import reason, Task 3), the docs/program-theory.md [WT-3] ownership-table row "Agent runtime and session" (module locator only, Task 13), and path-only `_Implementation mapping_` notes in six spec files (Task 13); new comments cite docs/specifications/10-CLI_Interface.md [CLI-5] and docs/specifications/13-Agent_Runtime.md [AR-7]
Superseded by: none

Class: 5+P, re-classified 2026-11-03 when the owner's rulings on owner
items 2 and 4 put two edits outside the code into this plan. Class 5: Task 13
repairs one module locator in `docs/program-theory.md` (the "Agent runtime and
session" row of the [WT-3] ownership table, line 383), and the theory is
"revised only through a class-5 plan" (its header), so that one-line edit is
class 5 by the theory's own rule. No normative spec text changes: the six spec
files Task 13 touches change only module-path text inside `_Implementation
mapping:_` notes, which this plan treats as the traceability maintenance that
`CLAUDE.md` requires. +P: Task 3 amends `CLAUDE.md` §4.2. `CLAUDE.md` is agent
guidance (the conventions tier of `docs/agent-context/decision-hierarchy.md`),
and the amendment changes which late imports future work may write and which
ones reviewers accept, so it is material to how work is implemented and
reviewed. Effective requirements are class 5's (`max(4, 5)`) plus the
pre-landing different-family review that +P adds. The risky triggers that made
the plan class 4 still fire, so the hardening checklist still applies: Task 13
changes how `TaskRunner` (`weft/core/tasks/runner.py`) and `BaseTask` obtain
the built-in host runner plugin, and moves the host runner's two session
modules; Task 12 rewires where `TaskSpec` field validation imports its
adapter-ref validator. Both sit on the durable spine
(`TaskSpec -> Manager -> Consumer -> TaskRunner`), which is the first risky
trigger in `docs/agent-context/runbooks/hardening-plans.md` (rollback per task,
stop gates, invariants, what not to mock, observable success, review before
implementation begins; see "Hardening (Tasks 11–13)" in §4). The strengthened
acyclic-graph test (Task 11 widens and renames the existing eager-cycle test)
is an architecture guardrail for the owner's code rule; it does not change how
work is planned, reviewed, or verified, so the +P comes from the `CLAUDE.md`
edit alone. Promotion strategy: B (atomic) for every non-code edit, as
`## Proposed Guidance and Theory Delta` states. The `CLAUDE.md` §4.2 block
lands in the same commit as the code it governs (Task 3's spec-citing comments
and Task 4's accessor), and the theory row and the six mapping notes land in
the same commit as Task 13's module moves. This follows the sibling plans'
treatment of non-spec guidance edits: an exact block for `CLAUDE.md`, as
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` gives for
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, and a
proposed theory line that is not a Replacement block, as
`docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md` Task 5 gives for
its [ALT-WT-004] edit.

Class history, kept for the record: from 2026-11-02, when the owner's second
ruling added Tasks 11–13, the plan was class 4 (risky) on the durable-spine
trigger above. That claim said "Not class 5" because no spec behavior changed
and the theory locator was held outside the class under owner item 4, and
"Not +P" because the plan did not edit `CLAUDE.md`. The owner's 2026-11-03
rulings on items 2 and 4 brought both edits into this plan.

Original class claim (before Tasks 11–13), kept for the record: Class 3
(standard). It is multi-surface work: the edits reach `weft/commands/`,
`weft/core/`, `weft/cli/`, `weft/builtins/`, the root `weft/` modules, and
`docs/ruff-suppression-registry.md`. That fires the non-trivial trigger in
[DOM-15]. No risky trigger fires. Nothing changes on the durable spine. No
public contract, CLI shape, queue name, TaskSpec field, result payload, or
persisted format changes. Every edit moves an import, edits a comment or
docstring, deletes code with no references, or merges two identical method
bodies, so execution paths stay the same. That rules out class 4. No normative
spec text is edited, which rules out class 5. No gate, skill, or runbook
changes, so +P does not apply. Re-checked after the owner ruling (Reading B,
below): the claim still holds. The ruling adds hoists and drops the §4.2
amendment, so no `CLAUDE.md` edit and no class 3+P dependency remains. The
added hoists move imports only, and the import-latency rows they create are in
the §4 difference register, which is not a risky trigger. (Owner item 2, ruled
2026-11-03, later admitted a stated third reason to §4.2 and put that
`CLAUDE.md` edit in this plan; the class claim above supersedes this one.)

## Owner decision (ruled 2026-11-02)

**The question, as the audit put it**
(`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, audit row
4.4-c and "Rows that need the owner" item 4): three late imports of private
functions across `weft/commands/` modules carry the required cycle comment and
are legal under `CLAUDE.md` §4.2. Does a cross-module private-name import cycle
fail the expert-developer test ([WT-4] 4.4, "The expert Python developer test")?

**The three sites.** All three are in `weft/commands/run.py` and import from
`weft/commands/tasks.py`. All three carry the same comment, "Late import avoids
the tasks -> system -> run dependency closure."

| Site | Comment line | Imported name | Defined at | Enclosing function |
|------|--------------|---------------|------------|--------------------|
| `weft/commands/run.py:439` | 438 | `_task_control_result` | `weft/commands/tasks.py:2540` | `_LiveRunSession.stop` |
| `weft/commands/run.py:511` | 510 | `_terminal_observation_snapshot` | `weft/commands/tasks.py:405` | `_stop_interactive_run_session` |
| `weft/commands/run.py:524` | 523 | `_require_observation_config` | `weft/commands/tasks.py:1601` | `_stop_interactive_run_session` |

**Premise correction.** The tree falsifies two parts of the audit's framing.
Every command and its output are in the Execution log, probes P3 to P6.

1. *No import cycle exists.* Importing `weft.commands.tasks` in a fresh
   interpreter does not load `weft.commands.run` (P4). Nothing in `weft/` imports
   `weft.commands.run` at module scope except `weft/cli/run.py:28` (P5). The
   static graph used by `test_weft_has_no_eager_import_cycles`
   (`tests/architecture/test_import_boundaries.py:629`) stays acyclic with a
   module-scope edge `weft.commands.run -> weft.commands.tasks` added (P3). The
   comment does not say "cycle". It says "dependency closure", and the audit row
   read that as a cycle.
2. *What the deferral actually buys:* `import weft.cli.app` loads
   `weft.commands.run` but not `weft.commands.tasks`, `weft.commands.system`, or
   `weft.commands.events` (P6). Adding them costs 8 modules and about 5 ms on a
   125 ms CLI import (P6, three runs). The client (`import weft.client`) already
   loads all three, so the deferral helps only the CLI.

So the three imports are not cycle breaks. They are also not optional-dependency
guards. §4.2 as written permits only those two reasons, so as written they are
outside §4.2. This holds even though they carry a comment. The private-name part
of the question is not what makes them unusual. `weft/commands/run.py` already
imports six private names at module scope (`_annotate_accepted_submission_error`,
`_bind_accepted_taskspec`, `_resolve_submission_runtime_root`,
`_initial_work_payload`, `_submit_spawn_request_with_payload`,
`_register_observation_wakeup`; P7), and `weft/` has 31 private-name imports in
total (P8). The only thing that sets these three apart is the late placement and
a comment that suggests a cycle that does not exist.

The same pattern shows up elsewhere, and the tree measures what the deferrals
buy:

| Site | Real cycle? (P3/P9) | What deferral saves (P6, P10) |
|------|---------------------|-------------------------------|
| `weft/commands/run.py:385` `iter_task_realtime_events`, comment "breaks the real result/events cycle" | no | part of the same ~5 ms CLI saving (events alone: 8 modules, 4.8 ms) |
| `weft/core/manager.py:2139`, `:2143`, `:2147` (`PipelineTask`, `PipelineEdgeTask`, `HeartbeatTask`), no comment | no | nothing. `weft/core/manager.py:217` `from .tasks import Consumer` runs `weft/core/tasks/__init__.py`, which already imports all three |
| `weft/core/manager.py:2151`, `:2155` (`TaskMonitor`, `LivenessMonitor`), no comment | no | 22 modules, ~25 ms on every `import weft.core.manager`. Both `weft.cli.app` and `weft.client` load that module |
| `weft/core/manager_runtime.py:2289` `run_manager_process`; docstring says "without importing the CLI module", but `weft.manager_process` does not load the CLI | no | 3 modules, 0.2 ms |
| `weft/cli/run.py:221` `PromptSession`, comment "Optional dependency", but `prompt-toolkit` is a hard dependency at `pyproject.toml:31` | n/a | 26–56 ms. `weft.cli.app` does not load it today |
| `weft/core/agents/backends/__init__.py:17-18`, commented; [AR-7] says schema or validation imports alone do not load backend adapters, and `test_agents_import_does_not_register_or_load_builtin_backends` pins it | no | spec-required import order; owner item 2 |
| `weft/bootstrap.py:107` (`InvalidConfigError`) and `:110` (Typer `app`), no comment; [CLI-5] applies `WEFT_ENV_FILE` "before the full CLI imports" | no | spec-required import order; owner item 2 |
| `weft/commands/tasks.py:2478` `follow_task_events` | **yes**: `weft/commands/events.py:18` imports `weft.commands.tasks` | not applicable; legal |

**Ruling:** B (owner, 2026-11-02: "We systematically discourage lazy imports"). §4.2 is not amended; measured import economy is not a permitted reason.

**Ruling (second, owner, 2026-11-02):** "We should refactor out the lazy imports. With weft's architecture we should not have any cycles." The three late imports that close real cycles (`weft/commands/tasks.py:2478`, `weft/core/taskspec/parameterization.py:131`, `weft/_runner_plugins.py:45`) are not acceptable either. Each cycle is broken structurally so the import sits at module scope (Tasks 11–13). The bootstrap and backends sites are not cycles (P22) and stay owner item 2.

The owner's words in full: "Tell me what you mean by 'measured import economy'.
We systematically discourage lazy imports." The session lead read that as
Reading B, a literal `CLAUDE.md` §4.2: a function-level import is permitted only
to break a real import cycle or to guard an optional dependency. The ruling
accepts the D rows the hoists create (§4 difference register).

**What Reading B does (the tasks execute this).** Every late import in the
table above that is neither a real cycle nor an optional-dependency guard moves
to module scope, except two groups. The four spec-ordered sites in owner
item 2 (`weft/bootstrap.py:107`, `:110`; `weft/core/agents/backends/__init__.py:17-18`)
stay where they are under the third §4.2 reason the owner admitted on
2026-11-03 (Task 3). The prompt-toolkit import moves into a
`weft/cli/deferred.py` accessor under the named §4.2 exception the owner
granted the same day (owner item 1, Task 4). The rest move:

- `weft/commands/run.py`: the three `weft/commands/tasks.py` helper imports and
  the `iter_task_realtime_events` import at line 385 (the same kind: P3 shows no
  cycle). Task 1. No function needs to move. `weft/commands/tasks.py` already
  owns all three helpers and calls each of them itself (for example
  `weft/commands/tasks.py:745`, `:1124`, `:2630`), and
  `weft/client/_namespaces.py:159` also calls `tasks._task_control_result`. A
  deferred-accessor route does not fit: `weft/core/deferred.py` cannot name a
  `weft.commands` module, because core must not import commands (`CLAUDE.md`
  §4.1), and a new `weft/commands/deferred.py` would be a new file built to
  break a cycle that does not exist.
- `weft/core/manager.py`: `PipelineTask`, `PipelineEdgeTask`, `HeartbeatTask`
  (lines 2139, 2143, 2147) and `TaskMonitor`, `LivenessMonitor` (lines 2151,
  2155). Task 2.
- `weft/core/manager_runtime.py`: `run_manager_process` (line 2289). Task 2.
- `weft/cli/run.py`: the `prompt_toolkit` import (line 221) does not move to
  module scope. It moves into the `weft/cli/deferred.py` accessor under the
  named exception (owner item 1). `prompt-toolkit` is a hard dependency
  (`pyproject.toml:31`), so the "Optional dependency" comment is false and
  goes. Task 4.

What stays late after both rulings and the 2026-11-03 rulings on owner items
1 and 2: the optional `weft-docker` import at `weft/builtins/agent_images.py:38`,
with a true comment (Task 3); the prompt-toolkit import, inside the
`weft/cli/deferred.py` accessor under the named §4.2 exception (Task 4); and
the bootstrap and backends sites, under the third §4.2 reason (an import order
a governing spec requires), with comments citing [CLI-5] and [AR-7] (Task 3).
The three cycle sites (`weft/commands/tasks.py:2478`,
`weft/core/taskspec/parameterization.py:131`, `weft/_runner_plugins.py:45`)
no longer stay late: the second ruling breaks each cycle (Tasks 11, 12, 13).
The three inline `simplebroker_pg` imports guard an optional dependency and
route through `weft/core/deferred.py` accessors, the house pattern (Task 5).
No site is hoisted or restructured under owner item 2.

**Rejected alternative: Reading A.** Keep the deferrals, rewrite each comment to
state the measured saving, and amend `CLAUDE.md` §4.2 to admit a third reason,
import-closure economy on an entry path. Its case was that the house already
pins lazy closures with tests (`tests/architecture/test_import_boundaries.py:791`,
`:806`, `:816`, `:1026`) and that it avoids every latency row below. The owner
rejected it: the house discourages lazy imports, and measured startup savings
are not a permitted reason.

**All owner decisions in this plan (one list).**

1. CLOSED (owner, 2026-11-03): "Use the accessor with an exception."
   prompt-toolkit (Task 4). Former options, as history: hoist
   `import prompt_toolkit` to the `weft/cli/run.py` header (26–56 ms on every
   CLI start; the session lead's default), or route it through a
   `weft/cli/deferred.py` accessor with an explicit §4.2 exception recorded.
   What the closure changes: Task 4 creates `weft/cli/deferred.py` with one
   accessor, `get_prompt_session()`, and its module docstring records the
   exception ("the interactive prompt library costs 26–56 ms at import and is
   used only by interactive runs; the owner granted this one exception on
   2026-11-03"). The false "Optional dependency" comment goes. The six
   `PromptSession` patch sites in `tests/commands/test_active_interactive_lifecycle.py`
   are retargeted at the accessor. The prompt-toolkit D row in the §4
   difference register becomes an N row: `import weft.cli.app` still loads no
   prompt-toolkit module, so CLI start time does not change. An accessor is
   still a late import. The named exception, not the file placement, is what
   makes it legal under §4.2, and the Task 3 `CLAUDE.md` block says where such
   exceptions are listed.
2. CLOSED (owner, 2026-11-03): "Agree, keep the rule". The spec-ordered late
   imports that §4.2 as written did not cover: `weft/bootstrap.py:107` and
   `:110` ([CLI-5]: the env file is applied before the full CLI imports; the
   module docstring extends that to configuration helpers) and
   `weft/core/agents/backends/__init__.py:17-18` ([AR-7]: schema or validation
   imports alone do not load backend adapters; pinned by
   `test_agents_import_does_not_register_or_load_builtin_backends`). Former
   options, as history: (a) admit a stated third reason to §4.2, "import
   order required by a governing spec", citing [CLI-5] and [AR-7] (the
   recommendation); or (b) restructure each site to remove the need. What the
   closure changes: option (a). Task 3 amends `CLAUDE.md` §4.2 (the
   Replacement block in `## Proposed Guidance and Theory Delta`) so the
   permitted reasons become three: a real import cycle, an optional
   dependency, and an import order a governing spec requires, with the
   comment naming the spec section. Task 3 adds [CLI-5] comments at the
   bootstrap site and puts the [AR-7] code into the existing comment at the
   backends site. Neither site is hoisted or moved. Editing `CLAUDE.md` is a
   process change, so the class gains +P.
3. CLOSED (owner, 2026-11-03): "O3 sounds correct". Task 13 (cycle 3, the
   runner-plugin registry). Former options, as history: every shape that
   breaks this cycle leaves an observable difference, so the owner chose among
   O1 (a `host` entry point in `pyproject.toml`: a packaging-metadata change,
   ~1.6–2.0 ms per host resolution, and a reinstall before it works), O3 (the
   recommendation), and O4 (O1's entry point plus O3's two session-module
   moves, with import-time backend registration kept at host import, at O1's
   costs plus O3's module-path C rows). O1 and O4 stay in Task 13's option
   table as rejected by this ruling. What the closure changes: Task 13 runs
   shape O3 with no stop for a shape choice. Shape O3 moves the host runner's
   two session modules out of `weft/core/tasks/` (to
   `weft/core/runners/sessions.py` and
   `weft/core/runners/agent_session_protocol.py`), and moves host's
   import-time agent-backend registration to the two points where host runs
   an agent. Its differences are C rows:
   two module paths and one logger name change; the pinned test
   `test_host_import_registers_builtin_agent_backends` flips to say host import
   does not load backends; and registration moves inside the workers' run
   boundaries. In `_worker_entry` (`weft/core/runners/host.py:145`) it runs
   after `start = time.monotonic()` (`:159`), inside the `try` whose
   `except Exception` handler (`:197`, `RUFF-SUP-342`) maps a failure to an
   `execute`-phase error outcome, and inside `_worker_runtime_context` (task
   env and CWD applied, `:258`). In `_agent_session_worker_entry` (`:293`) it
   runs after the `booted` response (`:304`), inside `_worker_runtime_context`,
   and inside the handler (`:363`) that reports a `runtime_startup`-phase
   startup error. So a registration failure surfaces as a task failure with
   those diagnostics instead of failing at host import, a one-shot agent
   outcome's duration includes the ~42 ms registration, and registration runs
   under the task's env and CWD. Existing coverage, no new suite: one-shot
   success through the real spawned worker,
   `tests/tasks/test_agent_execution.py:294`
   (`test_task_runner_executes_agent_successfully`); execution under a
   task's env and CWD, `tests/tasks/test_agent_execution.py:305`
   (`test_task_runner_executes_provider_cli_agent_successfully`, `env` and
   `working_dir` set; it proves the agent runs under that context, and it
   would also pass if registration ran before `_worker_runtime_context`, so
   registration's placement inside the context is verified by reading the
   diff, not pinned by a test); the real persistent session,
   `tests/tasks/test_agent_execution.py:629`
   (`test_consumer_persistent_agent_per_task_continues_conversation`); the
   session startup-error handler, `tests/tasks/test_runner.py:4635`
   (`test_agent_worker_maps_startup_failure_and_closes_ipc`, in-process with
   `start_agent_runtime_session` patched: the real registration runs before
   the injected failure, so the test proves the handler maps a session-creation
   failure, not a registration failure). No existing test drives a
   registration failure in either worker; the plan adds none, by decision
   (cross-family round of 2026-11-03, F2: correct the prose, do not add a
   suite). The one-shot `execute` handler's error mapping is pinned for
   function targets (`tests/tasks/test_runner.py:2748`, `:2791`) and a
   one-shot provider failure is driven through the real worker by
   `tests/tasks/test_agent_execution.py:876`; neither injects a registration
   failure, which is the case left unpinned. No test in `tests/tasks/` or `tests/core/`
   asserts an outcome duration (grep for `outcome.duration` and
   `["duration"]`). Measured options are in Task 13.
4. CLOSED (owner, 2026-11-03): "Update the spec line". The theory locator
   stranded by the session-module move. `docs/program-theory.md:383`, the
   "Agent runtime and session" row of the [WT-3] ownership table, names
   `weft.core.tasks.sessions` in its Owner column as the owner for persistent
   scope. Former options, as history: (a) a class-5 one-line locator repair
   landed with Task 13; or (b) keep the old module path as a thin re-export
   of the moved module, which the house rule against compatibility shims
   disfavors. What the closure changes: option (a). Task 13 changes
   `weft.core.tasks.sessions` to `weft.core.runners.sessions` in that one row,
   in the same commit as the move (the proposed row is in
   `## Proposed Guidance and Theory Delta`). No re-export shim is kept at
   either old module path ([ALT-WT-012], backward-compatibility code for
   changed contracts). The theory is "revised only through a class-5 plan"
   (its header), so this plan is now class 5. Note for the owner, not a task:
   the reviewer observed that a concrete module locator in the theory is an
   altitude concern that predates this plan ([WT-0]: the theory states the
   conceptual ideal, and specs and code carry the exact current shape). This
   plan repairs the locator and does not reopen that wording.
5. Not decisions for this plan: the excluded findings E1–E16 go to the owner
   or to their own plans (E2 and E3 need a style ruling, E6 needs a call on
   public names, E15 is a dependency change). None of them blocks a task here.

## 1. Goal

Settle audit row 4.4-c with the owner rulings (Reading B, then "no cycles",
then the four rulings of 2026-11-03 on owner items 1–4). Then apply them, and
the other re-derived craft findings that hold, as small edits that leave
behavior unchanged. Every intra-weft import moves to module
scope, and the import graph is acyclic even when function-level imports and
package-initializer edges count (Tasks 11–13, gated by a strengthened
architecture test). The only function-level imports left are the optional `weft-docker`
guard (Task 3), the optional `simplebroker_pg` and `setproctitle` accessors in
`weft/core/deferred.py` (Task 5), the prompt-toolkit accessor in the new
`weft/cli/deferred.py` under a named §4.2 exception (Task 4, owner item 1),
and the owner item 2 sites (`weft/bootstrap.py:107`, `:110`;
`weft/core/agents/backends/__init__.py:17-18`), which stay late under a third
§4.2 reason that Task 3 adds to `CLAUDE.md`: an import order a governing spec
requires. Of those, only `bootstrap.py:110` and `backends/__init__.py:17-18`
import a weft module. Task 13 also repairs the one theory locator its module
move would strand (`docs/program-theory.md:383`, owner item 4).
Optional backend imports should sit behind `weft/core/deferred.py` accessors, as
house style requires. One verbatim duplicate method, eleven private helpers with
no references, two stale docstrings, and one over-armored branch should be gone.
Nothing observable changes. That covers queue traffic, results, exit codes, and
CLI output, plus import-closure latency, except the rows the ruling accepts in
the difference register.

## 2. Source Documents

- Source specs: none. This is code craft under existing contracts. The one
  spec-adjacent edit (Task 9) fixes a citation inside a code docstring so that
  it resolves to existing sections in
  `docs/specifications/10-CLI_Interface.md` ([CLI-1.1], [CLI-1.2], [CLI-1.3],
  [CLI-1.4]).
  No normative spec text changes. Task 13 edits path-only `_Implementation
  mapping:_` notes in six spec files (`00-Overview_and_Architecture.md`,
  `01-Core_Components.md`, `06-Resource_Management.md`,
  `07-System_Invariants.md`, `10-CLI_Interface.md`, `13-Agent_Runtime.md`).
  Task 3's new comments cite `docs/specifications/10-CLI_Interface.md` [CLI-5]
  (`WEFT_ENV_FILE` is applied "before the full CLI imports") and
  `docs/specifications/13-Agent_Runtime.md` [AR-7] (schema or validation
  imports alone do not load backend adapters).
- `docs/program-theory.md` [WT-3], the ownership table row "Agent runtime and
  session" (line 383): Task 13 changes its module locator (owner item 4).
  [ALT-WT-012]: no backward-compatibility shim at the old module paths.
- `docs/program-theory.md` [WT-4] 4.4: the rows "The expert Python developer
  test", "Shared function, shared code", and "No over-armoring". Also
  [ALT-WT-013]: size alone is not a finding, which is why the
  `weft/_constants.py` size observation is excluded below.
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (completed):
  audit row 4.4-c and "Rows that need the owner" item 4. This plan is the
  "plan 8" that row routes to. Its description of the row is corrected above.
- `CLAUDE.md` §1.1 (read before you judge), §4.1 (layering), §4.2 (the import
  rule and the `deferred.py` house style; Task 3 amends lines 347–352 under
  owner item 2), §5.1 (bite-sized tasks, DRY/YAGNI).
- `docs/agent-context/decision-hierarchy.md` [DOM-15] (class claim above).
- `docs/agent-context/engineering-principles.md` §4.1 (failing proof first;
  for these refactors the proof is the grep/AST gate that shows the site before
  and after the edit), §4.2 (repository-wide search before declaring something
  dead), §9 (counts come from commands).
- `docs/agent-context/runbooks/writing-plans.md` (this plan's shape).
- `docs/plans/2026-09-22-task-monitor-maintenance-worker-plan.md`: the evidence
  cited for the expert-developer test row. Its status is in
  `docs/plans/README.md`. It is context only, not a veto.

## 3. Context and Key Files

Files to modify (the tasks say which file belongs to which task):

- `weft/commands/run.py`: Task 1
- `weft/core/manager.py`: Tasks 2 and 5
- `weft/core/manager_runtime.py`: Tasks 2 and 7
- `weft/_runner_plugins.py`, `weft/builtins/agent_images.py`, `weft/bootstrap.py`,
  `weft/core/agents/backends/__init__.py` (comment only), and `CLAUDE.md`
  §4.2 (lines 347–352; owner item 2): Task 3
- `weft/cli/run.py`, new `weft/cli/deferred.py`, and
  `tests/commands/test_active_interactive_lifecycle.py` (six patch lines): Task 4
- `weft/core/deferred.py`, `weft/core/runtime_identity.py`: Task 5
- `weft/core/tasks/sessions.py`, `docs/ruff-suppression-registry.md`,
  `tests/specs/test_ruff_policy.py` (one pinned count): Task 6
- `weft/commands/tasks.py`, `weft/commands/system.py`, `weft/core/spec_store.py`: Task 7
- `weft/_constants.py`: Task 8
- `weft/commands/types.py`: Task 9
- `weft/commands/result.py`: Task 10
- `weft/commands/tasks.py`, `weft/commands/events.py`, new
  `weft/commands/_observation.py`, `tests/commands/test_task_commands.py`,
  `tests/commands/test_event_observation_connections.py`: Task 11
- `weft/core/taskspec/parameterization.py`, `weft/core/taskspec/run_input.py`,
  `weft/core/taskspec/model.py`: Task 12
- `tests/architecture/test_import_boundaries.py` (the existing
  `test_weft_has_no_eager_import_cycles`, strengthened and renamed): Task 11
  rewrites it, Tasks 12 and 13 make it pass
- Task 13, shape O3 (owner item 3): `weft/_runner_plugins.py`,
  `weft/core/runners/host.py`, `weft/core/tasks/debugger.py`, the moves
  `weft/core/tasks/sessions.py` to `weft/core/runners/sessions.py` and
  `weft/core/tasks/agent_session_protocol.py` to
  `weft/core/runners/agent_session_protocol.py`,
  `tests/tasks/test_task_execution.py`, `tests/tasks/test_runner.py`
  (including their package-form `from weft.core.tasks import sessions`
  imports),
  `tests/commands/test_active_interactive_lifecycle.py`,
  `tests/architecture/test_import_boundaries.py`,
  `docs/ruff-suppression-registry.md`, the path-only implementation
  mapping lines in six spec files (listed in Task 13), and one row of
  `docs/program-theory.md` (line 383; owner item 4)

Read first:

- `CLAUDE.md` §4.1 and §4.2.
- `weft/core/deferred.py`, the whole file (3 accessors). It is the house pattern
  for Task 5 and the model for Task 4's `weft/cli/deferred.py`.
  `get_processtitle` uses `import_module`. `get_setproctitle` does the import
  inside the function and carries a reason comment. Callers import the module
  and look the accessor up at call time (`import weft.core.deferred` and
  `weft.core.deferred.get_setproctitle()` in `weft/core/process_title.py:17`,
  `:50`), and tests patch the accessor on the module
  (`monkeypatch.setattr(deferred, "get_setproctitle", ...)`,
  `tests/core/test_process_title.py:34`, `:68`).
- `tests/architecture/test_import_boundaries.py`: `_iter_import_edges`,
  `test_weft_has_no_eager_import_cycles` (line 629), and the closure pins at
  lines 791, 806, 816, and 1026. The static edge parser does not model the edge
  that a package's `__init__.py` adds. That is why `weft/_runner_plugins.py:45`
  shows "no cycle" statically but is a real runtime cycle (P9, P11).
- `docs/ruff-suppression-registry.md` rows `RUFF-SUP-344` (line 184) and its
  index row (line 385), and the global raw-`noqa` inventory (line 213), before
  Task 6.

Shared paths. Reuse them and do not duplicate them:

- `weft/core/deferred.py` is the only home for optional-dependency accessors in
  core.
- `weft/commands/tasks.py` owns `_task_control_result`,
  `_terminal_observation_snapshot`, and `_require_observation_config`. Do not
  move or rename them. `weft/client/_namespaces.py:159` and `:179` also call
  `_task_control_result`.

Two facts the edits depend on:

1. *Cycles.* `from .tasks import _task_control_result` in `weft/commands/run.py`
   can move to module scope, but `from .events import follow_task_events` in
   `weft/commands/tasks.py` cannot until Task 11 lands:
   `weft/commands/events.py:18` imports `weft.commands.tasks` at module scope,
   so a tasks-to-events edge closes a cycle (P3), while nothing that
   `weft.commands.tasks` loads imports `weft.commands.run` (P4). Counting
   function-level imports and the edge a submodule import adds to its package
   `__init__.py`, the tree at `b7eed9f9` has exactly three import cycles (P22):
   events/tasks, taskspec model/parameterization/transport, and a 14-module
   cycle through `weft/_runner_plugins.py`, `weft/core/runners/host.py`, and
   the `weft.core.tasks` package.
2. *Patch boundary.* `tests/commands/test_active_interactive_lifecycle.py:263`,
   `:297`, `:410`, `:585`, `:669`, and `:759` patch
   `prompt_toolkit.PromptSession` at call time. A module-scope
   `from prompt_toolkit import PromptSession` binding would ignore the patch; a
   module-scope `import prompt_toolkit` with an attribute lookup in the function
   honors it (P21). Under owner item 1 the import stays inside an accessor, so
   the patch would still take effect; Task 4 retargets all six sites at the
   accessor anyway, so tests patch the seam the code owns, as
   `tests/core/test_process_title.py` does for `weft/core/deferred.py`.

## 4. Invariants and Constraints

- No behavior change. Queue traffic, state events, result payloads, exit
  codes, error types, error messages, and CLI output stay the same.
- No new abstraction. No base class, mixin, protocol, or registry. Task 5
  adds accessor functions to the existing `weft/core/deferred.py`. Task 4
  creates `weft/cli/deferred.py` with one accessor, the house pattern applied
  to the CLI package (owner item 1). Task 6 adds
  one module-private function inside `weft/core/tasks/sessions.py`. Those are
  the only new definitions. One new module, from the second ruling: Task 11
  creates `weft/commands/_observation.py` to hold seven existing helpers moved
  verbatim. The existing homes do not fit: the reducer is pinned pure and may
  not import `time` (`tests/commands/test_task_snapshot_reducer.py:486`,
  `test_reducer_has_no_io_or_runtime_probe_dependencies`), and
  `weft/commands/_result_wait.py` owns result settlement, not snapshot
  synthesis. Task 13 moves two modules and adds no definitions.
- No drive-by refactor. Touch only the named lines. Do not rename private
  helpers, change absolute imports to relative ones, or reorder unrelated
  imports. `ruff check --fix` may reorder an import block that a task edits.
- Layering stays one-way (`CLAUDE.md` §4.1): `weft/core/` never names a
  `weft.commands`, `weft.cli`, or `weft.client` module, not even as an
  `import_module` string.
- Import-closure pins stay green (`tests/architecture/test_import_boundaries.py`).
  SQLite processes never load `simplebroker_pg`. The fresh-interpreter probe in
  Task 5 proves it; `tests/core/test_runtime_identity.py:314-318` only checks
  that module presence is unchanged around `install_runtime_identity`.
- The `RUFF-SUP-344` fail-open tradeoff stays as it is: a monitor failure still
  stops the monitor and returns `(True, None)`. Task 6 moves the code. It does
  not change the policy.
- No new dependency and no `pyproject.toml` edit. (Task 13 shape O1 would
  have edited `pyproject.toml`; the owner chose O3, owner item 3.)
- After Tasks 11–13 the import graph of `weft/` is acyclic with every
  non-`TYPE_CHECKING` import counted, function-level ones included, plus the
  package-initializer edge each submodule import implies. The strengthened
  test (Task 11) holds this.
- External review: this is a class 5+P plan, so it needs independent review of
  the plan, of the `CLAUDE.md` block and the theory row before they land, of
  the completed work, and a pre-landing review (+P), each from a different
  agent family through `skills/call-agent/SKILL.md`.

Difference register. Each row is classified Added, None, Changed, or Diminished.
Every D row is a measured latency that the ruling (Reading B, 2026-11-02)
accepts; none is pending. P21 measured the combined cost of all hoists on
`import weft.cli.app` at about 56–61 ms and 225 modules over a ~130 ms
baseline (tasks+events 4.7 ms, monitors 22.5–25.8 ms, `weft.manager_process`
0.1 ms, prompt-toolkit 28.1–30.3 ms); the cross-family review reproduced
56–63 ms and 225 modules. The method times sequential increments in fresh
interpreters, so it estimates the additional import work. It is not an exact
before/after benchmark of the final eager import order. Owner item 1
(2026-11-03) took prompt-toolkit out of the hoists, so its 28.1–30.3 ms and
196 modules no longer count. The D rows that remain add an estimated
26–35 ms and about 29 modules (the 56–63 ms and 225 modules above, less the
prompt-toolkit increment). That figure is a subtraction, not a measurement;
the Verification timing measures it.

| Change | Class | Measured | Accepted? |
|--------|-------|----------|-----------|
| Task 1: hoisting the four `weft/commands/run.py` imports adds tasks, system, and events to `import weft.cli.app` | D (latency) | +8 modules, ~5 ms of ~125 ms (P6) | accepted by the ruling |
| Task 2: hoisting `PipelineTask`, `PipelineEdgeTask`, `HeartbeatTask` | N | already in the closure of `weft.core.manager` (P10) | n/a |
| Task 2: hoisting `TaskMonitor` and `LivenessMonitor` into `weft/core/manager.py` adds the monitor modules to every `import weft.core.manager` (CLI and client) | D (latency) | +22 modules, ~25 ms (P10) | accepted by the ruling |
| Task 2: hoisting `run_manager_process` into `weft/core/manager_runtime.py` | D (latency, small) | +1 module, ~0.1 ms on the `weft.cli.app` closure; +3 modules, 0.2 ms on a bare `import weft.core.manager_runtime` (P10) | accepted by the ruling |
| Task 4: the prompt-toolkit import moves from an inline `try` in `weft/cli/run.py` into the `weft/cli/deferred.py` accessor (owner item 1) | N | the import still runs only when an interactive run starts; `import weft.cli.app` loads no `prompt_toolkit` module before or after (fresh-interpreter check in Task 4). The D row a hoist would have created (26–56 ms on every CLI start; P10, P21) is not incurred, so CLI start time does not change | n/a (owner item 1 replaced the hoist with the accessor) |
| Task 4: dropping the `ImportError` guard around a declared dependency | C (broken installs only) | an install missing `prompt-toolkit` still fails when an interactive run starts, but with the `ModuleNotFoundError` from the accessor instead of `RuntimeError("prompt_toolkit is required for interactive mode")`; correct installs see no difference | accepted under Reading B's disposition of this row (the install violates `pyproject.toml:31`; the guard rested on the false "Optional dependency" claim) |
| Tasks 3, 8, 9: comment and docstring edits | N | none | n/a |
| Task 3: `CLAUDE.md` §4.2 admits a third reason for a function-level import (an import order a governing spec requires) | N (product); guidance change (+P) | no code path changes; future late imports may cite a spec section as their reason | owner item 2 |
| Task 13: `docs/program-theory.md:383` names `weft.core.runners.sessions` instead of `weft.core.tasks.sessions` | N (product); theory locator (class 5) | the row's concept and owner are unchanged; only the module path follows the move | owner item 4 |
| Task 5: accessors for `simplebroker_pg` | N | the import still runs at call time; still never on SQLite | n/a |
| Tasks 6, 7, 10: merge a duplicate, delete unreferenced code, drop an unreachable branch | N | none | n/a |
| Task 11: seven helpers move from `weft/commands/tasks.py` to the new `weft/commands/_observation.py`; `weft/commands/events.py` imports `weft.commands.system` instead of `weft.commands.tasks`; `follow_task_events` is looked up on the events module at call time | N | +1 module (`weft.commands._observation`) on `weft.cli.app` and `weft.client`, none elsewhere (P22); the moved code was already loaded, so no import work is added | n/a |
| Task 11: the clock seam for the three deadline helpers and `_task_snapshot_from_live_pong` moves with them. Tests that patch `tasks.time` or `events.task_ops.time` to drive these helpers must patch `_observation.time` | C (test seam only) | five patch lines in two test files; assertions unchanged (P22) | the second ruling; no product behavior changes |
| Task 12: `validate_parameterization_adapter_ref` moves to `weft/core/taskspec/run_input.py` and is re-exported from `parameterization.py` | N | same function object at every public path (`weft.core.taskspec`, `weft.core.taskspec.parameterization`); closures unchanged (P22) | n/a |
| Task 13 (O3): `weft.core.runners.host` joins the closure of every importer of `weft._runner_plugins` | N | +2 modules, ~0 ms after `import weft.cli.app` once host no longer registers backends at import (P23); `weft.core.agents.backends.llm` stays unloaded in all six entry closures | n/a |
| Task 13 (O3): agent backends register when host runs an agent, not when host is imported | C | the spawned worker that runs an agent pays the same ~42 ms / 188-module registration, just before `execute_agent_target` or `start_agent_runtime_session` instead of at module import; `test_host_import_registers_builtin_agent_backends` changes meaning (P23). Registration now runs inside the workers' run boundaries: in `_worker_entry` after `start = time.monotonic()`, inside the `execute`-phase exception handler and `_worker_runtime_context` (task env and CWD); in `_agent_session_worker_entry` after `booted`, inside `_worker_runtime_context` and the `runtime_startup`-phase startup-error handler. A registration failure becomes a task failure with those diagnostics, and one-shot durations include registration time. Existing coverage is listed under owner item 3 | owner item 3 (O3, ruled 2026-11-03) |
| Task 13 (O3): two modules move from `weft/core/tasks/` to `weft/core/runners/` | C | import paths and the logger name `weft.core.tasks.sessions` become `weft.core.runners.sessions` (11 `caplog` sites in tests); no queue payload or persisted value names these modules (grep, P23) | owner item 3 (O3, ruled 2026-11-03) |

No latency row comes from Tasks 11 or 12. Measured once, on scratch copies with
the Reading B hoists applied (P22): `import weft.cli.app` loads 686 modules
against 685 without Tasks 11–12, `weft.client` 420 against 419, and
`weft.core.manager`, `weft.manager_process`, `weft.core.taskspec`, and
`weft.core.tasks.consumer` load the same count. Task 13 under O3 adds two
modules and no measurable time (P23). O1, which the owner rejected, would have
added a D row of about 1.6–2.0 ms for every host resolution (P23).

**Hardening (Tasks 11–13).** The risky triggers still fire under class 5+P, so
the hardening checklist applies. What this plan adds for it:

- *Hidden couplings, named.* (1) Tests patch module globals (`time`,
  `follow_task_events`) on the module that owns a helper today; moving a helper
  moves its seam. Task 11 keeps the `follow_task_events` seam by calling it as a
  module attribute and retargets the clock seam. (2) Host imports two modules
  that live inside `weft/core/tasks/`, so importing host runs
  `weft/core/tasks/__init__.py`, which loads `BaseTask`, which imports the
  registry. The old static eager-cycle test cannot see this edge (P22). (3)
  Importing host today registers the agent backends as a side effect
  (`weft/core/runners/host.py:101`): today, importing host loads the
  backends; importing `weft.core.runner_validation` does not. The [AR-7]
  violation (validation imports must not load backend adapters) would arise
  from naively hoisting host into the registry, which validation imports.
- *Observable success.* `weft run` of a host command task and of an agent task
  still completes with the same result, and `weft task stop` on a host task
  still stops it (the real-path tests in each task). The six entry imports
  still succeed. `weft.core.agents.backends.llm` is absent after
  `import weft.cli.app`, `weft.client`, `weft.core.manager`, and
  `weft.core.runner_validation`.
- *Rollback.* Each of Tasks 11–13 is its own revertable hunk set; see Rollback.
- *Review before implementation begins.* The cross-family round on Tasks 11–13
  closed on 2026-11-02 (Review Log). The cross-family round on the owner's
  2026-11-03 rulings as applied is pending; no task starts before it closes.

## 5. Findings Re-derived

Each finding in the session deep dive (a non-citable scratch file) was checked
against the current tree. Every count below comes from a command in the
Execution log.

**Held and tasked (8):**

| # | Finding | Evidence | Task |
|---|---------|----------|------|
| H1 | Late imports with neither a real cycle nor an optional-dependency reason, or with a reason that is false. Of the six comments that claim a cycle or a dependency closure, the four in `weft/commands/run.py` do not mark a real cycle. | P2 (23 function-level imports), P3/P9 (only `weft/commands/tasks.py:2478` and `weft/core/taskspec/parameterization.py:131` are real cycles), P6/P10 (measured savings) | 1, 2, 4 |
| H2 | Late imports whose real reason goes unstated: `_runner_plugins.py:45` (real runtime cycle), `builtins/agent_images.py:38` (optional `weft-docker` extension), and `bootstrap.py:107`, `:110` ([CLI-5] env-file ordering; owner item 2) | P2, P11, `weft/bootstrap.py` module docstring | 3 (`_runner_plugins.py:45` moved to 13 by the second ruling) |
| H3 | `simplebroker_pg` is imported inline at three sites, bypassing `weft/core/deferred.py` | P2: `manager.py:2919`, `:2926`, `runtime_identity.py:230` | 5 |
| H4 | `poll_limits` is duplicated verbatim in `CommandSession` (`sessions.py:189-201`) and `AgentSession` (`sessions.py:826-838`) | P12 (`diff` prints nothing) | 6 |
| H5 | Eleven private module-level helpers have zero references | P13 | 7 |
| H6 | The `weft/_constants.py` module docstring imports a nonexistent `WEFT_DEBUG` and sends readers to `load_config()` for the env-var list. The catalog is actually `WEFT_CONFIG_FIELDS`. | P14 | 8 |
| H7 | The bare `[CLI-1]` at `weft/commands/types.py:5` does not resolve, because the spec defines only `[CLI-1.x]` subsections | P15 | 9 |
| H8 | `_read_outbox_task_result` tolerates non-tuple rows (`weft/commands/result.py:148-152`), but `peek_generator(with_timestamps=True)` is typed `CloseableIterator[tuple[str, int]]` | P16 | 10 |

**Held but excluded from this plan (16).** Each fix would need a new
abstraction, a contract or config change, a dependency change, a mass edit, or
an owner style ruling, so each goes to the owner or to its own plan. No task
acts on them, so their audit counts are not carried here; whoever takes one up
re-derives its inventory.

| # | Finding | Why excluded |
|---|---------|--------------|
| E1 | `Any` density (ANN401) in `weft/` | typing program across 100+ files |
| E2 | §4.5 docstrings: few public functions with parameters have `Args:` | owner style ruling: change the code or the style doc |
| E3 | §4.9 logging: few logger calls pass `extra={` | same |
| E4 | Wide signatures; `weft/ext.py:572` takes 19 arguments | the public plugin contract would change |
| E5 | TaskSpec freeze machinery (`__setattr__`, `_freeze`) in `weft/core/taskspec/model.py` | needs a new mixin or a model change |
| E6 | Public zero-reference definitions and a logging facade with no callers outside `weft/helpers/` | external embedders may use public names; the owner decides |
| E7 | `weft/_constants.py` mixes functions into a large constants module | module split; [ALT-WT-013] says size alone is not a finding |
| E8 | `WEFT_MANAGER_REUSE_ENABLED` still declared (`_constants.py:1827`, `:2787`) | config-key removal; its own plan |
| E9 | `cmd_result` returns a mode-flag union and has an `all` parameter (`weft/commands/result.py:296-305`) | public API change |
| E10 | Literal plus `cast` keeps two vocabularies (`task_lifecycle.py`, `terminal_handoff.py`) | moving the Literal touches the rule that constants live in `_constants.py` |
| E11 | `InteractiveTaskMixin` abstract-property boilerplate | design judgment; no mechanical fix |
| E12 | 8 exception classes not rooted at `WeftError` | changing a base class changes `isinstance` behavior |
| E13 | `# noqa` directives name rules (BLE001, TRY004, RUF022, PLR0402, SIM102) that `pyproject.toml` does not select | `pyproject.toml` config change |
| E14 | mypy runs without `strict` | config change |
| E15 | `typing-extensions` is declared (`pyproject.toml:39`) but nothing imports it | dependency change; ask first |
| E16 | Import style: absolute imports outnumber relative ones; private-name imports | mass edit |

**Dropped (2):**

| # | Deep-dive claim | Why dropped |
|---|-----------------|-------------|
| X1 | Broad excepts without a pragma or marker | P17: 54 of 173 broad handlers have no comment on the same line. Every one of them re-raises in the handler body or collects the error and re-raises it later (`weft/core/manager.py:4760`, `weft/core/tasks/multiqueue_watcher.py:1103`). None of them swallows the error, which is the case the §4.7 pragma rule targets. |
| X2 | The suppression-governance note | the deep dive itself calls it a process-cost observation, not a defect, so there is nothing to fix |

Also left out on purpose: `prompt_toolkit` and `weft_docker` are not routed
into `weft/core/deferred.py`. Doing so would make core name a CLI dependency and
an extension package. `prompt_toolkit` is a hard dependency used only by the
CLI, so Task 4 routes it through a new `weft/cli/deferred.py` accessor under
the named §4.2 exception (owner item 1); `weft_docker` is an optional
extension, so Task 3 gives it a true comment.

## 6. Tasks

**Gate for every task:** the Ruling line is filled in (done: Reading B,
2026-11-02). Owner items 1–4 are closed (owner, 2026-11-03), so no task waits
on an owner answer. The tasks land and get reviewed as one change. Run them in
order. Record the structural check once per change group, before and after:
the before output is the failing proof (engineering principle 4.1, Rule 5
substitute: a grep or AST gate that shows the site), the after output is the
correction. Run each task's targeted tests after its edit; the full suite runs
once, in Verification, not per group. Line numbers are as of `b7eed9f9`.
Earlier tasks shift them (Task 2 edits `manager.py` before Task 5 and
`manager_runtime.py` before Task 7), so locate each site by its symbol name.

The AST gate used below is the function-level import lister. It is recorded
inline at P2. To re-create it, use any script that walks `ast` inside function
bodies and prints `path:line`, the import, and the preceding comment line.

1. **The four `weft/commands/run.py` late imports (H1).**
   - Delete the four function-level imports (and their comments at
     `weft/commands/run.py:384`, `:438`, `:510`, and `:523`). Add
     `from weft.commands.events import iter_task_realtime_events` and
     `from weft.commands.tasks import (_require_observation_config,
     _task_control_result, _terminal_observation_snapshot)` to the module
     header, in the file's existing absolute style. Let ruff's isort place them.
   - Do not: rename or move the helpers, or add a `weft/commands/deferred.py`.
   - Check: `grep -n "dependency closure\|result/events cycle" weft/commands/run.py`
     must print nothing, and the AST gate must list no rows for
     `weft/commands/run.py`.
   - Tests: `tests/architecture/test_import_boundaries.py`,
     `tests/commands/test_run_public.py`, `tests/commands/test_run.py`,
     `tests/commands/test_active_interactive_lifecycle.py`,
     `tests/commands/test_control_observation_connections.py`.
   - Stop if: the eager-cycle test fails, or a test turns out to patch one
     of the three names on `weft.commands.tasks` while it drives the
     `weft/commands/run.py` path. After hoisting, run's own binding would ignore
     that patch. At baseline this does not fire (P19): the only such patches,
     `tests/core/test_client.py:270` and `:324`, drive the client path, which
     looks the name up as `tasks._task_control_result` at call time.

2. **Manager-side late imports (H1).**
   - Delete the function-level imports at `manager.py:2139`,
     `:2143`, and `:2147`. Extend the existing `from .tasks import Consumer`
     (`manager.py:217`) to import `HeartbeatTask`, `PipelineEdgeTask`, and
     `PipelineTask` as well. All three are already exported by
     `weft/core/tasks/__init__.py`. In `weft/core/manager_runtime.py`, hoist
     `from weft.manager_process import run_manager_process` (line 2289) to
     module scope. Drop the false clause "without importing the CLI module at
     import time" from the `_run_manager_process_foreground` docstring.
   - `manager.py:2151` and `:2155` (`TaskMonitor`, `LivenessMonitor`): delete
     both function-level imports and add `from .monitor.task_monitor import
     TaskMonitor` and `from .tasks.liveness_monitor import LivenessMonitor` to
     the module header. Neither module imports `weft.core.manager` (P9), so no
     cycle forms. The ruling accepts the ~25 ms / 22-module D row.
   - Check: the AST gate shows no rows for `manager.py:2139`, `:2143`, `:2147`,
     `:2151`, `:2155`, or `manager_runtime.py:2289`. Closure check (presence
     only, no timing): in a fresh interpreter,
     `./.venv/bin/python -I -c 'import sys, weft.core.manager; print(all(m in sys.modules for m in ("weft.core.monitor.task_monitor", "weft.core.tasks.liveness_monitor")))'`
     prints `True` (it prints `False` at `b7eed9f9`). Do not rerun the P10
     incremental timing after the hoist: the modules are then already loaded,
     so it measures about zero. The one post-hoist timing comparison is in
     Verification.
   - Tests: `tests/architecture/test_import_boundaries.py`,
     `tests/core/test_manager.py`, `tests/tasks/test_liveness_monitor.py`,
     `tests/commands/test_serve.py`.

3. **State the real reason at the uncommented sites (H2), and admit the
   spec-ordering reason in `CLAUDE.md` §4.2 (owner item 2).** Comment edits
   plus one guidance edit; no import moves.
   - `weft/_runner_plugins.py:45`: superseded by the second ruling. Do not
     add a comment; Task 13 removes the late import.
   - `weft/builtins/agent_images.py:38`: `# Optional weft-docker extension;
     require_runner_plugin("docker") guards it.`
   - `CLAUDE.md` lines 347–352: apply the Replacement block in
     `## Proposed Guidance and Theory Delta`. The permitted reasons for a
     function-level import become three: a real import cycle, an optional
     dependency, and an import order a governing spec requires, with the
     comment naming the spec section. The block also says that named
     exceptions granted by the owner are listed in the accessor module that
     holds them. It lands in this change, with the code it governs.
   - `weft/bootstrap.py:107` and `:110` (third reason, [CLI-5]). At
     `b7eed9f9`, lines 104–106 carry a comment about rendering
     `InvalidConfigError` without a traceback; it names no spec section, and
     line 110 has no comment (its preceding line is `try:`). Add
     `# [CLI-5]: these imports run only after WEFT_ENV_FILE is applied.` as
     the last line of the comment block directly above line 107, and
     `# [CLI-5]: the full CLI imports only after WEFT_ENV_FILE is applied.`
     inside the `try:` directly above line 110. Keep the existing comment
     lines.
   - `weft/core/agents/backends/__init__.py:17-18` (third reason, [AR-7]). At
     `b7eed9f9` the two imports already carry a reason comment at lines 15–16
     ("Runtime adapters are loaded only at the explicit registration boundary
     so schema-only imports do not initialize model clients or provider
     tooling."), and the module and function docstrings cite [AR-7], but the
     comment itself names no spec section, and the AST gate reads only the
     physical line directly before each import (at `b7eed9f9` it reports no
     comment for line 18, because the two-line comment precedes line 17
     only; P2). Keep the existing two-line comment at lines 15–16 unchanged
     and add one short, self-contained comment line directly before each
     import, so that both line 17 and line 18 have a preceding comment that
     names the spec section: `# [AR-7]: loaded only at the registration
     boundary.` above `from .llm import LLMBackend`, and the same line above
     `from .provider_cli import ProviderCLIBackend`. (Cross-family round of
     2026-11-03, F1: a prefix on the wrapped comment would leave its last
     line, and line 18, without the code.)
   - Check: the AST gate shows a non-empty comment for each edited row, and
     the comment line it prints for `bootstrap.py:107`, `:110`,
     `backends/__init__.py:17`, and `backends/__init__.py:18` names
     `[CLI-5]` or `[AR-7]`;
     `grep -n "import order that a governing spec requires" CLAUDE.md` prints
     one line; `bin/check-doc-paths` exits 0.
   - Do not: move any of these imports. Hoisting the bootstrap imports would
     break the [CLI-5] env-file ordering, and hoisting the backend adapters
     would load them on schema-only imports, against [AR-7]
     (`test_agents_import_does_not_register_or_load_builtin_backends`).
   - Tests: `tests/cli/test_env_file_bootstrap.py`,
     `tests/architecture/test_import_boundaries.py`.

4. **Route prompt-toolkit through a `weft/cli/deferred.py` accessor under a
   named §4.2 exception (H1; owner item 1, closed 2026-11-03).**
   - Create `weft/cli/deferred.py` (new file; `weft/cli/` holds only
     `__init__.py`, `app.py`, and `run.py` at `b7eed9f9`, and
     `weft/cli/__init__.py` imports nothing, so the new module adds no import
     edge). Mirror `weft/core/deferred.py`: a module docstring,
     `from __future__ import annotations`, and one accessor function per
     deferred name, each with a docstring stating why, doing the import
     inside the function under a reason comment and returning the imported
     name (the `get_setproctitle` shape). The module docstring lists the
     named exceptions this module holds, one line each, and cites
     `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]. Its one entry:
     prompt-toolkit, `get_prompt_session()`: "the interactive prompt library
     costs 26–56 ms at import and is used only by interactive runs; the owner
     granted this one exception on 2026-11-03."
   - The accessor: `def get_prompt_session() -> type[PromptSession[str]]:`,
     with `from prompt_toolkit import PromptSession` under
     `if TYPE_CHECKING:` at module scope for the annotation, and in the body
     one self-contained comment line directly before the import, `# CLAUDE.md
     §4.2 named exception (owner, 2026-11-03): interactive-only import.`
     (the AST gate reads only the physical line before the import, so the
     reason must be complete on that line; the module docstring carries the
     full text), then `from prompt_toolkit import PromptSession` and
     `return PromptSession`.
   - `weft/cli/run.py`: delete the `try`/`except ImportError` block at
     `weft/cli/run.py:219-223`, including the false "Optional dependency"
     comment. `prompt-toolkit` is a declared dependency
     (`pyproject.toml:31`), so the guard protects only a broken install (C row
     in §4). Add `import weft.cli.deferred` to the module header's local
     group, and in `drive_interactive_session` write
     `prompt = weft.cli.deferred.get_prompt_session()()`, looking the
     accessor up on the module at call time as
     `weft/core/process_title.py:50` does, so a patch on the module attribute
     takes effect. Do not write `from weft.cli.deferred import
     get_prompt_session`; that binding would ignore the patch.
   - Tests: retarget the six patch sites in
     `tests/commands/test_active_interactive_lifecycle.py` (lines 263, 297,
     410, 585, 669, and 759, each `monkeypatch.setattr("prompt_toolkit.PromptSession",
     lambda: prompt)`) to `monkeypatch.setattr(cli_deferred,
     "get_prompt_session", lambda: lambda: prompt)`, with
     `from weft.cli import deferred as cli_deferred` added to the test
     module's imports, as `tests/core/test_process_title.py:16` imports
     `deferred` and patches it at `:34` and `:68`. Assertions unchanged. The
     test module's own `from prompt_toolkit import PromptSession` (line 14)
     stays; it builds the real prompt the patch returns.
   - Check: `grep -n "Optional dependency\|prompt_toolkit" weft/cli/run.py`
     prints nothing; `grep -c '"prompt_toolkit.PromptSession"'
     tests/commands/test_active_interactive_lifecycle.py` prints `0`; the AST
     gate lists no row for `weft/cli/run.py` and exactly one row for
     `weft/cli/deferred.py`, with a non-empty comment; and in a fresh
     interpreter
     `./.venv/bin/python -I -c 'import sys, weft.cli.app; print("prompt_toolkit" in sys.modules)'`
     prints `False` (it prints `False` at `b7eed9f9`).
   - Tests: `tests/commands/test_active_interactive_lifecycle.py`,
     `tests/architecture/test_import_boundaries.py`.
   - Stop if: the accessor typing needs anything beyond a `TYPE_CHECKING`
     import and the `type[PromptSession[str]]` annotation.

5. **Route `simplebroker_pg` through `weft/core/deferred.py` (H3).**
   - Add three accessors to `weft/core/deferred.py`:
     `get_pg_connection_stats()`, `get_pg_connection_usage()`, and
     `get_pg_bind_connection_owner()`. Each one does
     `from simplebroker_pg import <name>` inside the function, under the
     comment `# Optional PostgreSQL backend: SQLite installs never load it.`
     Each one returns the function. Type the return values with
     `Callable[...]`, using imports under `TYPE_CHECKING` from `simplebroker_pg`
     (it ships `py.typed`). That keeps `warn_return_any` clean. Do not return
     `ModuleType` here: attribute calls on it type as `Any` and fail mypy at the
     call sites.
   - Replace `manager.py:2919`, `manager.py:2926`, and
     `runtime_identity.py:230` with calls to the accessors. Keep each call
     where the import is today: inside `Manager._read_postgres_connection_stats`,
     `Manager._read_postgres_connection_usage`, and the
     `identity.backend_name == "postgres"` branch of `install_runtime_identity`.
   - Leave the type-only `from simplebroker_pg import ConnectionUsage` under
     `if TYPE_CHECKING:` at `weft/core/manager.py:240` in place. It never runs.
   - Check (lists runtime `simplebroker_pg` imports outside
     `weft/core/deferred.py`, skipping `if TYPE_CHECKING:` bodies):

     ```bash
     ./.venv/bin/python -I -c 'import ast, pathlib
     for p in sorted(pathlib.Path("weft").rglob("*.py")):
         if p.as_posix() == "weft/core/deferred.py":
             continue
         tree = ast.parse(p.read_text())
         typed = {id(n) for i in ast.walk(tree) if isinstance(i, ast.If) and ast.unparse(i.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING") for b in i.body for n in ast.walk(b)}
         for n in ast.walk(tree):
             mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names] if isinstance(n, ast.Import) else []
             if id(n) not in typed and any(m.split(".")[0] == "simplebroker_pg" for m in mods):
                 print(f"{p}:{n.lineno}")'
     ```

     Before (at `b7eed9f9`): `weft/core/manager.py:2919`,
     `weft/core/manager.py:2926`, `weft/core/runtime_identity.py:230`, and no
     row for `manager.py:240`. After: prints nothing.
   - SQLite probe (fresh interpreter, no permanent test). From the repository
     root, with `. ./.envrc` loaded:

     ```bash
     WEFT_BACKEND=sqlite ./.venv/bin/python -I -c '
     import sys, tempfile
     import weft.cli.app, weft.client, weft.manager_process, weft.core.manager, weft.core.deferred
     from weft.core.runtime_identity import RuntimeIdentity, canonical_runtime_context, install_runtime_identity
     t = tempfile.mkdtemp() + "/tasks.db"
     install_runtime_identity(RuntimeIdentity(tid="1791919490820769000", context=canonical_runtime_context(t), backend_name="sqlite", broker_target=t))
     assert "simplebroker_pg" not in sys.modules, "simplebroker_pg loaded"
     print("simplebroker_pg loaded:", "simplebroker_pg" in sys.modules)'
     ```

     Expected before and after: `simplebroker_pg loaded: False`, exit 0. An
     eager import added to `weft/core/deferred.py` fails it.
   - Tests: `tests/core/test_runtime_identity.py`,
     `tests/core/test_manager_family_maintenance.py`, and the PG lane
     `tests/system/test_measured_admission_pg.py` when a PostgreSQL target is
     configured. Otherwise record that it was skipped.
   - Stop if: the accessor typing needs anything beyond `TYPE_CHECKING` imports
     and `Callable` annotations.

6. **Merge the duplicate `poll_limits` (H4).**
   - Add one module-private function to `weft/core/tasks/sessions.py`:
     `_poll_monitor_limits(session: CommandSession | AgentSession) ->
     tuple[bool, str | None]`. Give it the exact current body, using `session.`
     where the body has `self.`, and keep the single
     `# noqa: BLE001 approved [TS-3.1] [RUFF-SUP-344] exception` line. Both
     `CommandSession.poll_limits` and `AgentSession.poll_limits` become
     `return _poll_monitor_limits(self)`. Leave `InProcessCommandSession.poll_limits`
     (`sessions.py:959`) unchanged, because its body is different.
   - Then update the three hand-maintained counts first. `--write` validates
     before it writes, so it fails until they match (P20):
     registry line 184, the `RUFF-SUP-344` cardinality cell, from
     `` `2` directives; raw: `BLE001=2` `` to `` `1` directive; raw: `BLE001=1` ``;
     registry line 213, the global raw-`noqa` inventory, from `BLE001=132` to
     `BLE001=131`; and `tests/specs/test_ruff_policy.py:146`
     `EXPECTED_DIRECTIVE_COUNT` from `325` to `324`. Then run
     `./.venv/bin/python bin/ruff_suppression_index.py --write`, which
     regenerates the index row (line 385) to name `_poll_monitor_limits`, and
     then `--check`.
   - Check: `grep -c "self._monitor.check_limits()\|session._monitor.check_limits()" weft/core/tasks/sessions.py`
     prints `1`.
   - Tests: `tests/tasks/test_task_interactive.py`, `tests/tasks/test_runner.py`,
     `tests/specs/test_ruff_policy.py`, `tests/specs/test_ruff_suppression_index.py`.

7. **Delete the eleven unreferenced private helpers (H5).**
   - `weft/commands/system.py:361` `_is_internal_service_record`;
     `weft/commands/tasks.py:226` `_task_is_persistent_payload`, `:230`
     `_queue_names_for_tid`, `:237` `_peek_final_outbox_snapshot`, `:255`
     `_coerce_terminal_envelope`, `:266` `_peek_terminal_ctrl_out_snapshot`,
     `:1066` `_terminal_snapshot_from_status_snapshot`, `:1614`
     `_ctrl_in_for_tid`, `:1618` `_ctrl_out_for_tid`;
     `weft/core/manager_runtime.py:381` `_select_uncertain_active_manager_from_snapshot`;
     `weft/core/spec_store.py:105` `_spec_bundle_path` (keep
     `_spec_bundle_path_from_root` at line 113).
   - Remove only the imports that `ruff check --select F401` then flags.
   - Check: rerun P13 for these eleven names after the deletion, adding
     `| grep -v "^docs/plans/"` because this plan and its siblings name them.
     Each must print `refs=0` and no definition.
   - Stop if: any name gains a reference between baseline and implementation.
     In that case re-derive P13 and drop that name from the task.

8. **Fix the `weft/_constants.py` module docstring (H6).** Replace the
   `WEFT_DEBUG` usage example with a real one, such as
   `load_config()["LOGGING_ENABLED"]`. Point "Environment Variables" at
   `WEFT_CONFIG_FIELDS` (line 2680). Check: `grep -n "import WEFT_DEBUG\|if WEFT_DEBUG" weft/_constants.py`
   prints nothing. Do not grep all of `weft/` for `WEFT_DEBUG`: it is a real
   environment variable (the `DEBUG` field), and the `debug_print` docstring at
   `weft/helpers/__init__.py:668` names it correctly.

9. **Make the bare `[CLI-1]` at `weft/commands/types.py:5` resolve (H7).**
   Replace it with `[CLI-1.1], [CLI-1.2], [CLI-1.3], [CLI-1.4]`. The module
   also defines `SpecRecord`, `SpecValidationResult`, and `SpecMutationResult`,
   which belong to Spec Management [CLI-1.4]. Check: each code on that line
   matches a heading in `docs/specifications/10-CLI_Interface.md`
   (`grep -n "^##.*\[CLI-1\.[1-4]\]" docs/specifications/10-CLI_Interface.md`
   shows the four headings at lines 92, 390, 618, and 691).

10. **Drop the unreachable non-tuple branch (H8).** In
    `weft/commands/result.py:148-152`, iterate `for payload, timestamp in rows:`
    and keep `observed_timestamps.append(int(timestamp))`. Check: `grep -n
    "isinstance(entry, tuple)" weft/commands/result.py` prints nothing; mypy
    stays clean. Tests: `tests/commands/test_result.py`.

**Tasks 11–13: break the three real import cycles (second owner ruling,
2026-11-02).** They run after Tasks 1–10, in order. Task 11 first
strengthens the existing eager-cycle test into the acyclic-graph test; it fails
at baseline with three cycles (P22), Task 11 clears the first, Task 12 the
second, and Task 13 the third. Until Task 13 lands, mark the strengthened test
`xfail(strict=True)` with the reason "cycle 3 is broken in Task 13", and
remove the marker in Task 13. A strict xfail turns into a failure as soon as
the graph is acyclic, so the marker cannot be forgotten. An xfail result alone
is not proof of the intermediate state: any other failure, including a new
cycle, would also be reported as expected. While the marker stands, read the
SCC list itself (each task's Check says how).
Stop gate for all three: if any listed behavioral test fails for a reason other
than a moved patch seam that the task names, the move changed behavior. Revert
that task's hunks with Edit and re-plan.

11. **Cycle 1: `weft/commands/tasks.py` and `weft/commands/events.py`.**
    Today `events.py:18` imports `weft.commands.tasks as task_ops` and uses six
    of its names: `task_ops.system_cmd.collect_known_tid_record`
    (`events.py:128`), `task_ops._task_snapshot_from_live_pong` (`:133`),
    `task_ops._public_snapshot` (`:140`), `task_ops._deadline_from_timeout`
    (`:191`, `:339`), `task_ops._deadline_expired` (`:219`), and
    `task_ops._remaining_timeout` (`:226`). `tasks.py:2478` late-imports
    `follow_task_events` in `cmd_task_status` for `--watch`.

    Shape chosen: move the helpers down (one-way layering inside
    `weft/commands/`). The alternative, moving the `--watch` composition out of
    `cmd_task_status`, would change the signature of a [PY-2] command that the
    client and CLI call, so it is a contract change, not a move. The helper move
    is 7 functions, about 185 lines, moved verbatim (P22).

    - Create `weft/commands/_observation.py` (new file). Module docstring:
      "Task observation helpers shared by task commands and event streams",
      citing `docs/specifications/05-Message_Flow_and_State.md` [MF-5] and
      `docs/specifications/14-Python_API_Surfaces.md` [PY-2]. Imports: `time`,
      `typing.Any`, `TERMINAL_TASK_STATUSES` from `weft._constants`,
      `TaskEvidenceSnapshot` from `weft.core.task_evidence`, `tid_short_form`
      from `weft.helpers`, `TaskSnapshot` from `._task_snapshot_reducer`, and
      `TaskSnapshot as PublicTaskSnapshot` from `.types`.
    - Move these definitions from `weft/commands/tasks.py` into it, bodies
      unchanged except for the names they qualify: `_deadline_from_timeout`
      (`tasks.py:334`), `_remaining_timeout` (`:340`), `_deadline_expired`
      (`:346`), `_state_from_taskspec` (`:219`), `_public_snapshot` (`:282`;
      its return type and constructor become `PublicTaskSnapshot`),
      `_task_snapshot_from_live_pong` (`:1140`), and `_runner_name_from_taskspec`
      (`:1253`). In the moved bodies, `system_cmd.TaskSnapshot` becomes
      `TaskSnapshot`, `system_cmd.TERMINAL_TASK_STATUSES` becomes
      `TERMINAL_TASK_STATUSES`, and `task_evidence.TaskEvidenceSnapshot` becomes
      `TaskEvidenceSnapshot`. These are the same objects
      (`weft/commands/system.py:83-87` re-exports the reducer's `TaskSnapshot`;
      `system.py:40` imports the constant). Keep the private names. Do not merge
      `_public_snapshot` with `weft/commands/system.py::_public_task_snapshot`
      or `_runner_name_from_taskspec` with the reducer's
      `runner_name_for_snapshot`: no drive-by.
    - `weft/commands/tasks.py`: import from `._observation`, at module scope,
      the six names it still calls (all but `_state_from_taskspec`, whose only
      caller moves with it). The callers that stay in `tasks.py` are at
      `tasks.py:959`, `:1283`, `:1360`, `:1382`, `:1421`, `:1423`, `:1714`, and
      `:2465`. Add
      `import weft.commands.events as events_cmd` next to
      `import weft.commands.system as system_cmd` (`tasks.py:30`). Delete the
      late import and its comment at `tasks.py:2477-2478`, and call
      `events_cmd.follow_task_events(ctx, full_tid)`. Keep the attribute lookup:
      `tests/commands/test_task_commands.py:163` patches
      `event_cmd.follow_task_events`, and a module-scope
      `from .events import follow_task_events` binding would ignore that patch
      (P22 saw it fail).
    - `weft/commands/events.py`: replace `import weft.commands.tasks as
      task_ops` with `import weft.commands.system as system_cmd`; import
      `_deadline_expired`, `_deadline_from_timeout`, `_public_snapshot`,
      `_remaining_timeout`, and `_task_snapshot_from_live_pong` from
      `._observation`; drop the `task_ops.` prefixes.
    - Test seams (assertions unchanged): the deadline helpers read the clock of
      the module that defines them. In `tests/commands/test_task_commands.py`
      add `from weft.commands import _observation as observation_cmd` and
      change the patch at lines 638 and 658 from `task_cmd` to
      `observation_cmd`. In `tests/commands/test_event_observation_connections.py`
      add the same import and change `events_cmd.task_ops` to `observation_cmd`
      at lines 169, 250, and 447. Leave the other `task_cmd, "time"` patches
      (`test_task_commands.py:920`, `:1574`) alone; they drive
      `tasks.py` code that still reads `tasks.time` and stayed green in P22.
    - Strengthen the existing `test_weft_has_no_eager_import_cycles`
      (`tests/architecture/test_import_boundaries.py:629`) in place instead of
      adding a second test that repeats its weaker check: rename it and
      replace its body with the following, which reuses `_iter_import_edges`,
      `_strongly_connected_components`, `_module_name`, and
      `_is_module_or_child`:

      ```python
      def test_weft_import_graph_is_acyclic_including_function_level_imports() -> None:
          """No import cycle may hide behind a function-level import or a package initializer."""

          known = {
              _module_name(path)
              for path in PACKAGE_ROOT.rglob("*.py")
              if "__pycache__" not in path.parts
          }
          edges: set[tuple[str, str]] = set()
          for edge in _iter_import_edges(PACKAGE_ROOT):
              if edge.type_checking or not edge.target_module.startswith("weft."):
                  continue
              edges.add((edge.source_module, edge.target_module))
              # Importing a.b.c first runs a.b's __init__; count that edge unless
              # the importer already lives inside a.b.
              parts = edge.target_module.split(".")
              for index in range(2, len(parts)):
                  package = ".".join(parts[:index])
                  if package in known and not _is_module_or_child(
                      edge.source_module, package
                  ):
                      edges.add((edge.source_module, package))
          assert _strongly_connected_components(sorted(edges)) == []
      ```

      Every function-level import counts as an edge, so "no lazy import hides a
      cycle" is mechanically checked. It differs from the old body in two
      ways: function-scope edges count, and so do package-initializer edges,
      which is how the cycle-3 chain runs
      (`host -> weft.core.tasks -> base -> _runner_plugins`). Its edge set is a
      superset of the old one, so the old name and body go; no second cycle
      test is kept. Mutations that trip the strengthened test, each run on the
      fully restructured copy (P23): restoring any one of the three cycles,
      even as a function-level import (`import weft.commands.tasks` inside a
      function in `events.py`; `from .parameterization import ...` inside a
      function in `model.py`; `from weft.core.tasks import base` inside a
      function in `host.py`), gives one SCC of 2, 3, or 14 modules.
    - Check: the AST gate lists no row for `weft/commands/tasks.py`; `grep -n
      "task_ops" weft/commands/events.py` prints nothing; the strengthened
      test, run once with `--runxfail` so its assertion output prints, reports
      exactly the two remaining SCCs (taskspec and runner-plugins). Read that
      list; the xfail result under the marker is not the check.
    - Tests: `tests/architecture/test_import_boundaries.py`,
      `tests/commands/test_task_commands.py`,
      `tests/commands/test_event_observation_connections.py`,
      `tests/commands/test_task_snapshot_reducer.py` (it pins the reducer pure),
      `tests/core/test_ops_shared.py`, `tests/commands/test_status.py`,
      `tests/core/test_client.py`. Do not mock the broker for these; the
      harness-backed tests in these files are the real proof.
    - Stop if: a test patches one of the seven moved names on
      `weft.commands.tasks` to change what `events.py` does. At `b7eed9f9` none
      does (grep in P22); if one appears, retarget it to `_observation` only if
      its assertions stay unchanged.

12. **Cycle 2: `weft/core/taskspec/` model, parameterization, transport.**
    Today `model.py:61` imports `validate_parameterization_adapter_ref` from
    `parameterization.py:35`, `transport.py:15` imports `TaskSpec` from
    `model`, and `parameterization.py:131` late-imports
    `validate_taskspec_payload` from `transport` inside
    `materialize_taskspec_template`.

    Shape chosen: split parameterization's two altitudes. The pure reference
    validator moves to `weft/core/taskspec/run_input.py`, which already holds
    its twin `validate_run_input_adapter_ref` (`run_input.py:34`) and the shared
    `parse_declared_option_args` that parameterization uses, and imports
    nothing from `model`. No new module.

    - Move `validate_parameterization_adapter_ref` verbatim from
      `parameterization.py:35` to `run_input.py`, next to
      `validate_run_input_adapter_ref`, and add it to `run_input.__all__`.
    - `model.py`: delete line 61 and add `validate_parameterization_adapter_ref`
      to the existing `from .run_input import (...)` block (`model.py:62`).
    - `parameterization.py`: import `validate_parameterization_adapter_ref` from
      `.run_input` (it stays in `parameterization.__all__`, so
      `weft.core.taskspec.parameterization.validate_parameterization_adapter_ref`
      and the `weft/core/taskspec/__init__.py:37` re-export keep resolving to
      the same function); drop `split_import_ref` from the
      `weft.core.imports` import, which ruff then flags as unused; add
      `from .transport import validate_taskspec_payload` at module scope; delete
      the late import and its comment at lines 130-131.
    - Public names: `weft/ext.py` names nothing from parameterization (grep in
      P22). `weft/core/taskspec/__init__.py` needs no edit.
    - Check: the AST gate lists no row for `weft/core/taskspec/`; the
      strengthened test, run with `--runxfail`, reports only the
      runner-plugins SCC.
    - Tests: `tests/architecture/test_import_boundaries.py`, `tests/taskspec/`,
      `tests/core/test_spec_parameterization.py` (its `import_callable_ref`
      patches at lines 215 and 234 stay on `parameterization`, which still
      owns that import), `tests/commands/test_specs.py`,
      `tests/core/test_builtin_dockerized_agent.py`,
      `tests/core/test_builtin_platform_support.py`.

13. **Cycle 3: the runner-plugin registry (shape O3; owner item 3, closed 2026-11-03).**
    Today `weft/_runner_plugins.py:45` late-imports host's `get_runner_plugin`
    when the name is `host`. `weft/core/runners/host.py:59` and `:66` import
    `weft.core.tasks.agent_session_protocol` and `weft.core.tasks.sessions`, so
    importing host runs `weft/core/tasks/__init__.py`, which loads `base.py`,
    which imports `require_runner_plugin` from the registry (`base.py:82`).
    Those two modules import nothing from the `weft.core.tasks` package except
    each other (P23). Host also calls `register_builtin_agent_runtimes()` at
    module scope (`host.py:101`), which loads the `llm` and `provider_cli`
    adapters: +42 ms and +188 modules on top of `weft.cli.app`. Host itself
    adds 2 modules and ~0 ms (P23). No entry closure loads host today.

    Options measured (P23):

    | Shape | Breaks the cycle? | Registry free of implementations? | Differences |
    |-------|-------------------|-----------------------------------|-------------|
    | Hoist the registry import as is | no: `host -> weft.core.tasks -> base -> _runner_plugins` | no | also loads `llm` from `weft.core.runner_validation`, against [AR-7]; +42 ms on every CLI start |
    | (a)/O1: `host` entry point under `[project.entry-points."weft.runners"]` in `pyproject.toml`, resolved by `_load_entry_point_plugin` | in the static graph only: `entry_point.load()` still imports host at call time, so the runtime loop remains behind `importlib` | yes | changes packaging metadata; needs a reinstall (`uv sync`) before `host` resolves, so an uninstalled source tree or a stale editable install loses the default runner; ~1.6–2.0 ms metadata scan on every host resolution, against ~0 today. Rejected (owner item 3, 2026-11-03: "O3 sounds correct") |
    | (b): an explicit registration call that the registry consults | no single bootstrap point precedes every resolution: no `weft.core.tasks` module can import host without a cycle, the consumer child loads host only through the registry, `weft task stop` resolves host inside the CLI process, and embedders build `Consumer` directly | yes | an import-order dependency the code cannot enforce ("Unknown runner plugin: host" when it is missed); also `weft/core/runners/__init__.py` cannot host it (`weft/core/tasks/runner.py:23` imports `weft.core.runners.outcome`, which would close a new cycle) |
    | (c) as briefed: move `require_runner_plugin`/`get_runner_plugin` below host | no: host transitively imports `base.py`, a caller of the resolver | n/a | not feasible |
    | **O3 (chosen; owner item 3, 2026-11-03: "O3 sounds correct")**: move the two session modules out of the `weft.core.tasks` package, move host's backend registration to the two agent-run points, import host at module scope in the registry | yes, with no dynamic import left | no: the registry names its one built-in default statically | C rows in §4: two module paths, one logger name, one pinned test flips, registration inside the worker timing and error handlers |
    | O4 (hybrid): O1's `host` entry point plus O3's two session-module moves; host keeps its import-time backend registration | yes: with the session modules out of `weft.core.tasks`, host no longer reaches `base.py`, so even the dynamic registry-to-host edge closes no loop; validation imports still avoid host, so [AR-7] holds | yes | O1's packaging-metadata change, ~1.6–2.0 ms metadata scan per host resolution, and reinstall before `host` resolves; plus O3's module-path and logger-name C rows; no registration-timing C row. Rejected (owner item 3, 2026-11-03) |

    Why O3: it removes the dependency loop with no packaging change and no
    added latency. O4 also removes it, at the cost of an entry point, a
    reinstall before resolution works, and a metadata scan per resolution.
    O1 alone only hides the loop from the graph. The cost of O3 is the C rows
    in §4. It does not meet the brief's "implementation-free registry"
    target: the registry imports `weft.core.runners.host`, the built-in
    default, by name. The owner ruled O3 on 2026-11-03 ("O3 sounds correct",
    owner item 3), so Task 13 runs O3 with no stop for a shape choice, and O1
    and O4 stay in the table above as rejected. Owner item 4 (closed the same
    day) settles the theory locator, so Task 13 no longer waits on it either.

    Edits under O3:

    - Move `weft/core/tasks/sessions.py` to `weft/core/runners/sessions.py` and
      `weft/core/tasks/agent_session_protocol.py` to
      `weft/core/runners/agent_session_protocol.py` (`git mv`; contents
      unchanged except the import path below). Neither destination exists at
      `b7eed9f9`: `weft/core/runners/` holds `__init__.py`, `host.py`,
      `outcome.py`, and `subprocess_runner.py`. No re-export module is left at
      either old path ([ALT-WT-012]; owner item 4). Do Task 6 first, at the
      old path.
    - Rewrite the import paths: `sessions.py:34`
      (`weft.core.tasks.agent_session_protocol` becomes
      `weft.core.runners.agent_session_protocol`), `host.py:59` and `:66`,
      `weft/core/tasks/debugger.py:13` (`from .sessions import` becomes
      `from weft.core.runners.sessions import`), and the tests
      `tests/tasks/test_task_execution.py:61` and
      `tests/tasks/test_runner.py:38` (`from weft.core.tasks import sessions
      as sessions_module` becomes `from weft.core.runners import sessions as
      sessions_module`), `tests/tasks/test_task_execution.py:68`,
      `tests/tasks/test_runner.py:39` and `:48`,
      `tests/commands/test_active_interactive_lifecycle.py:40`, plus the 11
      `caplog.set_level(..., logger="weft.core.tasks.sessions")` sites in the
      first two files.
    - The complete list is the AST-resolved inventory below, not a
      dotted-path grep: a grep for `core.tasks.sessions` misses the two
      `from weft.core.tasks import sessions` forms. The command resolves
      relative imports and `from package import submodule`, and also lists
      string literals naming either module (logger names, patch targets).
      Run from the repository root:

      ```bash
      ./.venv/bin/python -I - weft tests bin extensions integrations <<'PY'
      import ast, sys
      from pathlib import Path
      T = ("weft.core.tasks.sessions", "weft.core.tasks.agent_session_protocol")
      hit = lambda n: any(n == t or n.startswith(t + ".") for t in T)
      for sub in sys.argv[1:]:
          for p in sorted(Path(sub).rglob("*.py")):
              pkg = p.with_suffix("").parts[:-1]
              for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                  if isinstance(n, ast.Import) and any(hit(a.name) for a in n.names):
                      print(f"{p}:{n.lineno}: import")
                  elif isinstance(n, ast.ImportFrom):
                      base = ".".join((pkg[: len(pkg) - n.level + 1] if n.level else ()) + ((n.module,) if n.module else ()))
                      if hit(base) or any(hit(f"{base}.{a.name}") for a in n.names):
                          print(f"{p}:{n.lineno}: from {base} import {', '.join(a.name for a in n.names)}")
                  elif isinstance(n, ast.Constant) and isinstance(n.value, str) and any(t in n.value for t in T):
                      print(f"{p}:{n.lineno}: string {n.value!r}")
      PY
      ```

      Output at `b7eed9f9` (P24: the same 21 lines on the scratch copy and on
      the live tree; `bin/`, `extensions/`, and `integrations/` have no hit),
      import names abbreviated:

      ```text
      weft/core/runners/host.py:59: from weft.core.tasks.agent_session_protocol import make_booted_response, ... (5 names)
      weft/core/runners/host.py:66: from weft.core.tasks.sessions import AgentSession, CommandSession
      weft/core/tasks/debugger.py:13: from weft.core.tasks.sessions import CommandSession, InProcessCommandSession
      weft/core/tasks/sessions.py:34: from weft.core.tasks.agent_session_protocol import is_booted_response, ... (8 names)
      tests/commands/test_active_interactive_lifecycle.py:40: from weft.core.tasks.sessions import CommandSession
      tests/tasks/test_runner.py:38: from weft.core.tasks import sessions
      tests/tasks/test_runner.py:39: from weft.core.tasks.agent_session_protocol import make_booted_response, ... (6 names)
      tests/tasks/test_runner.py:48: from weft.core.tasks.sessions import AgentSession
      tests/tasks/test_runner.py:565, :630, :670, :884, :953: string 'weft.core.tasks.sessions'
      tests/tasks/test_task_execution.py:61: from weft.core.tasks import sessions
      tests/tasks/test_task_execution.py:68: from weft.core.tasks.sessions import AgentSession, CommandSession
      tests/tasks/test_task_execution.py:1382, :1433, :1591, :1623, :1842, :1875: string 'weft.core.tasks.sessions'
      ```

      Every line is a rewrite site above; there is no other.
    - `host.py`: delete the module-scope `register_builtin_agent_runtimes()`
      (line 101). Call it in `_worker_entry` immediately before
      `execute_agent_target(` (inside the `spec_data["type"] == "agent"` branch,
      line 181 at `b7eed9f9`) and in `_agent_session_worker_entry` immediately
      before `start_agent_runtime_session(` (line 307). These are the only two places
      in `weft/`, `extensions/`, and `integrations/` that run an agent (grep in
      P23). The function is idempotent (`weft/core/agents/backends/__init__.py`
      tolerates "already registered"). Validation already registers on its own
      (`weft/core/agents/validation.py:37`, `:72`).
    - `weft/_runner_plugins.py`: delete the late import at line 45 and add
      `from weft.core.runners.host import get_runner_plugin as
      get_host_runner_plugin` at module scope. `get_runner_plugin` keeps its
      `DEFAULT_RUNNER_NAME` branch, so resolution order is unchanged.
    - `tests/architecture/test_import_boundaries.py:831`: rename
      `test_host_import_registers_builtin_agent_backends` to
      `test_host_import_does_not_load_builtin_agent_backends` and assert that
      `weft.core.agents.backends.llm` and `...provider_cli` are absent after
      `import weft.core.runners.host`. Add the same absence assertion after
      `import weft.core.runner_validation` and `import weft.cli.app`. This
      replaces the import-time contract with the [AR-7] one ("when the
      host/runtime boundary explicitly requests built-in registration"). Remove
      the strict-xfail marker from the Task 11 test.
    - `docs/ruff-suppression-registry.md`: the nine rows naming
      `weft/core/tasks/sessions.py` or `weft/core/tasks/agent_session_protocol.py`
      (`RUFF-SUP-047`, `-049`, `-249`, `-299`, `-300`, `-303`, `-304`, `-307`,
      and the `RUFF-SUP-344` row Task 6 rewrote) change path; then
      `bin/ruff_suppression_index.py --write` and `--check`.
    - Spec implementation-mapping paths, path text only, in six spec files
      (00, 01, 06, 07, 10, 13):
      `docs/specifications/00-Overview_and_Architecture.md:182`,
      `01-Core_Components.md:540`, `:706`, `:1221`, `:1299`,
      `06-Resource_Management.md:314`, `07-System_Invariants.md:242`, `:256`,
      `10-CLI_Interface.md:277`, `13-Agent_Runtime.md:511`, `:518`, `:877`,
      `:879`. Add this plan as a backlink next to the [AR-7] mapping.
    - Theory locator (owner item 4): in `docs/program-theory.md:383`, the
      "Agent runtime and session" row of the [WT-3] ownership table, change
      `weft.core.tasks.sessions` to `weft.core.runners.sessions` in the Owner
      column, as the proposed row in `## Proposed Guidance and Theory Delta`
      gives it, in the same commit as the move. No other theory text changes.
    - Check: the AST gate lists no row for `weft/_runner_plugins.py`; the
      AST-resolved inventory command above prints nothing; for the non-Python
      surfaces, `grep -rn
      "core.tasks.sessions\|core.tasks.agent_session_protocol"
      docs/specifications docs/ruff-suppression-registry.md docs/program-theory.md`
      prints nothing; `grep -c "weft.core.runners.sessions" docs/program-theory.md`
      prints `1`; the Task 11 test passes without its marker; in fresh
      interpreters, `weft.core.agents.backends.llm` is absent after each of
      `weft.cli.app`, `weft.client`, `weft.core.manager`,
      `weft.core.runner_validation`, and `weft.core.task_evidence`.
    - Tests: `tests/architecture/test_import_boundaries.py`,
      `tests/tasks/test_runner.py`, `tests/tasks/test_task_execution.py`,
      `tests/tasks/test_agent_execution.py`,
      `tests/tasks/test_task_interactive.py`,
      `tests/commands/test_active_interactive_lifecycle.py`,
      `tests/core/test_agent_runtime.py`, `tests/core/test_llm_backend.py`,
      `tests/core/test_provider_cli_backend.py`,
      `tests/core/test_provider_cli_session_backend.py`,
      `tests/core/test_tool_profiles.py`, `tests/specs/test_ruff_policy.py`,
      `tests/specs/test_ruff_suppression_index.py`. The real one-shot agent
      proof is `tests/tasks/test_agent_execution.py:294`
      (`test_task_runner_executes_agent_successfully`), not the mocked
      one-shot orchestration case in `tests/tasks/test_runner.py`; the real
      persistent-session proof is `tests/tasks/test_agent_execution.py:629`
      (`test_consumer_persistent_agent_per_task_continues_conversation`). These
      and the host-command paths in `tests/tasks/test_runner.py` run the real
      spawned worker; do not mock `register_builtin_agent_runtimes`.
    - Stop if: any process runs an agent without passing through
      `_worker_entry` or `_agent_session_worker_entry`, a test or extension
      looks a backend up after importing host without running an agent, or a
      queue payload, pickle, or persisted record names the old module paths.
      P23 found none of these; any one of them makes O3 a behavior change.

## 7. Testing Plan

Tasks 1–10 add no tests. Task 4 retargets the six `PromptSession` patch lines
in `tests/commands/test_active_interactive_lifecycle.py` (lines 263, 297, 410,
585, 669, 759) at the `weft/cli/deferred.py` accessor, with assertions
unchanged (owner item 1). Tasks 11–13 strengthen and rename one architecture
test (`test_weft_has_no_eager_import_cycles` becomes
`test_weft_import_graph_is_acyclic_including_function_level_imports`, Task 11),
retarget five clock-patch lines to the module that now owns the helpers
(Task 11), and flip one pinned closure test
(`test_host_import_registers_builtin_agent_backends` becomes
`test_host_import_does_not_load_builtin_agent_backends`) and rename test
import paths and logger names (Task 13). The Task 13 test edits are no longer
conditional: the owner ruled shape O3 (owner item 3). The strengthened test is the failing proof for Tasks
11–13: at `b7eed9f9` it reports three SCCs (P22), and each task removes one.
Every change is behavior-preserving. The proof that each change is
needed is the before/after output of each change group's grep or AST check. The
proof of no regression is the targeted tests listed in each task, which run real
broker and process paths, plus the one full-suite run in Verification. Do not add mocks to cover these edits. If a listed test
fails, the edit changed behavior. Revert that task's hunks with Edit and
re-read.

## Verification

```bash
. ./.envrc
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python bin/ruff_suppression_index.py --check
bin/check-doc-paths   # the CLAUDE.md block, the theory row, and the spec mapping paths, like any other document edit
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py tests/specs/test_ruff_policy.py tests/specs/test_ruff_suppression_index.py tests/specs/test_plan_metadata.py tests/cli/test_env_file_bootstrap.py -q
./.venv/bin/python -m pytest tests/commands/test_run_public.py tests/commands/test_run.py tests/commands/test_active_interactive_lifecycle.py tests/commands/test_control_observation_connections.py tests/commands/test_result.py tests/commands/test_serve.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_runtime_identity.py tests/core/test_manager_family_maintenance.py tests/core/test_builtin_agent_images.py tests/tasks/test_liveness_monitor.py tests/tasks/test_task_interactive.py tests/tasks/test_runner.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_event_observation_connections.py tests/commands/test_task_snapshot_reducer.py tests/core/test_ops_shared.py tests/commands/test_status.py tests/core/test_client.py -q   # Task 11
./.venv/bin/python -m pytest tests/taskspec tests/core/test_spec_parameterization.py tests/commands/test_specs.py tests/core/test_builtin_dockerized_agent.py tests/core/test_builtin_platform_support.py -q   # Task 12
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/tasks/test_agent_execution.py tests/core/test_agent_runtime.py tests/core/test_llm_backend.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_session_backend.py tests/core/test_tool_profiles.py -q   # Task 13
./.venv/bin/python -m pytest -q   # fast suite, from current state, before the completion claim
```

Also run, once, after all tasks. The final gate has two parts. (1) Zero
function-level imports of weft modules outside the two owner item 2 sites: the
AST gate (P2) must print `TOTAL 10`, and the only rows that import a `weft.*`
module are `weft/bootstrap.py:110` and
`weft/core/agents/backends/__init__.py:17`, `:18`. The other seven rows are
`bootstrap.py:107` from `simplebroker.ext`, `weft/builtins/agent_images.py:38`
from `weft_docker`, `weft/core/deferred.py:12` `setproctitle`, the three
`simplebroker_pg` imports, which Task 5 moves into `weft/core/deferred.py`
without changing the count, and the prompt-toolkit import inside
`weft/cli/deferred.py` (owner item 1). P23 measured `TOTAL 9` on the fully
restructured copy with prompt-toolkit hoisted; the tenth row is the accessor,
so `10` is derived from P23, not measured. Every one of the ten carries a
comment that names a permitted §4.2 reason or the named exception. (2) The
import graph counting late imports and package-initializer edges is acyclic:
`test_weft_import_graph_is_acyclic_including_function_level_imports` passes
with no xfail marker. Then run: the Task 2 closure check; the Task 4
prompt-toolkit closure check; the Task 5 gate and SQLite probe; the Task 3 and
Task 13 document checks (`CLAUDE.md` grep, theory grep, spec-path grep); and
one fresh-process timing of `import weft.cli.app` (three fresh interpreters,
`time.perf_counter()` around the import, as in P21), recorded next to the
difference register and compared with the ~130 ms baseline. Expected: within
the accepted increase, an estimated 26–35 ms now that prompt-toolkit stays
behind its accessor (§4 register). The incremental probes P6 and P10 are not
rerun; after the hoist they measure cached imports.

Class-5 completion ([WT-7] in `docs/program-theory.md`, "Possession probe,
keyed", and the theory-versus-code audit refresh), in the form
`docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md` Task 5 uses: the
keyed possession probe (the owner writes the key first; an agent that did not
author this plan, from a different family, answers from
`docs/program-theory.md` alone; key and result in `## Review Log`), and a
refresh, by reference, of the audit-table rows in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (`### Audit
table`) whose evidence names a file this plan touched, starting with row
4.4-c, recorded in `## Execution log` without editing that plan. Then the
pre-landing different-family review that +P requires, over the landed diff.

## Rollback

Every task is independent and reversible. Revert a task's hunks with Edit, never
with `git checkout` on shared files. The `CLAUDE.md` §4.2 block (Task 3) and
the `docs/program-theory.md:383` row (Task 13) are document hunks in the same
commit as the code; they revert with it, and reverting Task 3 or Task 13 by
hand restores the block's "Current text:" or the old locator respectively.
Task 4: delete `weft/cli/deferred.py`, restore the `try`/`except ImportError`
block in `weft/cli/run.py`, and restore the six
`"prompt_toolkit.PromptSession"` patch lines. Per task for 11–13:

- Task 11: delete `weft/commands/_observation.py` after moving its seven
  functions back into `weft/commands/tasks.py` at their old positions; restore
  `import weft.commands.tasks as task_ops` and the `task_ops.` prefixes in
  `events.py`, the late import in `cmd_task_status`, and the five test patch
  lines; restore `test_weft_has_no_eager_import_cycles` with its old name
  and body. Nothing persisted refers to these
  modules.
- Task 12: move `validate_parameterization_adapter_ref` back to
  `parameterization.py`, restore `model.py:61`, and restore the late import.
- Task 13 (O3): reverse the two `git mv`s and the path rewrites (code, tests,
  registry rows, spec mapping lines), restore `register_builtin_agent_runtimes()`
  at host module scope and remove the two call-site calls, restore the late
  import in `_runner_plugins.py`, and restore
  `test_host_import_registers_builtin_agent_backends`, and restore
  `weft.core.tasks.sessions` in the `docs/program-theory.md:383` row. (O1,
  rejected, would also have needed the `pyproject.toml` entry removed and a
  reinstall, the rollout-order dependency O3 avoids.) No queue data, persisted format, or config
is touched, so no migration or rollout order is involved. If the work has
landed, `git revert` the commit.

## Out of Scope

- The 16 excluded findings (E1–E16). Amending `CLAUDE.md` §4.2 for import
  economy: the ruling rejected it. (The spec-ordering reason owner item 2
  admitted is in scope, Task 3, and the named prompt-toolkit exception owner
  item 1 granted is in scope, Task 4; neither admits import economy as a
  general reason.)
- Moving, hoisting, or restructuring `weft/bootstrap.py:107`, `:110` or
  `weft/core/agents/backends/__init__.py:17-18`. Owner item 2 kept them late;
  Task 3 edits only their comments.
- A committed gate that inventories function-level imports (an allow-list of
  the remaining late-import sites). That would change how future work is
  verified (+P). If the owner wants it, it gets its own change. The Task 11
  acyclic-graph test is not that gate: it forbids cycles, not late imports,
  and needs no allow-list (the owner item 2 sites close no cycle, P22).
- Making `weft/_runner_plugins.py` implementation-free (shape O1, O4, or a
  registration hook): the owner chose O3 (owner item 3, 2026-11-03).
- A re-export shim at `weft.core.tasks.sessions` or
  `weft.core.tasks.agent_session_protocol` ([ALT-WT-012]; owner item 4).
- Rewording the theory's module locators at a different altitude. The
  reviewer's [WT-0] observation under owner item 4 is a note for the owner,
  not a task.
- Renaming the 31 private-name imports, or the three `weft/commands/tasks.py` helpers.

## Review Log

- self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 15 corrections — closure-pin test lines 805/815 to 806/816; P5 grep pattern matched unrelated modules; `run_manager_process` D row measured on the wrong closure (CLI cost is +1 module, ~0.1 ms); that D row added to a single owner-decision list with a fresh-eyes finding; Task 6 registry steps reordered (`--write` validates first) and the missed global `BLE001` inventory and `EXPECTED_DIRECTIVE_COUNT` pin added; Task 8 check would never pass (`WEFT_DEBUG` is a real env var); Task 9 adds the missing [CLI-1.4]; Task 7 P13 rerun must exclude `docs/plans/`; four missing PromptSession patch sites; Task 5 "postgres branch" wording fixed for the two manager methods; H8 line range 148-152; E8 line 2787; H1 "cycle comments" wording; X1 full paths; line-shift note between tasks plus P19/P20 probes
- owner ruling applied 2026-11-02: Reading B (see Ruling).
- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass and the owner's Reading B ruling): BLOCKED on F1 (Task 5 gate matched the `TYPE_CHECKING` import at manager.py:240), accepted and applied. F2 (bootstrap 107/110 and the backends deferral listed for the owner as explicit §4.2 exceptions; accessor alternative needs the same), F3 (fresh-interpreter SQLite probe), F4 (post-hoist timing gate separated from the closure check), F5 (non-reproducible inventories deleted): P2, accepted and applied; performative bookkeeping trimmed. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1, F2–F5 confirmed): no blocker; all five closed with live evidence (the AST gate reports exactly manager.py:2919, :2926 and runtime_identity.py:230 and nothing after their removal; the owner list names bootstrap.py:107/110, backends/__init__.py:17–18 and the accessor exception; the fresh-interpreter SQLite probe prints `simplebroker_pg loaded: False`; closure presence and timing separated; disputed inventories deleted). Review loop closed; the plan waits on the owner's three decisions (prompt-toolkit hoist or accessor; the §4.2 third reason for bootstrap and backends; the run_manager_process D row).
- owner ruling applied 2026-11-02: no cycles; Tasks 11–13 added (cross-family round on the addition pending).
- Cross-family review round 1 on the no-cycles addition (codex 0.161.0, 2026-11-02, scoped to Tasks 11–13, the acyclic test, class 4 and owner item 3): BLOCKED on F1 (two package-form `sessions` imports in tests missed by the grep), accepted and applied with an AST-resolved inventory. F2 (O3 moves registration inside worker timing and error handlers; C row expanded), F3 (hybrid O4 added), F4 (nine registry rows), F5 (theory locator at program-theory.md ~383 stranded by the move; owner item 4, class-5 by the theory's rule), F6 (existing cycle test strengthened instead of a second test): P2, accepted and applied. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1, F2–F6 confirmed): no blocker; all six closed with live evidence (the AST inventory reproduces 21 sites including test_runner.py:38 and test_task_execution.py:61; O3's C row matches host.py:145 and :293; O1/O3/O4 listed; nine registry rows; owner item 4 gates Task 13 on the theory locator at program-theory.md:383; the existing guard at test_import_boundaries.py:629 is strengthened in place). The 'only shape' sentence was corrected after the round. Review loop on the addition closed; the plan waits on owner items 1–4.
- owner rulings applied 2026-11-03: item 1 prompt-toolkit stays late through a `weft/cli/deferred.py` accessor under a named §4.2 exception; item 2 `CLAUDE.md` §4.2 gains a third permitted reason (import order a governing spec requires) and the bootstrap and backends sites keep their late imports with spec-citing comments; item 3 Task 13 runs with shape O3; item 4 the theory locator at program-theory.md:383 is repaired in one line with Task 13, no shim. Class 4 → 5+P (theory line; `CLAUDE.md` edit). Cross-family round 1 on the rulings as applied (codex 0.161.0, 2026-11-03): BLOCKED on F1 (P2, the backends `[AR-7]` prefix and the accessor's wrapped comment do not satisfy the AST gate, which reads only the physical line before an import; line 18 had no preceding comment): accepted, one self-contained comment line now sits directly before each backend import and before the accessor import. F2 (P2, the provider test proves execution under the task's env and CWD, not registration there; the startup-error test injects a session-creation failure): accepted, coverage prose narrowed, no suite added. Recommendation 5 (carry the theory row as a Replacement block so `bin/check-doc-paths` verifies it after promotion): accepted. Round 2 (codex 0.161.0, 2026-11-03, scoped to F1 and F2, theory block confirmed): no blocker; F1 closed (a complete comment line now precedes each of backends/__init__.py:17 and :18 and the accessor import; the live file confirms the wrapped comment preceded only line 17), F2 closed (test_agent_execution.py:309–325 asserts execution env and CWD; test_runner.py:4653–4656 injects a session-creation failure); the theory row's Current text matches program-theory.md:383 byte for byte. One observation applied without a further round: the claim that no test drives an agent-branch failure was too broad (test_agent_execution.py:876 drives a one-shot provider failure); the sentence now names the unpinned case as registration failure only. Review loop on the rulings closed; nothing in this plan waits on the owner (item 5's excluded findings go to their own plans).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9`: no normative spec text changes. Task 9 cites existing sections
  of `docs/specifications/10-CLI_Interface.md` as of this commit. Task 13
  edits path text in `_Implementation mapping:_` notes of the six spec files
  it lists, as of this commit. `CLAUDE.md` lines 347–352 (Task 3) and
  `docs/program-theory.md` line 383 (Task 13) are quoted below as of this
  commit.
- Promotion baseline identifier: pending (the landing commit SHA; strategy B
  lands every document edit in the same commit as the code it governs).

## Proposed Guidance and Theory Delta

| Document | Strategy | Edit |
|----------|----------|------|
| `CLAUDE.md` §4.2 | B — atomic with Tasks 3 and 4 | lines 347–352, the import rule: three permitted reasons, and where named exceptions are listed (owner item 2; owner item 1) |
| `docs/program-theory.md` [WT-3] | B — atomic with Task 13 | line 383, the Owner column of the "Agent runtime and session" row: one module locator (owner item 4) |
| six spec files (Task 13 list) | B — atomic with Task 13 | path text in `_Implementation mapping:_` notes only; non-normative |

Why B: each edit describes the tree after its task. Under strategy A,
`CLAUDE.md` would permit a spec-ordering reason that no comment yet cites
and name a `weft/cli/deferred.py` that does not exist, and the theory would
name a module path that does not exist.

### `CLAUDE.md` §4.2 — replace the bullet that opens "Imports at the top of the module. Function-level imports are allowed only" and closes "readability or performance issue." (lines 347–352)

Current text:

```markdown
- Imports at the top of the module. Function-level imports are allowed only
  to break a real import cycle or to guard an optional dependency, and must
  carry a brief comment naming that reason; plain stdlib late imports are
  not allowed. House style is to put all deferred imports into a deferred.py file
  with a dedicated accessor function unless doing so would cause a significant
  readability or performance issue.
```

Replacement (`CLAUDE.md`):

```markdown
- Imports at the top of the module. Function-level imports are allowed only
  for one of three reasons, and must carry a brief comment naming that
  reason: (1) to break a real import cycle; (2) to guard an optional
  dependency; (3) to keep an import order that a governing spec requires,
  with the comment naming the spec section (for example `[CLI-5]`). Plain
  stdlib late imports are not allowed. House style is to put all deferred
  imports into a deferred.py file with a dedicated accessor function unless
  doing so would cause a significant readability or performance issue.
  Named exceptions granted by the owner are listed in the accessor module
  that holds them (today: `weft/cli/deferred.py`, prompt-toolkit,
  2026-11-03).
```

### `docs/program-theory.md` [WT-3] — the Owner column of the "Agent runtime and session" row (line 383)

A Replacement block, the form `docs/plans/2026-11-02-tid-display-encoding-plan.md`
uses for its theory edits, so that `bin/check-doc-paths` verifies the row
once this plan is promoted (the cross-family round of 2026-11-03 recommended
this over the proposed-row form the heartbeat plan uses for its owner-gated
[ALT-WT-004] edit). The row is applied by Task 13 in the same commit as the
module move, and only with the owner's acceptance (owner item 4, closed
2026-11-03: "Update the spec line"). The one change in the row is
`weft.core.tasks.sessions` becoming `weft.core.runners.sessions`. Every other
cell is unchanged. No re-export shim is kept at the old path ([ALT-WT-012]).

Current text:

> | Agent runtime and session | Agent-backed work run as an ordinary task: the agent TaskSpec section, the runtime adapters, and a per-task persistent conversation in a dedicated session subprocess; no second durable conversation store | The agent runtime modules (`weft.core.agents`); the owning task's session (`weft.core.tasks.sessions`) for persistent scope | `13-Agent_Runtime.md` [AR-0.0], [AR-5], [AR-6], [AR-8] |

Replacement (`docs/program-theory.md`):

```text
| Agent runtime and session | Agent-backed work run as an ordinary task: the agent TaskSpec section, the runtime adapters, and a per-task persistent conversation in a dedicated session subprocess; no second durable conversation store | The agent runtime modules (`weft.core.agents`); the owning task's session (`weft.core.runners.sessions`) for persistent scope | `13-Agent_Runtime.md` [AR-0.0], [AR-5], [AR-6], [AR-8] |
```

## Execution log

Plan authoring, 2026-11-02, at `b7eed9f9`. All commands ran from
`/Users/van/Developer/weft` with `./.venv/bin/python -I` unless shown otherwise.
The probe scripts lived in the session scratchpad, so the full logic of each is
summarized here.

- **P1 baseline gates.** `./.venv/bin/ruff check .` → `All checks passed!`;
  `bin/ruff_suppression_index.py --check` → exit 0;
  `pytest tests/architecture/test_import_boundaries.py -n 0 -o addopts="" -q` →
  `54 passed in 7.38s`.
- **P2 function-level imports.** An AST walk of `weft/**/*.py` that prints
  imports inside function bodies along with the preceding comment →
  `TOTAL 23`. The comment-bearing rows include `weft/commands/run.py:385` "breaks the real
  result/events cycle", `run.py:439/511/524` "avoids the tasks -> system -> run
  dependency closure", `tasks.py:2478` "breaks the intentional tasks <-> events
  helper cycle", `parameterization.py:131` "Local import breaks model ->
  parameterization -> transport -> model", `weft/cli/run.py:221` "Optional dependency
  ...", `manager.py:2919/2926` and `runtime_identity.py:230` optional PostgreSQL,
  and `deferred.py:12`. Rows with no comment: `_runner_plugins.py:45`,
  `bootstrap.py:110`, `builtins/agent_images.py:38`, `manager.py:2139`, `:2143`,
  `:2147`, `:2151`, `:2155`, `manager_runtime.py:2289`, and
  `agents/backends/__init__.py:18`, whose comment sits above line 17.
- **P3 static hoist probe.** This imports the helpers from
  `tests.architecture.test_import_boundaries`, adds one module-scope edge per
  late import, and runs `_strongly_connected_components`. Baseline `[]`.
  `run -> tasks` gives `[]`, `run -> events` gives `[]`, and `tasks -> events`
  gives `[('weft.commands.events', 'weft.commands.tasks')]`. Across all 16
  in-package late imports, only `tasks.py:2478` and `parameterization.py:131`
  print `CYCLE`.
- **P4 runtime closure.** `import weft.commands.tasks` → `run loaded by tasks:
  False`. `import weft.commands.events` → `weft.commands.run` False and
  `weft.commands.tasks` True.
- **P5** `grep -rnE "^(from weft\.commands\.run |import weft\.commands\.run)" weft`
  → only `weft/cli/run.py:28`; `grep -rnE "^from \.run |^from \. import run" weft/commands`
  → nothing. (The draft's `^from \.run` pattern also matched `.runtime_identity`,
  `.runner`, `.run_input`, and `weft/cli/app.py:49`'s `from .run` of `weft.cli.run`.)
- **P6 CLI cost.** `import weft.cli.app` then `import weft.commands.tasks,
  weft.commands.events` → `cli.app 128 ms; +tasks+events 5 ms`, `124/5`,
  `127/5`, with `extra modules: 8`. Events alone → `4.8 ms, 8 modules`. The
  closure of `weft.cli.app` contains `weft.commands.run` but not tasks, system,
  or events. `weft.client` contains tasks, system, and events.
- **P7** an AST pass over the module-scope imports of `weft/commands/run.py`
  lists six private names (listed above).
- **P8** an AST count of `_`-prefixed names imported from `weft` modules →
  `31`, with `weft/commands/run.py` contributing 9 (6 at module scope plus 3
  late).
- **P9** runtime probe: `import weft.core.runners.host` → `_runner_plugins
  loaded by host: True`. An import-trace hook shows the chain
  `weft/core/runners/host.py`, `weft/core/tasks/__init__.py`,
  `weft/core/tasks/base.py`. `transport` loads `parameterization`: True.
  `pipeline`, `heartbeat`, `task_monitor`, and `liveness_monitor` each load
  `weft.core.manager`: False. `weft.manager_process` loads
  `weft.core.manager_runtime`: False. `weft.cli.app` loads `weft.bootstrap`:
  False.
- **P10 other closures.** After `import weft.core.manager`: pipeline True,
  heartbeat True, task_monitor False, liveness_monitor False. Adding the two
  monitors → `25.7/24.8/25.0 ms, 22 modules`. Adding `weft.manager_process` to
  manager_runtime → `0.2 ms, 3 modules` (`weft.core.launcher`,
  `weft.core.serve_log`, `weft.manager_process`); over the `weft.cli.app`
  closure, which already holds the first two → `0.1 ms, 1 module`
  (fresh-eyes rerun). `weft.core.manager` is in the closure
  of both `weft.cli.app` and `weft.client`. `prompt_toolkit` is not loaded by
  `weft.cli.app`, and importing it costs `+56 ms` and `+26 ms` in two runs.
  `sed -n 24,48p pyproject.toml` lists `prompt-toolkit>=3.0.53` under
  `dependencies`, and `simplebroker-pg` under the `pg` extra.
- **P11** `sed -n 1,20p weft/bootstrap.py`: the module docstring says it
  "avoids importing the full Typer app ... until after WEFT_ENV_FILE has been
  applied". `grep -n "_runner_plugins" weft/core/tasks/base.py` → line 82.
- **P12** `diff <(sed -n 189,201p weft/core/tasks/sessions.py) <(sed -n 826,838p weft/core/tasks/sessions.py) && echo IDENTICAL`
  → `IDENTICAL`. `grep -n "RUFF-SUP-344" docs/ruff-suppression-registry.md` →
  lines 184 and 385, listing 2 directives.
- **P13** for each of the 17 deep-dive names, `grep -rnw <name> weft tests bin
  extensions integrations docs README.md CLAUDE.md pyproject.toml | grep -vE
  "(def|class) <name>"` → `refs=0` for all 17. `examples/` does not exist.
  Eleven of the names are `_`-prefixed (Task 7); the rest are public and not
  acted on here (E6). Because this plan now names the eleven, a rerun must drop
  `docs/plans/` hits.
- **P14** `grep -n WEFT_DEBUG weft/_constants.py` → lines 12 and 15, both in
  the docstring. `grep -c "^WEFT_DEBUG" weft/_constants.py` → `0`.
  `load_config()["LOGGING_ENABLED"]` → `False`, so the key exists.
- **P15** `grep -noE "\[CLI-(1|4|6)[^]]*\]" docs/specifications/10-CLI_Interface.md`
  → `[CLI-1.1]`, `[CLI-1.2]`, `[CLI-1.3]`, `[CLI-1.4]` and subsections,
  `[CLI-4]`, `[CLI-6]`. There is no bare `[CLI-1]`.
- **P16** `grep -n "def peek_generator" -A12 .venv/.../simplebroker/sbqueue.py`
  → the `with_timestamps: Literal[True]` overload returns
  `CloseableIterator[tuple[str, int]]`. `grep -rn _read_outbox_task_result weft tests`
  → one caller, `weft/commands/result.py:205`.
- **P17 broad handlers.** An AST walk of `ExceptHandler`s whose type is
  `Exception`, `BaseException`, or bare → `broad handlers: 173 without
  same-line comment: 54`. Of those, the ones with no `raise` in the handler
  body → `count 4` (`manager.py:4643`, `:4669`, `multiqueue_watcher.py:1078`,
  `:1090`), and all four re-raise later (`manager.py:4760`,
  `multiqueue_watcher.py:1103`).
- **P19 patch-site scan (fresh-eyes).** `grep -rn` in `tests/` for
  `setattr`/`patch` of the hoisted names: `weft.commands.tasks._task_control_result`
  at `tests/core/test_client.py:270` and `:324` (client `stop_many`/`kill_many`
  path, not `weft/commands/run.py`); `manager_process.run_manager_process` at
  `tests/system/test_manager_process.py:169`, `:232`, `:267`, `:347` (they call
  `manager_process.main`, not `_run_manager_process_foreground`);
  `tests/commands/test_serve.py` patches `_run_manager_process_foreground`
  itself. None is bypassed by Task 1 or Task 2. A scratch copy of `weft/` with
  the Reading B hoists and all Task 2 hoists applied passed
  `tests/architecture/test_import_boundaries.py` (`54 passed`), and
  `import weft.cli.app`, `weft.client`, and `weft.manager_process` all succeeded.
- **P20 Task 6 dry run (fresh-eyes).** In a scratch copy with the merge applied,
  `bin/ruff_suppression_index.py --write` first failed on the global inventory
  (`actual BLE001=131 ... approved BLE001=132`), then on
  `RUFF-SUP-344 has 1 directives; approved cardinality is 2`. After both
  registry edits it passed and rewrote line 385 to
  `sessions.py::_poll_monitor_limits | 1 | BLE001=1`; `--check` passed;
  `tests/specs/test_ruff_policy.py::test_approved_suppressions_match_the_standalone_registry`
  failed `324 == 325` until `EXPECTED_DIRECTIVE_COUNT` became `324`. ruff and
  mypy were clean on the merged file.
- **P21 full Reading B hoist set (owner ruling, 2026-11-02).** A scratch copy
  of `weft/`, `tests/`, `bin/`, and `pyproject.toml` with every Task 1, 2, and 4
  hoist applied: the four `weft/commands/run.py` imports, all five
  `weft/core/manager.py` imports (pipeline, edge, heartbeat, `TaskMonitor`,
  `LivenessMonitor`), `run_manager_process`, and `import prompt_toolkit` with the
  call-time `prompt_toolkit.PromptSession()` lookup. `ruff check` on the four
  files → `All checks passed!` after isort fixed 3 orderings; `mypy` on the four
  files → `Success: no issues found in 4 source files`. With `PYTHONPATH` on the
  copy (asserted via `weft.__file__`), `import weft.cli.app`, `weft.client`,
  `weft.manager_process`, `weft.core.manager`, `weft.commands.tasks`, and
  `weft.commands.events` all succeeded. The AST gate on the copy → `TOTAL 12`,
  with no rows for `weft/commands/run.py`, `weft/cli/run.py`,
  `weft/core/manager_runtime.py`, or `manager.py:2139-2155`.
  `pytest tests/architecture/test_import_boundaries.py -n 0 -o addopts="" -q` →
  `54 passed`; `pytest tests/commands/test_active_interactive_lifecycle.py` →
  `22 passed` (the `prompt_toolkit.PromptSession` patches still apply).
  Latency, in-repo, sequential increments in one interpreter, three runs:
  `import weft.cli.app` 130–135 ms / 425 modules; then tasks+events 4.7 ms / 8;
  monitors 22.5–25.8 ms / 20; `weft.manager_process` 0.1 ms / 1;
  `prompt_toolkit` 28.1–30.3 ms / 196. Total about 56–61 ms and 225 modules on
  CLI start. (Wall times measured from the scratch copy were inflated about 2x
  for weft modules, unmodified copy included, so they are not used.)
- **Round 1 fixes (2026-11-02, at `b7eed9f9`).** The Task 5 gate as written
  above → `weft/core/manager.py:2919`, `:2926`, `weft/core/runtime_identity.py:230`,
  with no `manager.py:240` row. On a scratch copy of `weft/` with those three
  inline imports deleted → no output. The Task 5 SQLite probe →
  `simplebroker_pg loaded: False`, exit 0. `simplebroker/ext.py` reads no
  environment at module scope (grep), so `bootstrap.py:107`'s ordering rests on
  the module docstring's import-light contract, not on a measured behavior
  dependency; owner item 2 covers it either way.
- **P22 cycle inventory and Tasks 11–12 probe (second ruling, 2026-11-02, at
  `b7eed9f9`).** A graph probe reusing `_iter_import_edges` and
  `_strongly_connected_components`, counting every non-`TYPE_CHECKING` edge to
  `weft.*` (function scope included) plus the package-initializer edge (the
  Task 11 test's logic), on the repository → `late weft edges: 16`, three
  SCCs: `('weft.commands.events', 'weft.commands.tasks')`, the three taskspec
  modules, and a 14-module SCC containing `weft._runner_plugins`,
  `weft.core.runner_validation`, `weft.core.runners.host`, `weft.core.tasks`,
  `weft.core.tasks.base`, and the other `weft.core.tasks` modules. The
  bootstrap and backends late imports are in no SCC. A breadth-first path
  search from `weft.core.runners.host` to `weft._runner_plugins` → only
  `host -> weft.core.tasks -> weft.core.tasks.base -> weft._runner_plugins`.
  Scratch copy of `weft/`, `tests/`, `bin/`, `pyproject.toml` with the Reading
  B hoists (P21 script) plus Tasks 11 and 12 as written: `ruff check .` → `All
  checks passed!`; `mypy weft tests/commands/test_task_commands.py` →
  `Success: no issues found in 160 source files`; graph probe → one SCC (the
  runner-plugins cycle) and 4 late weft edges (`_runner_plugins.py:45`,
  `bootstrap.py:110`, `backends/__init__.py:17`, `:18`). With the copy first on
  `sys.path` (asserted via `weft.__file__`), `weft.cli.app`, `weft.client`,
  `weft.manager_process`, `weft.core.manager`, `weft.core.taskspec`,
  `weft.core.runners.host`, `weft.commands.events`, `weft.commands.tasks`,
  `weft.core.taskspec.parameterization`, and `weft.core.taskspec.transport`
  each imported cleanly. `pytest -n 0` on the copy, no Manager started:
  `tests/architecture/test_import_boundaries.py`, the two harness-free
  `follow_task_events` tests in `tests/core/test_ops_shared.py`,
  `tests/commands/test_task_snapshot_reducer.py`,
  `tests/core/test_spec_parameterization.py`, `tests/taskspec`,
  `tests/commands/test_specs.py`, four `test_task_commands.py` tests → `224
  passed`, 4 failed, all in `test_event_observation_connections.py`, which
  patched `events_cmd.task_ops.time`; after retargeting those three lines →
  `14 passed`. Earlier shapes that failed, and why the task text reads as it
  does: putting the helpers in `_task_snapshot_reducer.py` failed
  `test_reducer_has_no_io_or_runtime_probe_dependencies` (the reducer may not
  import `time`); a module-scope `from .events import follow_task_events`
  failed `test_canonical_task_status_watch_returns_typed_event_stream`, which
  patches the events module; and moving the deadline helpers without
  retargeting failed `test_deadline_from_timeout_clamps_at_zero` and
  `test_deadline_helpers_share_exact_expiry_boundary`.
  `test_terminal_snapshot_positive_timeout_stops_at_deadline` and
  `test_await_control_surface_public_grace_outlives_expired_kill_ack` (the
  other two `task_cmd, "time"` patchers) → passed unchanged. Closure counts
  (fresh interpreters): `weft.cli.app` 685 → 686, `weft.client` 419 → 420
  (`weft.commands._observation`), the other four entries unchanged. The P21
  scratch `weft/commands/run.py` needed `ruff format` after the Task 1 hoist,
  so run the formatter on every file a task edits.
- **P23 cycle-3 options (2026-11-02, at `b7eed9f9`).** In-repo, three fresh
  interpreters each: after `import weft.cli.app`, `import
  weft.core.runners.host` → `42 ms, +190 modules`, `llm` loaded; splitting it,
  `register_builtin_agent_runtimes()` → `41–42 ms, +188` and the rest of host →
  `0 ms, +2`. None of `weft.cli.app`, `weft.client`, `weft.manager_process`,
  `weft.core.manager`, `weft.core.taskspec`, `weft.core.tasks`,
  `weft.core.task_evidence`, `weft.commands.tasks`, or
  `weft.core.tasks.consumer` loads host today. `metadata.entry_points().select(
  group="weft.runners", name="host")` → `2.0`, `1.6`, `1.6 ms`. `grep` for
  importers of `weft.core.runners`: only `_runner_plugins.py:45` and
  `weft/core/tasks/runner.py:23` (`outcome`). `sessions.py` and
  `agent_session_protocol.py` import nothing from `weft.core.tasks` except each
  other; their only importers are `host.py`, `debugger.py`, `sessions.py`, and
  three test files; no string reference to their dotted paths outside imports
  and 11 `caplog` logger names. `execute_agent_target` and
  `start_agent_runtime_session` are called only at `host.py:181` and `:307`.
  O3 applied on a second scratch copy (on top of P22): `ruff check .` → `All
  checks passed!`; `mypy weft` plus the three moved-path test files →
  `Success: no issues found in 162 source files`; graph probe → `SCC count 0`,
  3 late weft edges (`bootstrap.py:110`, `backends/__init__.py:17`, `:18`); the
  P2 AST gate → `TOTAL 9`. All six brief entry modules plus
  `weft.core.runner_validation` and `weft.core.task_evidence` import cleanly,
  and `weft.core.agents.backends.llm` is absent after each. Closures:
  `weft.cli.app` 688, `weft.client` 422, `weft.core.manager` 391,
  `weft.core.tasks.consumer` 356 (each +2: host and its session module), the
  others unchanged. `pytest tests/architecture/test_import_boundaries.py
  tests/core/test_agent_runtime.py` → `65 passed`, 1 failed:
  `test_host_import_registers_builtin_agent_backends`, the contract O3 flips;
  `tests/core/test_provider_cli_backend.py`, `test_tool_profiles.py`,
  `test_provider_cli_session_backend.py`, `test_llm_backend.py` → `57 passed`.
  Mutations on the O3 copy, each a function-level import appended to one file,
  run through the graph probe: `import weft.commands.tasks` in `events.py` →
  SCC of 2; `from .parameterization import materialize_taskspec_template` in
  `model.py` → SCC of 3; `from weft.core.tasks import base` in `host.py` → SCC
  of 14. The harness-backed runner, interactive, and agent-task tests were not
  run (no Manager in this probe); they are in Task 13's list. O1 was not
  applied: it needs installed distribution metadata, which a scratch copy on
  `sys.path` does not have.
- **P24 session-module import inventory (2026-11-02, at `b7eed9f9`).** The
  AST-resolved inventory command in Task 13, run from the root of the
  pre-move scratch copy (`weft`, `tests`, `bin`) and from the live tree
  (`weft`, `tests`, `bin`, `extensions`, `integrations`), printed the same 21
  lines (diff empty); the live-only directories had no hit. The two
  package-form imports `tests/tasks/test_runner.py:38` and
  `tests/tasks/test_task_execution.py:61` appear in it and not in the earlier
  dotted-path grep. The 11 string hits match the 11 `caplog` logger sites.
  `grep -c` over `docs/ruff-suppression-registry.md` for the two old paths →
  `9` rows. `weft/core/runners/host.py` read for owner item 3's line numbers
  (`:145`, `:159`, `:197`, `:258`, `:293`, `:304`, `:363`).
- **Path check.** Every backticked full repository path in this plan was
  resolved with `test -e` at authoring time. Five do not resolve, by design:
  `weft/commands/deferred.py`, named as a file not to create;
  `weft/cli/deferred.py`, named in owner item 1 as the alternative to the
  prompt-toolkit hoist; and three new paths from Tasks 11 and 13,
  `weft/commands/_observation.py`, `weft/core/runners/sessions.py`, and
  `weft/core/runners/agent_session_protocol.py`. Short forms such as `manager.py:2139` refer to the full path
  given earlier in the same section. `bin/check-doc-paths` reported no failure
  in this file.
- **P25 owner-rulings verification (2026-11-03, at `b7eed9f9`).**
  `weft/cli/` holds `__init__.py`, `app.py`, `run.py`; no
  `weft/cli/deferred.py` exists, and `weft/cli/__init__.py` imports nothing.
  `weft/cli/run.py:219-223` holds the `try`/`except ImportError` around
  `from prompt_toolkit import PromptSession` (line 221) under the comment
  "Optional dependency is imported only for the interactive terminal mode."
  `grep -rn PromptSession tests weft` → six
  `monkeypatch.setattr("prompt_toolkit.PromptSession", lambda: prompt)` lines
  in `tests/commands/test_active_interactive_lifecycle.py` (263, 297, 410,
  585, 669, 759) and no other patch site. Fresh interpreter:
  `import weft.cli.app` leaves `prompt_toolkit` out of `sys.modules`
  (`False`). Accessor pattern read from `weft/core/deferred.py` and its users
  (`weft/core/process_title.py:17`, `:50`; `tests/core/test_process_title.py:16`,
  `:34`, `:68`). `CLAUDE.md:347-352` is the §4.2 import bullet quoted in the
  delta (byte-compared). `weft/bootstrap.py:104-106` comment names no spec;
  line 110's preceding line is `try:`. `weft/core/agents/backends/__init__.py:15-16`
  carries a reason comment without a spec code; its docstrings cite [AR-7].
  `weft/core/runners/` holds `__init__.py`, `host.py`, `outcome.py`,
  `subprocess_runner.py`, so the O3 destinations do not collide.
  `grep -rn "core/tasks/sessions\|core/tasks/agent_session_protocol" docs/specifications`
  → hits in exactly six files (00, 01, 06, 07, 10, 13). `docs/program-theory.md:383`
  is the only theory line naming `weft.core.tasks.sessions` (byte-compared
  in the delta). `host.py` call lines: `execute_agent_target(` at 181,
  `start_agent_runtime_session(` at 307.
