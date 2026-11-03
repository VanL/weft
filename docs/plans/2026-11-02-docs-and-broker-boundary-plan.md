# Docs and the Broker Boundary: Security Account and Layer Ownership

Status: promoted
Source specs: docs/specifications/00-Overview_and_Architecture.md "Observability and Security"; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.2], [SB-0.3], [SB-0.4], [SB-0.4a]
Superseded by: none

Class: 5 (promotion strategy D, spec-authoring). The security account is new
normative text in `docs/specifications/00-Overview_and_Architecture.md`, and
the hygiene slice edits normative implementation-mapping text in six more
spec files; [DOM-15] puts any normative spec edit at class 5, clarification
included. hardening: N/A — no risky trigger (no execution path, contract,
persisted format, CLI shape, or one-way door changes; no code changes). Not
+P: the account restates [WT-2a.11] and [WT-5], which are already adopted;
it does not change how work is planned, reviewed, or verified. The
broker-boundary slice changes nothing in this repository; its outputs are
findings and two SimpleBroker request candidates.

Plan type: spec-authoring. This is cleanup plan 10 of the series named in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (§9 fork 2 (a);
appendix "Fork 2 candidates" item 1; "Rows that need the owner" item 6).

## 1. Goal

Write Weft's own security account into spec 00 by adapting SimpleBroker's
(the owner's direction, 2026-11-02: "Adopt or adapt what simplebroker has for
security"), so the spec says what the program theory already says: what Weft
trusts by placement, what it checks for shape, what it recognizes as its own,
what bounds effect without deciding trust, and what it never does. Verify
from the tree whether any Weft code pulls queue mechanics up from
SimpleBroker ([WT-2a.8]). Settle the reconciliation audit's one unverified
broker-boundary observation. Repair the spec implementation-mapping pointers
that no longer resolve. README and the agent-context index were swept, and
nothing in them needs changing.

## 2. Source Documents

Source specs:
- docs/specifications/00-Overview_and_Architecture.md "Observability and
  Security", "Product Boundary"
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.2],
  [SB-0.3], [SB-0.4], [SB-0.4a]
- docs/specifications/07-System_Invariants.md [QUEUE.2a], [OBS.4]
- docs/specifications/06-Resource_Management.md [RM-0] (cites the 00
  section by heading and first sentence; both must survive)

Theory (governs interpretation; not a behavior contract):
- `docs/program-theory.md` [WT-2a.8], [WT-2a.11], [WT-5] (including the
  paragraph beginning "This is SimpleBroker's model, adopted"), [WT-4] (the
  push-down rule), [ALT-WT-014], [ALT-WT-019]

Sibling account (SimpleBroker repository, read at its working tree, HEAD
`fa95520`):
- `../simplebroker/docs/guides/configuration.md`, sections "Security" and
  "General security considerations". This is the text being adapted.
- `../simplebroker/extensions/simplebroker_pg/README.md`. It covers database
  existence, schema creation, and "No privileges are granted". The
  configuration guide has no PostgreSQL grant note, so the PostgreSQL
  paragraph below is adapted from this README instead (see §10, premise
  corrections).
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-6] (watcher
  subclass surface)
- `../simplebroker/docs/specs/13-message-identity.md` (`generate_timestamp`,
  `insert_messages`)

Plans:
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, `completed`.
  It assigns this scope. Its §9 fork 2 (a) and its appendix are the authority
  for the security slice. Its audit table gives plan 10 no drift row ("Plans 7
  and 10 have no row in this table"). This plan therefore starts on the
  owner's fork 2 (a) direction, not on a drift row. §8 below settles the one
  open broker-boundary observation (item 6).

Guidance:
- `CLAUDE.md` §1.1 (layer boundary with SimpleBroker), §4.11 (security), §5.1
- `docs/agent-context/decision-hierarchy.md` [DOM-15]
- `docs/agent-context/runbooks/writing-plans.md` §4b to §4d (strategy D)

## 3. Context and Key Files

Files to modify (spec-promotion slice only; no code):
- `docs/specifications/00-Overview_and_Architecture.md` (security account;
  backlink)
- `docs/specifications/01-Core_Components.md` (one mapping repair)
- `docs/specifications/04-SimpleBroker_Integration.md` (three mapping
  repairs)
- `docs/specifications/05-Message_Flow_and_State.md` (one mapping repair)
- `docs/specifications/09-Implementation_Plan.md` (one mapping repair)
- `docs/specifications/10-CLI_Interface.md` (one mapping repair)
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` (one mapping repair)
- `docs/plans/README.md` (Status Index row: added when this plan lands,
  set to `promoted` before Task 3, flipped to `completed` at closeout)

Read first:
- `docs/specifications/00-Overview_and_Architecture.md` (the whole file is 255
  lines; the security section is lines 219 to 232 at baseline)
- `docs/program-theory.md` [WT-2a.11] and [WT-5]
- `../simplebroker/docs/guides/configuration.md` "Security" and "General
  security considerations"
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] (context
  resolution, owner labels) and [SB-0.4a] (Monitor tables)

Current structure:
- `weft/context.py::build_context` resolves the project root by searching
  upward for the Weft-scoped broker project config (`PROJECT_CONFIG_PATH`
  `.weft`, `PROJECT_CONFIG_NAME` `broker.toml`), with project scoping on by
  default (`PROJECT_SCOPE` default `True` in `weft/_constants.py`; external
  name `WEFT_PROJECT_SCOPE`). It creates `.weft/`, `outputs/`, `logs/`, and
  `autostart/` when enabled, and loads `.weft/config.json`.
- `weft/helpers/__init__.py::write_file_atomically` writes a same-directory
  temporary file with ordinary file defaults, then replaces the target. Weft
  never sets or reads permission bits. `grep -rnE "chmod|st_mode|S_IMODE|umask"`
  over `weft/`, `extensions/*/weft_*`, and
  `integrations/weft_django/weft_django` returns no lines.
- `weft/core/control_messages.py::ControlRequest` enforces the strict
  envelope of [QUEUE.2a]. The envelope has no credential field.
- `weft/commands/submission.py` raises `NotImplementedError` for
  `submit_command(..., shell=True)`.
- `weft/context.py::_strip_backend_target_secret` and
  `weft/core/runtime_identity.py::canonical_runtime_context` strip a URL-userinfo
  password (`scheme://user:password@host`) from the keys and identities Weft
  derives. They keep the URL query and return a target with no scheme or
  netloc unchanged, so a libpq `password=…` keyword string or a URI query
  password stays in the derived identity (changing it changes the identity).
  Display goes through SimpleBroker's `display_target`
  (`WeftContext.broker_display_target`), which redacts those forms too.

Shared path, do not duplicate: none. This plan writes prose only.

### Comprehension questions (answer in the execution log before the first edit)

1. A stored TaskSpec validates cleanly and names `rm -rf "$HOME"` as its
   command. Under the account in §5, does Weft reject it, and what bounds it?
   Expected answer: Weft does not reject it. Validation checks shape only.
   The commands a TaskSpec names are trusted by placement, so the bound is the
   operating-system permissions of the user Weft runs as, or a restricted
   runner the TaskSpec selects. Adding a content check would be the sanitizer
   that [ALT-WT-014] refuses.
2. A quiet task watches its queues through a native activity waiter (the
   PostgreSQL case). SimpleBroker's `PollingStrategy.wait_for_activity`
   returns from its native idle poll with no hint. Does the task run a turn?
   Expected answer: No.
   `weft/core/tasks/multiqueue_watcher.py::_activity_hint_action` calls
   `_has_pending_messages()`: at most one `has_pending()` per eligible
   delivery queue (exactly one each on an empty, uninterrupted pass; `any()`
   stops at the first queue with pending work; notification-only
   observation interests have their own checks and are not in this count).
   With nothing pending it returns `"continue"`, and
   `_wait_for_activity_body` keeps waiting. The idle-poll cadence belongs to
   SimpleBroker: a base of `max(1.0, 10 × max_interval)` seconds, with the
   deadline jittered to between one and two times that base. The deadline
   is checked after a native wait finishes, so it is not a strict
   wall-clock bound on the return. Weft changes nothing.

A wrong answer is a stop-and-re-read blocker.

## 4. Invariants and Constraints

- Keep the heading `## Observability and Security` and its first sentence,
  "Weft assumes user-level trust, not hostile multi-tenancy."
  `docs/specifications/06-Resource_Management.md` [RM-0] cites both, and so
  does `AGENTS.md` §4.11.
- The existing four observability bullets and the "operational clarity"
  paragraph stay verbatim. The delta inserts text after them and removes
  nothing.
- No backticked repository-style path in spec text may name a file that
  does not exist in this repository. `bin/check-doc-paths` fails on any
  backticked `docs/…`, `skills/…`, `bin/…`, or `tests/…` token. Name the
  SimpleBroker guide by title only.
- No implementation-mapping claims are added for the new security text
  (strategy D). The mapping repairs in Task 2 correct existing claims. They
  claim nothing new.
- No code, test, theory, or SimpleBroker file changes in this plan.
- No wording in specs that `tests/specs/test_spec_hygiene.py` bans (plan
  status phrases such as "draft … plan").
- Every rule in the delta was checked against the tree (§3, §8, Execution
  log). Do not strengthen a clause during promotion. Stored-spec names, for
  example, are not shape-checked, and the delta does not claim they are.

## Spec Baseline

- `b7eed9f9` — docs/specifications/00-Overview_and_Architecture.md,
  01-Core_Components.md, 04-SimpleBroker_Integration.md,
  05-Message_Flow_and_State.md, 09-Implementation_Plan.md,
  10-CLI_Interface.md, 11-CLI_Architecture_Crosswalk.md at plan authoring
  time.
- Plan type: spec-authoring (the spec text is the deliverable).
- Promotion baseline identifier: diff base `da957a29` plus the worktree state
  of the promotion; the promotion lands as the single commit directly on top
  of `da957a29` that changes this plan file (a commit cannot record its own
  SHA; `git log --format=%h -1 -- docs/plans/2026-11-02-docs-and-broker-boundary-plan.md`
  names it).

## 5. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/00-Overview_and_Architecture.md | D — spec-authoring | "Observability and Security": insertion after the paragraph ending "what happened without guessing."; "Related Documents": backlink |
| docs/specifications/01-Core_Components.md | D — clarification | `_Implementation mapping_` paragraph after the validation-options text |
| docs/specifications/04-SimpleBroker_Integration.md | D — clarification | file-level `_Implementation mapping_` (line 11); the configuration mapping (line 725); the context-construction rules bullet (lines 761 to 763) |
| docs/specifications/05-Message_Flow_and_State.md | D — clarification | spawn reconciliation mapping (lines 1474 to 1476) |
| docs/specifications/09-Implementation_Plan.md | D — clarification | `weft/commands/specs.py` ownership bullet (lines 67 to 69) |
| docs/specifications/10-CLI_Interface.md | D — clarification | [CLI-1.2.3] `_Implementation mapping_` (lines 548 to 550) |
| docs/specifications/11-CLI_Architecture_Crosswalk.md | D — clarification | `spec` table row (line 33) |

Line numbers are at baseline `b7eed9f9`. Each block below is the exact text
the target file must contain after promotion. After promotion,
`bin/check-doc-paths` checks that it is contained in the target.

### 5.1 Security account (spec 00)

Insertion point: immediately after the paragraph that opens "The reason this
matters is operational clarity." and closes "what happened without
guessing.", and before `## Scope Boundaries`. Nothing is removed. Clause
audit of the existing section: the first sentence, the "explicitness and
containment" sentence, the four bullets (`weft.log.tasks`, process titles,
reserved queues, explicit broker/project context), and the operational-
clarity paragraph all remain unchanged.

Replacement (`docs/specifications/00-Overview_and_Architecture.md`):
```markdown
### Trust Domain

Weft operates within one operator trust domain. It trusts whatever the
operating system let the current user place or reach; the operating system,
filesystem permissions, and database grants decide who can. This is
SimpleBroker's security model, adopted: inputs that choose where the boundary
sits are trusted by placement, inputs that shape names inside it are checked
for shape, and intent is never inspected. The identity statement is
[WT-2a.11] in `docs/program-theory.md`; the sibling account is the "Security"
and "General security considerations" sections of SimpleBroker's
configuration guide.

**Trusted by placement.** Weft uses these as given:

- the broker target and its location: the SQLite database (by default
  `.weft/broker.db` under the project root) and the directory that holds it,
  or the PostgreSQL database and schema. Whoever can write the target can
  write any queue, including `weft.spawn.requests` and every `T{tid}.ctrl_in`.
- the Weft-scoped project config, by default `.weft/broker.toml`. Project
  scoping is on by default (`WEFT_PROJECT_SCOPE`), so the nearest such file at
  or above the discovery start directory selects the backend, target, and
  backend options for every command run beneath it, including absolute paths,
  parent paths, and remote backends. Do not run Weft beneath a project config
  you would not trust. An explicit root (`--context` or `WEFT_CONTEXT`) is
  trusted the same way.
- the Weft metadata directory: `config.json`, stored task and pipeline specs,
  and autostart manifests ([TS-1.2]) when autostart is enabled.
- the commands, Python callables, and agent runtimes a TaskSpec names. A
  TaskSpec that validates may run any program the current user can run.

**Checked for shape.** Weft validates shape for correctness and portability,
not as a trust boundary:

- TaskSpecs: every public TaskSpec model rejects unknown fields ([TS-1]), and
  `spec` and `io` are immutable after creation.
- control messages: the strict envelope of [QUEUE.2a]; a rejected row is
  acknowledged, never obeyed.
- task IDs and message IDs: canonical 19-digit decimal identifiers
  ([SB-0.2]).
- queue names: SimpleBroker validates every queue name; Weft composes its own
  names from validated task IDs.
- names Weft derives: `WEFT_DIRECTORY_NAME` must be a single directory name,
  not a path; process-title segments keep only letters, digits, `_`, and `-`
  ([OBS.4]); PostgreSQL owner labels match `[!-~]{1,54}` ([SB-0.4]).

**Recognized as its own.** SimpleBroker records a magic string and a schema
version in its broker metadata. It refuses a database whose recorded magic
string is not its own or whose recorded schema version is newer than it
supports. A database with no broker metadata is not refused: SimpleBroker
installs its metadata and tables there and leaves any existing tables in
place. Weft accepts a
stored task or pipeline spec only when it validates as one; an existing
`.weft/config.json` that is not a JSON object is an error that Weft reports
without rewriting the file ([SB-0.4]); Monitor tables are versioned and
verified before use ([SB-0.4a]). Recognition protects correctness. It says
nothing about who wrote the artifact.

**Bounds on effect, not trust.** Resource limits ([RM-0]) and restricted
runners, such as the first-party Docker, macOS sandbox, and Microsandbox
runner extensions ([CC-3.1], [TS-1.3]), bound what a task may do. They do not
decide whether a caller is trusted. Resource limits are a cooperative
operational control, not isolation against an adversarial task.

**Never.** Weft does not:

- authenticate in band: control messages, spawn requests, and owner labels
  carry no credential or signature, and owner labels confer no authority.
  Anyone who can write a queue can send to it.
- treat a sanitizer as a boundary: commands run as argv lists, never through
  a shell (`shell=True` is rejected), and message content is not inspected
  for intent ([ALT-WT-014] in `docs/program-theory.md`).
- infer confidentiality or integrity from file mode, ownership,
  parent-directory permissions, or ACLs, and it does not chmod, tighten,
  repair, or preflight them ([CTX.5]). Weft creates files with ordinary
  defaults (on POSIX, the process umask applies).
- provision operating-system users, groups, ACLs, PostgreSQL roles, or
  grants.

### Shared Access

**SQLite with more than one OS user.** Every intended writer needs effective
read and write access to the broker database and to every companion file
SimpleBroker creates or may create (its journal, WAL, shared-memory, lock,
status, and vacuum-lock files). The containing directory must let every
writer traverse it and create, replace, and remove entries. Weft adds entries
of its own: `config.json` in the metadata directory and the same-directory
temporary files that replace it atomically, `outputs/`, the logs directory
(`WEFT_LOGS_DIR`, by default `.weft/logs`), and `autostart/` when enabled.
Shared writers need the same rights there. On POSIX that means suitable file
permissions or ACLs plus directory write and execute permission; on Windows,
the equivalent file and directory ACL rights. Operators own placement,
ownership, groups or principals, ACLs, and (on POSIX) the umask. For
sensitive contents, use a directory restricted to the intended users.

**PostgreSQL.** Database grants decide who can read and write queues. The
database must already exist: SimpleBroker initialization creates its managed
schema and tables inside it, and the TaskMonitor creates and migrates only
its own Monitor tables in an initialized broker database ([SB-0.4a]). The
role Weft connects as needs the privileges those objects require. Every role
that can write the broker schema shares one trust domain with every Weft
process that uses it. Prefer `WEFT_BACKEND_PASSWORD`, or the driver's own
convention such as libpq's `PGPASSWORD`, over a password embedded in
`.weft/broker.toml`. Weft shows targets through SimpleBroker's redacted
display form. It strips a URL-userinfo password from the context keys and
runtime identities it derives ([MF-1]); a password given as a libpq
`password=` keyword or in a URI query stays in those derived identities.
```

Checks on this block (all from the Execution log): `PROJECT_SCOPE` defaults
to `True`. `WEFT_DIRECTORY_NAME` rejects `/`, `\`, `.`, and `..`
(`weft/_constants.py::_parse_weft_directory_name`). Process-title segments
are filtered by `[^a-zA-Z0-9_-]`
(`weft/core/tasks/base.py::_sanitize_process_title_segment`). Weft has no
queue-name validator of its own (`grep -rn "QueueNameError|validate_queue_name" weft/`
returns no lines), so the queue-name clause credits SimpleBroker. Every
TaskSpec model sets `extra="forbid"`. `WEFT_BACKEND_PASSWORD` loads as
`BACKEND_PASSWORD` through `load_config()`. Codes [TS-1], [TS-1.2], [TS-1.3],
[QUEUE.2a], [OBS.4], [SB-0.2], [SB-0.4], [SB-0.4a], [RM-0], [CC-3.1], [MF-1],
[CTX.5] resolve in `docs/specifications/`. The file-mode clause follows
[CTX.5] (`docs/specifications/07-System_Invariants.md`), which says in-place
writes retain existing permissions, so the clause does not say Weft never
preserves them. [WT-2a.11] and [ALT-WT-014] resolve in
`docs/program-theory.md`. The recognition clause follows
`simplebroker/db.py` in the installed 8.7.2: the SQLite admission snapshot
and `_verify_database_magic` raise on a magic mismatch or a newer schema
version, and `_verify_database_magic` returns early when no meta table
exists. Probe E20 confirms that a foreign SQLite database keeps its own
table after SimpleBroker initializes it. The password clause follows
`_strip_backend_target_secret` and `canonical_runtime_context`, which
rebuild only the URL netloc (E21).

### 5.2 Backlink (spec 00)

Insertion point: after the line
"- retired: 2026-07-29-import-boundary-remediation-plan — source 396c3b85; see docs/plans/README.md"
under `## Related Documents`. Spec 00 has no `## Plans` or
`## Related Plans` section. Its plan history is kept in `## Related Documents`,
so the backlink goes there.

Replacement (`docs/specifications/00-Overview_and_Architecture.md`):
```markdown
- retired: 2026-07-29-import-boundary-remediation-plan — source 396c3b85; see docs/plans/README.md
- [Docs and the broker boundary](../plans/2026-11-02-docs-and-broker-boundary-plan.md)
```

### 5.3 Mapping repairs (hygiene)

Each repair replaces a pointer to a file or symbol that no longer exists
with the current owner. Evidence is in §9 and the Execution log.

01: replaces the paragraph that opens "_Implementation mapping_:
`weft/commands/specs.py` owns the structured," and closes "own runner and
agent-runtime probes." The only change is the rendering owner. `weft/cli/validate_taskspec.py`
was deleted in `0ccb3e1f`. Rendering and exit codes now live in
`weft/cli/app.py::spec_validate` and `weft/cli/app.py::_render_spec_validation`.

Replacement (`docs/specifications/01-Core_Components.md`):
```markdown
_Implementation mapping_: `weft/commands/specs.py` owns the structured,
ordered validation capability; `weft/cli/app.py` (`spec_validate`,
`_render_spec_validation`) owns terminal rendering and exit-code adaptation;
`weft/core/runner_validation.py` and `weft/core/agents/validation.py` own
runner and agent-runtime probes.
```

09: replaces the three lines from "- `weft/commands/specs.py` owns
stored-spec management and structured" through "rendering and exit-code
adaptation; `weft/core/spec_store.py` owns the". The bullet continues
unchanged from "  shared `NAME|PATH` resolution logic;".

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
- `weft/commands/specs.py` owns stored-spec management and structured
  validation; `weft/cli/app.py` (`spec_validate`, `_render_spec_validation`)
  owns TaskSpec validation rendering and exit-code adaptation;
  `weft/core/spec_store.py` owns the
```

11: replaces the `spec` table row that opens "| `spec` | `weft/commands/specs.py`, `weft/cli/validate_taskspec.py` |".

Replacement (`docs/specifications/11-CLI_Architecture_Crosswalk.md`):
```markdown
| `spec` | `weft/commands/specs.py`, `weft/cli/app.py` | stored spec management, resolution, builtin task-spec discovery, structured TaskSpec validation, and CLI rendering |
```

04 (file-level mapping): replaces the three lines from "_Implementation
mapping_: `weft/context.py`, `weft/commands/queue.py`," through
"`weft/commands/interactive.py`, `weft/_constants.py`, `weft/bootstrap.py`,".
`weft/commands/interactive.py` was deleted in `6f4bcd7d`. Its run-session
queue use is in `weft/commands/run.py` (`_LiveRunSession` builds its input
queue through `context.queue(...)`).

Replacement (`docs/specifications/04-SimpleBroker_Integration.md`):
```markdown
_Implementation mapping_: `weft/context.py`, `weft/commands/queue.py`,
`weft/commands/init.py`, `weft/commands/dump.py`, `weft/commands/load.py`,
`weft/commands/run.py`, `weft/_constants.py`, `weft/bootstrap.py`,
```

04 (configuration mapping): replaces the three lines from
"`weft/manager_process.py` (main, run_manager_process); `weft/commands/init.py`;"
through "`weft/core/manager.py`; `weft/core/pipelines.py`; `weft/core/queue_wait.py`;".

Replacement (`docs/specifications/04-SimpleBroker_Integration.md`):
```markdown
`weft/manager_process.py` (main, run_manager_process); `weft/commands/init.py`;
`weft/commands/run.py`;
`weft/core/manager.py`; `weft/core/pipelines.py`; `weft/core/queue_wait.py`;
```

04 (context-construction rule): replaces the bullet that opens "- direct
`Queue(...)` construction in command-layer code is reserved for" and closes
"interactive queue client that owns its own task-local inbox lifecycle". The
rule stays. Only its example is gone, because the interactive client it named
now uses `WeftContext.queue()`. A grep for `Queue(` construction in
`weft/commands/` and `weft/cli/` returns no lines.

Replacement (`docs/specifications/04-SimpleBroker_Integration.md`):
```markdown
- direct `Queue(...)` construction in command-layer code is reserved for
  explicit low-level edges that do not carry a `WeftContext`
```

05: replaces the three lines from "`weft/commands/_spawn_submission.py`
static reconciliation queue inventory," through "recognize every
source/reserved pair;". `_find_reserved_spawn_request_queue` was removed
(last present before `edb66f4a`). The remaining owner is named.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
`weft/commands/_spawn_submission.py` static reconciliation queue inventory
and `weft/commands/_spawn_submission.py::_reserved_spawn_request_queue_names` recognize every source/reserved pair;
```

10: replaces the three lines from "process projection is owned by" through
"`weft/cli/app.py::_task_status_plain_lines()`. The command and
Python-client". `_task_status_process_fields` was introduced in `842ed36f`
and removed in `f57b5633`. The
process fields are now added in `weft/cli/app.py::task_status` and rendered
by `_task_status_plain_lines(..., include_process=...)`.

Replacement (`docs/specifications/10-CLI_Interface.md`):
```markdown
process projection is owned by
`weft/cli/app.py::task_status()` and
`weft/cli/app.py::_task_status_plain_lines()`. The command and Python-client
```

## 6. Tasks

0. Index and comprehension.
   - Outcome: the plan has its `docs/plans/README.md` Status Index row
     (`draft`, title as above, `none`). Comprehension answers are in the
     Execution log.
   - Files to touch: `docs/plans/README.md` (one row, top of the index table,
     same shape as the row for
     `2026-11-02-program-theory-reconciliation-plan.md`).
   - Done when: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
     passes and `bin/check-doc-paths` prints `OK`. The row already exists
     and both gates pass (§7), so only the comprehension answers remain.

1. Promote the security account and backlink (§5.1, §5.2).
   - Outcome: spec 00 contains both blocks verbatim.
   - Files to touch: `docs/specifications/00-Overview_and_Architecture.md`.
   - Constraints: insert only. Do not reword the existing section. Do not
     add an `_Implementation mapping_` line. Do not backtick the
     SimpleBroker guide path.
   - Stop if: a reviewer asks for a clause the tree does not support (for
     example, that stored-spec names are shape-checked, or that Weft
     validates queue names itself). Record a deviation row instead.
   - Done when: `grep -c "### Trust Domain" docs/specifications/00-Overview_and_Architecture.md`
     prints `1`, and the Task 3 gates pass.

2. Apply the mapping repairs (§5.3).
   - Outcome: every backticked `weft/…py` path and `path::symbol` reference
     in `docs/specifications/` resolves.
   - Files to touch: the six files in §5.3.
   - Constraints: replace exactly the quoted spans. No other wording edits
     in those paragraphs.
   - Done when: the two resolution commands in the Execution log (E9, E10)
     print no `MISSING` or `DEAD` lines, and
     `grep -c "interactive queue client" docs/specifications/04-SimpleBroker_Integration.md`
     prints `0`. The grep is needed because the 04 context-construction
     Replacement block is a prefix of the unrepaired bullet, so containment
     passes whether or not that repair is applied (Execution log E22).

3. Promote the plan status, record the promotion baseline, and run the gates.
   - Outcome: `Status: promoted` in this file and `promoted` in its
     `docs/plans/README.md` Status Index row, set in one edit before any §7
     command runs. `Promotion baseline identifier:` in `## Spec Baseline`
     holds the promotion commit SHA (or diff base plus worktree state if the
     owner reviews it uncommitted). Gates are green.
   - Why the order matters: `bin/check-doc-paths`
     (`_plan_replacement_sync_failures`) skips a plan whose status is
     `draft`, `completed`, or `superseded` before it looks at the promotion
     baseline. While the index row says `draft`, the Replacement containment
     check never runs on this plan, and a passing gate proves nothing about
     §5.
   - Commands: §7.
   - Done when: the §7 commands succeed with the plan at `promoted`, and the
     results are appended to the Execution log. The plan stays `promoted`
     until Task 5.

4. Record the owner's disposition of the SimpleBroker request candidates
   (§8, R1 and R2). Ruled 2026-11-03: neither is sent. R1: Weft decodes no
   timestamp component after plan 12 (the TID is opaque everywhere except
   the short display form, which plan 12 encodes and decodes as a whole
   integer). R2: the eight watcher overrides stay as they are. Nothing in
   this repository changes beyond this record. No Weft plan or release waits
   on SimpleBroker.
   - Done when: §8 carries the rulings (it does, as of this edit).

5. Close.
   - Precondition: Task 3's gates passed while the plan was `promoted`.
   - Class-5 completion requirements ([WT-7] in `docs/program-theory.md`):
     - Keyed possession probe. The owner writes the key first: one
       hypothetical feature and where it belongs, one category error and the
       entry that refuses it, and the class of defect an audit of a named
       subsystem should find. An agent that did not author this plan, from a
       different family where one is available, answers from
       `docs/program-theory.md` alone and is graded against the key. The key
       and the result go in the Review Log, and the drawn key items are
       spent. On FAIL, classify after disposition (document deficiency, key
       fault, or agent reasoning); only a document deficiency changes the
       theory, and that change is not this plan's. Task 0's comprehension
       answers do not satisfy this requirement.
     - Theory-versus-code audit refresh. Carry the audit table in
       `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` forward
       by reference, and re-audit only the rows this plan touches ([WT-2a.8]
       and [WT-2a.11]). Record the result in the Review Log.
   - Outcome: `Status: completed` in this file and `completed` in the index
     row, in the same change. The Review Log holds the review dispositions,
     the probe key and result, and the audit refresh.

## 7. Testing Plan and Verification

This plan changes prose only, so there is no red-green test. The proof is the
repository's doc gates plus the two resolution commands (E9, E10). Do not add
a test for the security prose. It describes scoping and absence, and nothing
can fire it ([WT-2a.11] evidence note in the reconciliation plan).

## Verification

Status sequencing: set `Status: promoted` in this file and `promoted` in
the `docs/plans/README.md` index row before running these commands (Task 3),
and flip both to `completed` only after they pass (Task 5).
`bin/check-doc-paths` skips `draft` plans before it reads the promotion
baseline, so with the plan at `draft` the Replacement containment check does
not run, and its `OK` does not cover §5. With the plan at `promoted`, a
Replacement block missing from its target fails the gate.

Run from the repository root:

```bash
. ./.envrc
bin/check-doc-paths
./.venv/bin/python -m pytest tests/specs -q
```

Success: `check-doc-paths: OK (...)` and exit 0. The `pytest` command exits
0 (the repository's addopts suppress the summary line; at baseline
`./.venv/bin/python -m pytest tests/specs -p no:cacheprovider -o addopts="" -q`
prints `164 passed`). After Task 2, re-run E9 and E10 from the Execution log and the Task 2
grep for "interactive queue client" (containment cannot see that repair).

Known red state: none. The `draft` Status Index row now exists in
`docs/plans/README.md` (added with the series index update after authoring),
so `bin/check-doc-paths` prints `OK` and `tests/specs/test_plan_metadata.py`
passes (checked at fresh-eyes review, 2026-11-02). E19 records the earlier
unindexed state.

## Rollback

All changes are documentation. Revert the spec-promotion commit with
`git revert <promotion-sha>`. Nothing depends on the new text (no code, test,
or gate reads it), so reverting it breaks nothing. If promotion is still
uncommitted, revert only this plan's hunks with Edit. Never run
`git checkout` on shared files.

## 8. Broker Boundary Findings ([WT-2a.8])

Method: read every Weft site that touches queue mechanics SimpleBroker owns
(message identity, ordering, atomic moves, delivery claims, retries,
watcher lifecycle, connection attribution). Compare it with the installed
SimpleBroker 8.7.2. The installed `watcher.py` is identical to
`../simplebroker/simplebroker/watcher.py`, and the installed `simplebroker_pg`
4.7.2 is identical to `../simplebroker/extensions/simplebroker_pg/simplebroker_pg`
(`diff -q` and `diff -rq -x __pycache__`, no output). Commands and counts
are in the Execution log.

| # | Observation | Holds? | Path | Disposition |
|---|-------------|--------|------|-------------|
| B1 | Weft decodes the hybrid-timestamp bit layout itself (`value >> 12`, `value & 0xFFF`) | holds | `weft/helpers/__init__.py:144-145` (`tid_short_form`) | Not fixed here. Cleanup plan 12 retires `tid_short_form` and [OBS.5] (reconciliation plan §9 fork 2 (b)). Request candidate R1 was whether, if plan 12's lossless rendering still needed the components, to ask SimpleBroker to export its decoder (which exists privately as `decode_hybrid_timestamp` in `../simplebroker/simplebroker/_timestamp.py:69` and is absent from `simplebroker.ext.__all__`). Owner, 2026-11-03: no request. "We don't need to decode the timestamp anymore. We encode and decode, all we need is the TID"; "Weft only encodes and decodes the *short* display form. Everywhere else it uses the TID." Plan 12's display form is a bijective encoding of the whole 64-bit TID (its §5: a 13-character base-32 string that "carries the low 12 counter bits in full"), so no Weft code reads the physical or counter component after plan 12 lands; this B1 site is the last such reader and it leaves with `tid_short_form`. |
| B2 | Two Weft retry loops around broker writes: the completion-fact broadcast and terminal envelope (`TERMINAL_EVENT_WRITE_RETRIES` = 3, 0.2 s pause), and the child-inbox seed (3 attempts, linear 0.05 s base) | holds as fact; not a layer violation | `weft/core/tasks/base.py:1972-1989`, `weft/core/manager.py:4493-4513`, `weft/core/manager.py:2096-2108` | Already aligned. SimpleBroker owns contention retry: `write` and `broadcast` run under `_run_with_retry` with a 30 s idle window (`OPERATION_RETRY_MAX_ELAPSED = 30.0`, `simplebroker/db.py:216`) and retry lock and busy errors. Weft's loops catch only what escapes that policy (`BrokerError`, `OSError`, `RuntimeError`). Their purpose is a Weft durability rule: a completion fact or a child's first work item is not dropped on one failure. No push-down. The constant docstring at `weft/_constants.py:682-691` gives "a transient lock" as its example, which SimpleBroker has already retried by then. That wording is a one-line code-comment fix and belongs to a later code plan, not this one. |
| B3 | `MultiQueueWatcher` overrides eight underscore-named `BaseWatcher` methods (excluding `__init__`): `_sigint_handler`, `_create_activity_waiter`, `_start_strategy`, `_on_data_version_change`, `_cleanup_runtime_resources`, `_cleanup_stop_resources`, `_cleanup_run_resources`, `_drain_queue`. The set is derived by intersecting the two classes' method names (E7), not searched by name. | holds | `weft/core/tasks/multiqueue_watcher.py:844, 963, 969, 977, 1056, 1123, 1128, 1568` | Request candidate R2 was to ask SimpleBroker to declare the supported subclass hooks (names and contracts) in [SB-API-6], which "owns the public types used to run and subclass watchers" but names no hooks: of the eight, `_drain_queue` ("must be implemented by subclasses") and `_sigint_handler` ("can be overridden by subclasses") are declared by their base docstrings, `_create_activity_waiter` is the override the Python guide recommends for a custom native waiter (`../simplebroker/docs/guides/python.md:807`), and the five lifecycle steps (`_start_strategy`, `_on_data_version_change`, `_cleanup_runtime_resources`, `_cleanup_stop_resources`, `_cleanup_run_resources`) carry no statement either way. Owner, 2026-11-03, after seeing that inventory: "Leave these overrides as-is." No request is sent; Weft keeps the eight overrides, and a SimpleBroker change to any of them is caught by Weft's reactor tests at upgrade time, which the owner accepts. This plan does not gate on anything. |
| B4 | Reconciliation audit item 6 (4.3-b): does the native idle poll make a quiet task run a turn? | verified: no turn | `weft/core/tasks/multiqueue_watcher.py::_activity_hint_action`, `::_wait_for_activity_body` | Aligned. The idle-poll return carries no native, local, or data-version hint. With a native waiter and no deadline reached, Weft runs `_has_pending_messages()` and returns `"continue"` unless a watched queue has pending work. Native and data-version hints also end in `"continue"` unless work is pending; a consumed local hint always returns. So a quiet task's wait returns only on pending work, a local hint, an observation, a deadline, or stop. Probe E8: one waiter, one quiet queue, `wait_for_activity(timeout=5)` returned at 5.01 s, with 196 native waits and 3 or 4 pending probes per run. The cost of quiet is at most one `has_pending()` per eligible delivery queue per SimpleBroker idle-poll pass: exactly one each on an empty, uninterrupted pass, fewer when `any()` stops at a queue with pending work; notification-only observation interests have their own checks and are not in this count. The idle-poll deadline has a base of `max(1.0, 10 × max_interval)`, jittered uniformly to between one and two times the base, so 1 to 2 s at the default `max_interval` 0.1 (`PollingStrategy._next_native_idle_poll_deadline`). The deadline is checked after a native wait finishes, so it is a scheduling target, not a strict wall-clock bound on the return. That is a read, not a turn, and SimpleBroker owns the cadence. The probe used a stand-in native waiter on SQLite. The decision path is backend-independent. The PostgreSQL waiter belongs to SimpleBroker, and its `PostgresMultiQueueActivityWaiter.wait` returns `False` when its timeout passes with no notification (`../simplebroker/extensions/simplebroker_pg/simplebroker_pg/runner.py`), which is the idle-poll case the probe stands in for. A run against real PostgreSQL was not done and is not needed to settle the question. |
| B5 | Backend coupling | does not hold | `weft/` | Aligned. No `sqlite3` or `psycopg` imports, and no `simplebroker._*` or `simplebroker_pg._*` imports (0 lines each). The only SQL is in `weft/core/monitor/sql.py`, over Monitor-owned tables per [SB-0.4a]. The one other keyword hit is a comment at `weft/core/tasks/multiqueue_watcher.py:1224`. |
| B6 | Reservation and move semantics | does not hold | 13 `move*` call sites (Execution log E5) | Aligned. Every reservation, requeue, and recovery move calls SimpleBroker `move_one`, `move_many`, `move`, or `move_generator`. No read-then-write move exists. `_apply_spawn_reserved_policy` uses `reserved.move_one(...)` (`weft/core/manager.py:5424`). |
| B7 | Message identity and ordering | does not hold | `weft/core/spawn_requests.py:228, 246-254`; `weft/core/pruning/retention.py:465, 506` | Aligned. SimpleBroker mints TIDs (`db.generate_timestamp()`) and writes them through the public exact-ID `insert_messages` (`../simplebroker/docs/specs/13-message-identity.md`). Retention sorts rows it has grouped by `message_id`. That uses SimpleBroker's order and defines no order of its own. |
| B8 | Connection attribution | does not hold | `weft/core/runtime_identity.py:230-233`, `weft/core/manager.py:2926-2928` | Aligned. Weft binds labels and reads usage only through the public `simplebroker_pg.bind_connection_owner` and `get_connection_usage` ([SB-0.4]). No `application_name` or `pg_stat_activity` use exists in `weft/`. |

Summary: B1 and B3 hold as push-down candidates, and each becomes a
SimpleBroker request candidate (R1 conditional on plan 12, R2
unconditional). B2 holds as a fact but is a Weft-owned policy, so it is
aligned. B4, the audit's unverified item, is verified aligned. B5 to B8 do
not hold. Owner disposition of R1 and R2 (2026-11-03): neither request is sent (see the B1 and B3 cells).

## 9. Docs Hygiene Findings

Re-derived from the tree at `b7eed9f9`. The sweep it rechecks was run at
`ec50bd57` and is not cited.

| # | Finding | Holds? | Evidence | Action |
|---|---------|--------|----------|--------|
| H1 | `weft/cli/validate_taskspec.py` cited by three specs | holds | file absent; deleted in `0ccb3e1f` | §5.3 repairs 01, 09, 11 |
| H2 | `weft/commands/interactive.py` cited twice in spec 04 | holds | file absent; deleted in `6f4bcd7d` | §5.3 repairs 04 lines 11 and 725 |
| H3 | Spec 04's `Queue(...)` rule names the interactive queue client as its example edge | holds | `weft/commands/run.py:372` builds the queue through `context.queue(...)`; no `Queue(` construction in `weft/commands/` or `weft/cli/` | §5.3 drops the example |
| H4 | `_spawn_submission.py::_find_reserved_spawn_request_queue` in spec 05 | holds | symbol absent from `weft/` | §5.3 repairs 05 |
| H5 | `app.py::_task_status_process_fields()` in spec 10 [CLI-1.2.3] | holds | symbol absent; process fields are added in `task_status` | §5.3 repairs 10 |
| H6 | README CLI claims lag the CLI | does not hold | the 4 top-level commands README invokes directly (`init`, `status`, `result`, `run`) and all 35 group subcommands it names exist in `--help`. Of 119 README invocations parsed, 7 flag tokens are not in `--help`, and all are legitimate: the `--` separator (2) and the `dockerized-agent` arguments `--provider` (a parameterization argument) and `--prompt`, `--document` (run-input arguments) (5), declared in `weft/builtins/tasks/dockerized-agent/taskspec.json`. The exit-code table matches `weft/_constants.py` and [IMPL.1]. | none |
| H7 | Agent-context index stale | does not hold | `docs/agent-context/context.index.yaml` has `updated_at: 2026-11-02`, its read order matches `CLAUDE.md`, and it lists all 8 runbooks, each also named in `docs/agent-context/README.md` | none |
| H8 | Code cites spec codes that do not exist (`[CLI-1]`, `[SB-API-6]`) | does not hold as stale docs | `[SB-API-6]` is a SimpleBroker code, labeled as SimpleBroker's at `weft/core/tasks/multiqueue_watcher.py:613`. `[CLI-1]` in `weft/commands/types.py:5` is a family prefix with no heading of its own. It is a code docstring, so it is out of scope (§10). | none here |

## 10. Out of Scope

- Code changes of any kind, including the B2 constant docstring and the
  `[CLI-1]` docstring prefix (H8).
- `weft/core/tasks/consumer.py:1328` `_purge_stream_markers` peeks a fixed
  `limit=256` on append-only `outbox` and `ctrl_out`. That is a read-pattern
  question, not a layer violation. No plan in this series owns it; the owner
  routed it on 2026-11-03 to a later code plan ("Later plan"), and this plan
  records it as a one-line finding for that plan.
- Theory edits. `[ALT-WT-015]`'s old file pointer, flagged at `ec50bd57`,
  is no longer in `docs/program-theory.md`.
- Opening SimpleBroker work (owner decision, Task 4: none opened).
- Extending `bin/check-doc-paths` to cover `weft/` paths. The resolution
  commands E9 and E10 are this plan's proof. A gate change would be process
  work (+P).

Premise corrections (the brief's premise versus the tree):

- SimpleBroker's configuration guide has no PostgreSQL grant note. The
  adapted PostgreSQL paragraph rests on
  `../simplebroker/extensions/simplebroker_pg/README.md` and on [SB-0.4a].
- The `.broker.toml` anchor in Weft is `.weft/broker.toml`, and project
  scoping is on by default (`PROJECT_SCOPE` default `True`), not only "when
  project scoping is on".
- "Checked for shape: derived file names" holds only for
  `WEFT_DIRECTORY_NAME` and process-title segments. Stored-spec names are
  joined into paths unchecked (`weft/core/spec_store.py::_spec_path`). They
  sit under trusted placement because `NAME|PATH` is operator-chosen. Queue
  names are checked by SimpleBroker, not by Weft. The delta says exactly
  this.
- "Trusted by placement / checked for shape / intent never inspected" is
  the program theory's summary of SimpleBroker's model ([WT-5]). It is not
  SimpleBroker's wording.
- Plan 10 has no drift row in the reconciliation audit. Its start authority
  is the owner's fork 2 (a) direction.

## Owner Decisions

Every fork this plan raises for the owner. None is decided here.

1. R1 (§8 B1, Task 4): whether to ask SimpleBroker to export its hybrid
   timestamp decoder. CLOSED (owner, 2026-11-03): no request. "We don't
   need to decode the timestamp anymore. We encode and decode, all we need
   is the TID." and "Weft only encodes and decodes the *short* display form.
   Everywhere else it uses the TID." The TID is opaque throughout Weft; plan
   12's short display form encodes the whole integer.
2. R2 (§8 B3, Task 4): whether to ask SimpleBroker to declare its supported
   watcher subclass hooks in [SB-API-6]. CLOSED (owner, 2026-11-03, after
   the eight were listed with their base-class docstrings): "Leave these
   overrides as-is." No request is sent.
3. Routing of the `_purge_stream_markers` fixed-limit peek observation
   (§10). CLOSED (owner, 2026-11-03): "Later plan." Recorded as a one-line
   finding for a later code plan; not opened here.

No Weft plan or release waited on any of these, and none remains open.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 9 corrections — quiet-wait probe count is per wait-activity queue and the idle poll is jittered 1 to 2 s; local hints always return (B4); PostgreSQL waiter timeout evidence and full-file identity of installed SimpleBroker added (§8, B4); file-mode clause aligned with [CTX.5] (in-place writes retain permissions); "message content is not validated" narrowed to intent; E9/E10 re-run moved after Task 2; stale unindexed red state, Task 0 gate text, and E19 updated (index row now exists); H6 `--provider` is a parameterization argument; owner forks gathered in one Owner Decisions section; cross-family round 1: recorded in the next bullet
- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED. F1 (SQLite admission overstated), F2 (password-stripping claim narrowed to URL userinfo), F3 (promote before the containment check runs), F4 (eight watcher overrides, derived inventory), F5 (evidence metadata), F6 ([WT-7] closeout): all P2, accepted and applied. Round 2 (codex 0.161.0, 2026-11-02, scoped to the applied dispositions of F1–F4, F5–F6 confirmed): no blocker; all six closed with live evidence (installed `simplebroker/db.py` magic and schema-version checks; `weft/context.py:216–238` and `weft/core/runtime_identity.py:53–77` strip URL-userinfo passwords only; `bin/check-doc-paths:342–362` skips `draft` before promotion detection, and a promoted overlay reports nine unapplied blocks; the derived override intersection yields exactly eight). Review loop closed; the plan waits on the owner's R1, R2 and routing decisions.

- owner rulings applied 2026-11-03: R1 closed (no decoder request; Weft decodes no timestamp component, the TID is opaque everywhere but plan 12's whole-integer short display form), R2 closed (the eight watcher overrides stay as they are; no hook-declaration request), the stream-marker observation routed to a later code plan. Nothing in the plan's tasks changes beyond recording the rulings; no review round is needed for record-only edits.

- Theory-versus-code audit refresh (Task 5, 2026-11-03, at `da957a29` plus
  this promotion): the audit table in
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` ("### Audit
  table") is carried forward by reference; only its two rows this plan
  touches were re-audited. I-7 → [WT-2a.8]: still `aligned`; its evidence
  holds (`weft/core/queue_wait.py` imports and calls
  `simplebroker.create_activity_waiter_for_queues`; `weft/core/runtime_identity.py`
  binds the connection owner through `simplebroker_pg.bind_connection_owner`),
  and §8 B1 to B8 add the wider sweep (B1 leaves with plan 12; B3 kept by
  owner ruling). I-8 → [WT-2a.11]: still `aligned`, now with a spec
  statement: spec 00 "Observability and Security" carries the Trust Domain
  and Shared Access text (§5.1); `weft/commands/submission.py` lines 975–976
  still raise `NotImplementedError` for `shell=True`; a word-bounded grep for
  `chmod`, `st_mode`, `S_IMODE`, `umask` over `weft/`, `extensions/*/weft_*`,
  and `integrations/weft_django/weft_django` returns 0 lines. No row changes
  classification. This refresh was done by the implementing agent and is a
  re-audit, not a probe.
- Keyed possession probe (Task 5, [WT-7]): NOT RUN. It needs an
  owner-written key first (one hypothetical feature and where it belongs,
  one category error and the entry that refuses it, the defect class an
  audit of a named subsystem should find), then a non-author agent of a
  different family answering from `docs/program-theory.md` alone. No key was
  supplied with the 2026-11-03 implement instruction, and an implementer may
  not write or grade it ("A self-graded probe is not a probe"). The plan
  therefore stays `promoted`; it moves to `completed` in the change that
  records the key and the graded result here.

Reviewer inputs when run: this plan including §5, the seven target spec
files at `b7eed9f9`, `docs/program-theory.md` [WT-2a.8], [WT-2a.11], [WT-5],
[ALT-WT-014], and the two SimpleBroker sources named in §2.

## Execution log

Authoring evidence, `b7eed9f9`, 2026-11-02. Commands run from the
repository root unless a path says otherwise.

- E1 `git log --oneline -1` → `b7eed9f9 Adopt the program theory with
  fourteen identity invariants and the hub's directional rules`. SimpleBroker
  working tree HEAD `fa95520`. Installed `simplebroker` 8.7.2.
- E2 `grep -rnE "^\s*(import|from) (sqlite3|psycopg)" weft/ | wc -l` → 0.
  `grep -rnE "from simplebroker\._|import simplebroker\._|simplebroker_pg\._" weft/ | wc -l`
  → 0.
- E3 `grep -rlE "\b(SELECT|INSERT INTO|DELETE FROM|UPDATE [a-z_]+ SET|PRAGMA)\b" weft/`
  → `weft/core/tasks/multiqueue_watcher.py` (comment at line 1224) and
  `weft/core/monitor/sql.py`.
- E4 `grep -rnE ">> ?12|& ?0xFFF|0xfff" weft/` → `weft/helpers/__init__.py:144`,
  `:145`. `./.venv/bin/python -c "import simplebroker; print(hasattr(simplebroker,'decode_hybrid_timestamp'))"`
  → `False`. `grep -n "def decode_hybrid_timestamp" ../simplebroker/simplebroker/_timestamp.py`
  → line 69.
- E5 `grep -rnE "\.move(_one|_many|_generator)?\(" weft/ | wc -l` → 13.
- E6 `grep -rnE "(^|[^A-Za-z_.])Queue\(" weft/` → 6 lines in 4 files
  (`weft/context.py`, `weft/core/spawn_requests.py`,
  `weft/core/tasks/base.py`, `weft/core/tasks/multiqueue_watcher.py` ×3).
  None are in `weft/commands/` or `weft/cli/`.
- E7 Derived override inventory (compares method names; searches no
  predetermined list):

  ```bash
  ./.venv/bin/python -I -c "
  import inspect, sys; sys.path.insert(0, '.')
  from simplebroker.watcher import BaseWatcher
  from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher
  base = {n for n, _ in inspect.getmembers(BaseWatcher, inspect.isfunction)}
  print(sorted(n for n in base & set(vars(MultiQueueWatcher)) if n.startswith('_') and not n.startswith('__')))"
  ```

  → `['_cleanup_run_resources', '_cleanup_runtime_resources',
  '_cleanup_stop_resources', '_create_activity_waiter', '_drain_queue',
  '_on_data_version_change', '_sigint_handler', '_start_strategy']` (eight;
  the public overlap is `run_forever`, `run_in_thread`, `stop`). Then grep
  `../simplebroker/docs/` for each of the eight names: 4 lines, of which
  `../simplebroker/docs/guides/python.md:807` names `BaseWatcher._create_activity_waiter(queue)`
  as a recommended override, two name `_drain_queue` of
  `examples/reference_reactor.py`, and one is the test name
  `test_create_activity_waiter_for_queues_rejects_distinct_same_repr_options`
  in the [SB-API-2] evidence row of `../simplebroker/docs/specs/16-python-library-api.md`.
  No line comes from [SB-API-6]'s contract text.
- E8 Quiet-wait probe (throwaway script, not committed). It patched
  `weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues` to
  return a waiter whose `wait()` sleeps its timeout and returns `False`. It
  built a `MultiQueueWatcher` over one empty SQLite queue, counted
  `_queue_has_pending` calls, and called `wait_for_activity(timeout=5)`.
  Output: `uses_native=True elapsed=5.01s waiter_calls=196 pending_probes=3
  offsets=[1.5, 3.07, 4.68]`. A 3.5 s run gave 2 probes (1.3, 2.71). A
  fresh-eyes re-run of the same probe gave `elapsed=5.01s waiter_calls=196
  pending_probes=4 offsets=[1.03, 2.1, 3.31, 4.84]`. The probe count varies
  because SimpleBroker jitters the idle-poll deadline.
- E9 Path resolution over backticked `.py` paths in `docs/specifications/`
  (151 distinct paths checked):

  ```bash
  grep -ohE '`(weft|integrations|extensions)/[A-Za-z0-9_./-]+\.py' docs/specifications/*.md \
    | tr -d '`' | sort -u \
    | while read p; do [ -e "$p" ] || echo "MISSING $p"; done
  ```

  Output: `MISSING weft/cli/validate_taskspec.py`,
  `MISSING weft/commands/interactive.py`.
- E10 Symbol resolution over `path::symbol` references (246 distinct
  references checked):

  ```bash
  grep -ohE '`(weft|integrations|extensions)/[A-Za-z0-9_./-]+\.py::[A-Za-z_][A-Za-z0-9_.]*' docs/specifications/*.md \
    | tr -d '`' | sort -u \
    | while read r; do p=${r%%::*}; s=${r##*::}; s=${s##*.}; \
        [ -e "$p" ] && ! grep -qE "^\s*(async )?(def|class) $s\b|^$s\s*[:=]" "$p" && echo "DEAD $r"; done
  ```

  Output: `DEAD weft/cli/app.py::_task_status_process_fields`,
  `DEAD weft/commands/_spawn_submission.py::_find_reserved_spawn_request_queue`.
  Scope: backticked `.py` paths and `path::symbol` forms only. Bare
  directory paths and parenthesized symbol lists are not checked.
- E11 `git log --diff-filter=D --format=%h -- weft/cli/validate_taskspec.py` → `0ccb3e1f`.
  `git log --diff-filter=D --format=%h -- weft/commands/interactive.py` → `6f4bcd7d`.
  `git log --oneline -S"_find_reserved_spawn_request_queue" -- weft` → newest `edb66f4a`.
  `git log --oneline -S"_task_status_process_fields" -- weft` → `f57b5633`
  (newest; removes it) and `842ed36f` (introduces it).
- E12 `grep -rnE "chmod|st_mode|S_IMODE|umask" weft/ extensions/*/weft_* integrations/weft_django/weft_django`
  → no lines. `grep -rn "QueueNameError\|validate_queue_name" weft/` → no lines.
- E13 `WEFT_BACKEND_PASSWORD=<set> ./.venv/bin/python -c "from weft._constants import load_config; ..."`
  → `PROJECT_SCOPE: True`, `DEFAULT_DB_NAME: .weft/broker.db`,
  `PROJECT_CONFIG_PATH: .weft`, `PROJECT_CONFIG_NAME: broker.toml`,
  `BACKEND_PASSWORD` present.
- E14 `COLUMNS=120 ./.venv/bin/weft <group> --help` → task: list status
  ping stop kill tid. queue: read write peek move list exists stats resolve
  watch delete broadcast alias. manager: start serve stop list status. spec:
  create list show delete validate generate. system: tidy task-monitor prune
  dump builtins load. Every `weft <group> <sub>` that README names
  (`grep -oE "weft (task|queue|manager|spec|system) [a-z][a-z-]*" README.md | sort -u`,
  35 pairs) is in this list. A README flag parse over 119 invocations found
  7 tokens not in `--help`, all accounted for in H6.
- E15 Spec codes cited by the delta: `grep -rlF "[<code>]" docs/specifications/`
  resolves [TS-1], [TS-1.2], [TS-1.3], [QUEUE.2a], [OBS.4], [SB-0.2],
  [SB-0.4], [SB-0.4a], [RM-0], [CC-3.1], [MF-1], [CC-2.2.1], [OBS.5],
  [CLI-1.2.3]. `grep -cF` in `docs/program-theory.md`: [WT-2a.8] 2,
  [WT-2a.11] 2, [WT-5] 12, [ALT-WT-014] 2, [ALT-WT-019] 2. [DOM-15] is in
  `docs/agent-context/decision-hierarchy.md` (3 hits). [SB-API-6] is at
  `../simplebroker/docs/specs/16-python-library-api.md:688`.
- E16 Baseline gates: `bin/check-doc-paths` → `check-doc-paths: OK
  (/Users/van/Developer/weft)`, exit 0.
  `./.venv/bin/python -m pytest tests/specs -p no:cacheprovider -o addopts="" -q`
  → `164 passed`.
- E17 Tests that pin the strings being repaired:
  `grep -rn "commands/interactive\|_task_status_process_fields\|_find_reserved_spawn_request_queue\|interactive queue client" tests/ bin/ skills/ AGENTS.md docs/agent-context docs/lessons.md README.md`
  → no lines. (`validate_taskspec` hits in `tests/` are an unrelated runner
  plugin method.)
- Comprehension answers (Task 0, 2026-11-03, before the first spec edit):
  1. Weft does not reject it. TaskSpec validation checks shape only
     (unknown fields rejected, `spec` and `io` frozen); the command a TaskSpec
     names is trusted by placement. The bound is the operating-system
     permissions of the user Weft runs as, or a restricted runner the TaskSpec
     selects (Docker, macOS sandbox, Microsandbox); resource limits bound
     effect, not trust. A content check on the command would be the sanitizer
     [ALT-WT-014] refuses.
  2. No turn. With a native waiter, no hint, and no deadline reached,
     `weft/core/tasks/multiqueue_watcher.py::_activity_hint_action` calls
     `_has_pending_messages()` (an `any()` over eligible delivery queues, so at
     most one `has_pending()` each, exactly one each on an empty pass;
     observation interests are checked separately by
     `_has_pending_observation()`), returns `"continue"` when nothing is
     pending, and `_wait_for_activity_body` keeps waiting. The idle-poll cadence
     (base `max(1.0, 10 × max_interval)`, deadline jittered to one to two times
     the base, checked after a native wait finishes) belongs to SimpleBroker.
     Weft changes nothing. Re-read at `da957a29`: lines 1189–1220 match.
- E18 Promotion dry run (throwaway copy of `docs/specifications/`, not
  committed): every quoted span in §5 occurs exactly once in its target, all
  10 Replacement blocks are contained after substitution, the
  `tests/specs/test_spec_hygiene.py` status-phrase pattern finds no match,
  and E9 and E10 run over the copy print nothing.
- E19 Gates with this file present and unindexed: `bin/check-doc-paths`
  exits 1, listing this plan among the unindexed plans with Replacement
  blocks. `tests/specs` gives 2 failed
  (`test_plan_readme_indexes_every_plan_file`,
  `test_plan_readme_statuses_match_plan_metadata`) and 162 passed. The
  index row was added later; at fresh-eyes review `bin/check-doc-paths`
  prints `OK` and `tests/specs` gives `164 passed`.
- E20 SQLite admission probe (throwaway script under the session
  scratchpad, not committed; `./.venv/bin/python -I`). It created a SQLite
  file holding one unrelated table `app_users` with one row, then wrote a
  message through `simplebroker.Queue` on that file. Output: tables
  `['app_users', 'messages', 'meta', 'queue_aliases']`, `app_users` still
  1 row, `meta` holds `magic = simplebroker-v1` and `schema_version = 6`.
  After the probe rewrote `meta.magic` to `other-v1`, the next `Queue`
  write raised `RuntimeError` ("Database magic string mismatch").
  So SimpleBroker refuses a foreign magic string but initializes a database
  that carries no broker metadata.
- E21 `grep -n "def _strip_backend_target_secret" -A23 weft/context.py`:
  the helper returns the target unchanged when it has no scheme or netloc
  (the libpq keyword form) and rebuilds only the netloc otherwise, keeping
  `parsed.query`. `canonical_runtime_context` in
  `weft/core/runtime_identity.py` follows the same netloc-only pattern.
- E22 Replacement-sync overlay (throwaway copy of the tracked tree under the
  session scratchpad, not committed; `bin/check-doc-paths --root <copy>`).
  With this plan and its index row at `draft`, the gate prints `OK`. With
  both at `promoted` and no spec edited, it reports 9 failures, one per
  unapplied Replacement block, and none for the 04 context-construction
  block, which is a prefix of the unrepaired bullet. At baseline
  `grep -c "interactive queue client" docs/specifications/04-SimpleBroker_Integration.md`
  prints `1`.

Implementation evidence, 2026-11-03, on top of `da957a29`. Commands run from
the repository root after `. ./.envrc`.

- Task 0: the Status Index row already existed (`draft`); comprehension
  answers recorded above before any spec edit.
- Tasks 1 and 2: the ten §5 Replacement blocks were extracted from this file
  by a throwaway script (session scratchpad, not committed) and applied
  verbatim; each quoted old span occurred exactly once in its target.
  `grep -c "### Trust Domain" docs/specifications/00-Overview_and_Architecture.md`
  → `1`. `grep -c "interactive queue client" docs/specifications/04-SimpleBroker_Integration.md`
  → `0`. E9 re-run (149 distinct paths) → no `MISSING` lines. E10 re-run
  (245 distinct references) → no `DEAD` lines. The new pointers resolve:
  `weft/cli/app.py` defines `spec_validate` (line 1158),
  `_render_spec_validation` (1372), `_task_status_plain_lines` (1505), and
  `task_status` (1618); `weft/commands/_spawn_submission.py` defines
  `_reserved_spawn_request_queue_names` (147); `weft/commands/run.py:372`
  builds the run-session input queue through `context.queue(...)`.
- Task 3: `Status: promoted` here and `promoted` in the
  `docs/plans/README.md` row, set in one edit before the §7 commands.
  `bin/check-doc-paths` → `check-doc-paths: OK (/Users/van/Developer/weft)`,
  exit 0. `./.venv/bin/python -m pytest tests/specs -p no:cacheprovider -o addopts="" -q`
  → `164 passed`. Negative overlay (throwaway copy of the tracked tree in the
  session scratchpad, plan and index at `promoted`, spec 00 reset to
  `da957a29`): `bin/check-doc-paths --root <copy>` → 2 failures, the §5.1
  and §5.2 blocks, so the containment check ran on this plan. Full fast
  suite `./.venv/bin/python -m pytest -q -p no:cacheprovider` → exit 0;
  mypy → `Success: no issues found in 481 source files`; `ruff check .` →
  `All checks passed!`; `tests/specs/test_plan_metadata.py` → `4 passed`.
  `ruff format --check .` reports 2 files that would be reformatted, both
  Python blocks inside other plans
  (`docs/plans/2026-11-02-tid-display-encoding-plan.md`,
  `docs/plans/2026-11-03-family-scheduler-frontier-ring-plan.md`), present
  at `da957a29` and not touched here.
- Task 4: §8 and Owner Decisions already carry the 2026-11-03 rulings (R1
  and R2 not sent; stream-marker observation routed to a later code plan).
  No SimpleBroker file and no Weft code changed.
- Task 5: audit refresh done (Review Log); keyed possession probe not run
  (no owner key); status left at `promoted`.
- Evidence correction to E12: the unbounded pattern
  `grep -rnE "chmod|st_mode|S_IMODE|umask"` matches 3 lines in
  `weft/core/manager.py` (7479, 7489, 7533), all the substring `st_mode` in
  `_autostart_manifest_mode`, which was already present at `b7eed9f9`. The
  word-bounded `grep -rnwE` form returns 0 lines. The §5.1 clause it
  supports (Weft never sets or reads permission bits) is unchanged.
- No theory edit is implied by this promotion.
