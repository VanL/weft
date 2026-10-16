# Operator-Owned Filesystem Permissions

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [CTX.5]; docs/specifications/05-Message_Flow_and_State.md [MF-5] Large Output Handling; docs/specifications/10-CLI_Interface.md [CLI-6]
Superseded by: none

Class: 5. Owner-directed filesystem contract change, with hardening for shared
context, command, and task output paths. Plan type: implementation with spec revision.

## Goal

Remove Weft's permission enforcement. Ordinary files and directories use OS and
operator policy, including umask and inherited ACLs. No chmod, mode repair, or
permission preflight is added. The owner explicitly authorized this reversal of
CTX.5 and the corresponding spill/dump policies on 2026-10-15.

## Source Documents

Read: `docs/program-theory.md` [WT-5] (single operator trust domain),
`docs/agent-context/README.md`, `docs/agent-context/decision-hierarchy.md`,
`docs/agent-context/principles.md`, `docs/agent-context/engineering-principles.md`,
`docs/agent-context/lessons.md`, and `docs/lessons.md`.
Relevant runbooks read: runtime-and-context-patterns, writing-plans,
hardening-plans, review-loops-and-agent-bootstrap, testing-patterns under
`docs/agent-context/runbooks/`. Apply `skills/test-audit/SKILL.md` and
`skills/call-agent/SKILL.md`.

Governing behavior: `docs/specifications/07-System_Invariants.md` [CTX.5],
`docs/specifications/05-Message_Flow_and_State.md` [MF-5] Large Output Handling,
`docs/specifications/10-CLI_Interface.md` [CLI-6].

## Spec Baseline

Baseline: `5da1eb8bee2bab3f603838c1bc9ad77f3fdf4c54`.
This plan revises the spec itself. Promotion baseline: spec delta applied against `5da1eb8bee2bab3f603838c1bc9ad77f3fdf4c54`,
identified by the CTX.5/large-output/CLI-6 diff and plan backlinks in the three
listed spec files. Spec hygiene and plan metadata tests verify the promoted surfaces.

## Context and Key Files

- `weft/context.py`: build_context forces metadata directories to 0700 and
  resolution/discovery repairs broker config modes to 0600.
- `weft/commands/init.py`: another broker-config chmod before backend init.
- `weft/helpers/__init__.py`: permission-only helpers wrap mkdir/open/chmod;
  write_file_atomically uses mkstemp, whose restrictive mode survives rename.
- `weft/core/tasks/base.py`: _spill_large_output uses the restrictive helpers.
- `weft/commands/dump.py`: the dump writer uses the restrictive text helper.
- `weft/commands/task_monitor.py`: _write_checkpoint uses NamedTemporaryFile;
  reuse write_file_atomically while preserving checkpoint serialization.
- Tests: `tests/context/test_context.py`, `tests/cli/test_cli_init.py`,
  `tests/commands/test_dump_load.py`, `tests/commands/test_task_monitor.py`,
  `tests/system/test_helpers.py`, `tests/tasks/test_task_execution.py`.
- Documentation: governing specs above, `README.md`, `CHANGELOG.md`,
  `docs/lessons.md`, this plan and `docs/plans/README.md`.

Read callers before deleting helpers. Full-tree rg found their runtime callers
in context, dump and BaseTask, plus direct helper tests. Provider CLI ephemeral
TemporaryDirectory use is an ordinary stdlib temporary workspace, not a durable
artifact mode override, and remains unchanged. Load-command mkdtemp and Docker/microsandbox temporary workspaces are also
transient and out of scope. The PostgreSQL fixture chmod in
`tests/helpers/test_backend.py` is fixture-owned policy. Test executable fixtures and
repo tooling that preserve source modes are outside the runtime policy.

## Invariants and Difference Register

| Boundary | Disposition |
| --- | --- |
| Existing metadata directories and broker configs | Preserve operator permissions; remove tightening, including read-only context discovery |
| Newly created metadata directories, spill files and dumps | Ordinary mkdir/open defaults under OS policy |
| Existing spill and dump files overwritten in place | Preserve their existing mode through ordinary truncation |
| Atomic config/settings/health/checkpoint writes | Preserve atomic replacement; stage with ordinary exclusive creation rather than forced-private tempfile creation |
| Replacement inode metadata | New inode follows current directory policy; do not copy old file mode/ACL or introduce chmod to preserve it |
| Dump payload and spill envelope/hash/placement | Preserve verbatim payload and existing wire/cleanup contracts |
| Files previously restricted by Weft | No chmod or migration; an atomic rewrite installs new-file policy and may broaden access |
| Access failures | Actual filesystem errors follow existing command/task error boundaries; no speculative checks or repairs |

TIDs, task state, reservations, immutable spec/io, spawn semantics, queue naming,
runtime-state dump exclusions and durable task execution stay unchanged. No
new dependency, execution lane, CLI flag, public compatibility alias, or ACL
management. Keep directory ownership separate from access-policy ownership.

Comprehension check (author answers before edits):
1. Does deleting chmod alone remove durable forced modes? No: explicit open/mkdir
   modes and tempfile staging also affect final artifacts.
2. Can atomic rename retain an old inode's ACL without extra policy? No. This
   change keeps replacement semantics and lets new-inode defaults apply.

## Proposed Spec Delta

Promotion strategy A: replace text in existing active specs before implementation,
then synchronize implementation mappings and backlinks in the same slice.

### System Invariants [CTX.5]

Replace the complete CTX.5 bullet, from "Weft-created metadata directories" to
"caller-owned modes.", with:

> - **CTX.5**: Filesystem permissions belong to the OS and operator policy.
>   Weft uses ordinary creation defaults for persistent files and directories,
>   subject to umask and inherited ACLs; it does not chmod, tighten, repair, or
>   preflight their permissions. Context discovery and initialization leave
>   existing directory and broker configuration permissions unchanged. In-place
>   writes retain existing file permissions. Atomic writes replace the inode
>   using umask plus the parent directory's inherited/default ACLs; they do not copy the previous
>   inode's mode or ACL. No migration changes existing permissions; an atomic rewrite of an older
>   restricted file may produce broader access under current operator policy.

Remove ensure_owner_only_dir from the adjacent mapping; add the context resolver,
init command and atomic writer as owners. Default and custom paths share this
rule, replacing the prior protected-parent exception.

### Message Flow [MF-5], Large Output Handling

Replace the mapping sentences "Spill files are written" through
"shared helpers in `weft/helpers/__init__.py`." with:

> Spill files and per-tid directories use ordinary filesystem creation defaults
> under OS and operator policy ([CTX.5]); existing permissions are not changed.

Other mapping owners, envelope fields, hashes, placement and cleanup remain.

### CLI [CLI-6]

Replace the complete dump bullet beginning "`system dump` writes its output
file owner-only" through "treated like secret material." with:

> - `system dump` uses ordinary file creation and truncation under OS and operator
>   policy ([CTX.5]), preserving an existing output file's permissions. Dump
>   content (TaskSpec command arguments and `spec.env` values) is written verbatim
>   and unredacted, so operators control access to dump files as secret material.

Extend the existing checkpoint bullet beginning "task-monitor checkpoints under"
and ending "`result`" with:

>   Checkpoints use atomic replacement with ordinary file creation defaults
>   ([CTX.5]); filesystem errors retain the command's existing failure behavior.

## Rollout and Rollback

Ship code, specs and tests together. No database migration or cleanup occurs.
No migration changes existing modes. Atomic rewrites can replace an older 0600
file with broader access under current umask/default ACLs; document this in README
and changelog. Operators own the policy.
Older processes may still enforce the old policy until restarted. Reverting
this change reinstates tightening on subsequent operations, without reverting
file contents or queue data. No irreversible migration or new deferred work.
Atomic staging remains same-directory, exclusive creation, rename and cleanup;
on FileExistsError raise without retry and never delete a colliding path that
this write did not create. Only replacement PermissionError retains its retry.

## Tasks and Verification

1. Review this plan and exact delta, including author fresh-eyes and a
   different-family review. Stop for an unresolved contract ambiguity.
2. Promote the spec delta and backlinks. Write behavioral regressions first:
   POSIX new creation follows a controlled umask, pre-existing paths keep modes,
   discovery and explicit init preserve broker config permissions, dump/spill
   content remains correct, atomic writes use new-inode defaults and clean up
   failed staging. Isolate umask in subprocesses where changed, not shared
   threaded pytest state. The Consumer spill test remains in-process and compares
   new modes with ordinary sibling creation; its pre-existing directory/file
   case uses 0750/0640 and therefore catches forced 0700/0600 regardless of umask.
   The atomic writer runs with subprocess umasks 0002 (0664 expected) and 0077
   (0600 expected), for text and bytes. Check cleanup by enumerating directory
   entries, not just a staging-name glob. Use real filesystem and broker paths.
   Observe intended
   RED failures before changing runtime code.
3. Replace permission helpers with Path.mkdir/write_bytes/open at their callers;
   delete helpers and their implementation-only tests. Change atomic staging to
   a same-directory UUID sibling named `.{name}_<uuid>.tmp`, opened in exclusive binary mode (xb), with text wrapping inside cleanup scope, preserving retry/cleanup.
   Reuse it for checkpoint JSON with unchanged formatting. Stop if schema,
   task lifecycle, or backend-specific behavior would need alteration.
4. Update README, changelog and a concise owner-correction lesson. Run focused
   tests, formatting, lint and mypy; run the default suite at configured worker
   count. Independent final review checks removed coverage and traceability.
   Close plan and index only after required verification succeeds.

Authoring gate: permission tests protect operator policy, not helper names.
Restoring old tightening or restricted creation must fail them. Existing
payload/lifecycle tests remain the oracle for unrelated behavior. Delete direct
permission-helper tests because their policy and helpers are intentionally
retired; retain atomic correctness/failure/concurrency tests. No test-only
production seam is needed.

Commands (source `. ./.envrc` before repo tools):

- `./.venv/bin/python -m pytest tests/context tests/cli/test_cli_init.py tests/cli/test_cli_init_sqlite_only.py tests/commands/test_dump_load.py tests/commands/test_task_monitor.py tests/system/test_helpers.py tests/tasks/test_task_execution.py tests/specs`
- `./.venv/bin/python -m pytest`
- `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`
- `./.venv/bin/ruff format` with explicit changed Python file list;
  `./.venv/bin/ruff check .`; `git diff --check`
- `bin/check-doc-paths`; `bin/coalesce-check`; spec hygiene and plan metadata
  tests above are the existing repository traceability checks.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Review and Execution Log

- Startup tree clean; coalescing check passed, 9 non-exemplar completed index
  rows and no unindexed plan files before this plan; no sweep authorized.
- Claude CLI probe: liveness returned PROBE-OK; read-only tool allowlist plus
  plan mode refused repo write, probe-write-test.txt absent. Cross-family
  reviewer eligible. Commands: claude -p PROMPT --permission-mode plan
  --allowedTools Read,Grep,Glob, bounded at 120 seconds each.
- Author fresh-eyes: atomic replacement is a distinct inode boundary, so preserve
  atomicity without claiming old inode ACL preservation. Keep native temporary
  workspaces distinct from durable artifact writers. Review pending.

### Plan review dispositions

Claude review: BLOCKED on F1 (contradictory no-widening sentence). Findings below
were checked against the named files. Exact review output retained in the session.

| ID | Disposition | Action |
| --- | --- | --- |
| F1 P2 | accept | Clarify no migration/chmod; atomic rewrites can broaden access under current operator policy. Apply to exact CTX.5 delta, difference register, rollout, README and changelog. |
| F2 P2 | accept | Atomic staging keeps .tmp suffix; strengthen cleanup checks to enumerate directory contents. |
| F3 P2 | modify | Existing Consumer stays real/in-process; new artifacts compared with ordinary siblings, existing modes 0750/0640 differ from forced modes. Controlled umask runs at atomic helper subprocess boundary. |
| F4 P3 | accept | Collision raises before cleanup ownership, no collision retry. |
| F5 P3 | accept | Merge checkpoint sentence into existing CLI-6 checkpoint bullet. |
| F6 P3 | accept | Explicitly exclude transient load/provider/runner workspaces and fixture-owned modes. |
| F7 P3 | reject | A new theory record/skill workflow is outside this bounded change; WT-5 already establishes ownership and CTX.5 plus the owner lesson make the exact correction durable. |
| F8 P3 | accept | Remove init's unused os import. |

- Review round 2: PASS. P3-a accepted: Task 3 now names the hidden .tmp staging
  form explicitly. No other new findings. Round 1 and round 2 exact outputs
  retained in session review files.
- RED: selected permission regressions ran against unchanged baseline runtime
  at default 12-worker parallelism: 14 failed, 3 passed. On inspection, 13
  failures were mode assertions (0600 vs 0640/0644/0664 and 0700 vs
  0750/0755/0775); init's remaining failure was a setup error because default
  SQLite initialization does not create broker.toml. The init fixture now writes
  an explicit broker config when needed; its baseline control was rerun below.
  Passing cases are restrictive-umask controls and failed-sink handling.
- Implementation replaces permission-only helpers with ordinary Path calls and
  uses exclusive ordinary staging for atomic writes. Checkpoint serialization
  remains sorted, non-ASCII JSON plus newline. Added collision ownership and
  encoding-failure cleanup proof for the changed staging phase.

- Corrected init RED control: restoring baseline runtime then running
  test_init_preserves_existing_broker_config_permissions fails on 0600 != 0640;
  candidate bytes restored exactly afterward.
- Staging sensitivity controls: replacing exclusive creation with truncation
  makes the collision test fail with DID NOT RAISE FileExistsError; disabling
  cleanup makes the encoding-failure test fail on the retained .tmp entry.
  Candidate bytes restored exactly after both controls.
- First focused suite: 597 passed, 1 skipped, init setup failure identified above.
  Mypy passed for 476 source files; lint, diff and doc-path/coalescing checks passed.

- Author error-path probe found invalid-codec lookup can create a staging file
  before text open raises. Added the invalid-codec case: RED on retained staging
  entry (1 failed, 1 passed). Fixed by establishing exclusive binary ownership
  first and constructing TextIOWrapper inside the cleanup/close scope.
- Interrupted the first default suite with SIGINT after 1906 passed and 12
  skipped to repair this proved staging bug before acceptance. No concurrency,
  deadlines or test selection were weakened; default suite rerun follows.
- Focused suite before the encoding correction: 598 passed, 1 skipped.

### Completed-work review dispositions

Claude completed-work review: no blocker. Tested code and spec claims against the
live tree and embedded diff; final verification of the encoding correction and
accepted test fixes follows separately.

| ID | Disposition | Action |
| --- | --- | --- |
| F1 P3 | modify | Keep real backend coverage; use workdir/WeftTestHarness ownership for init's setup and cleanup instead of leaking a standalone PostgreSQL schema or forcing SQLite under PG. |
| F2 P3 | accept | Patch helpers_module.uuid with a local SimpleNamespace, never stdlib uuid.uuid4 shared across threads. |
| F3 P4 | accept | Include captured stderr in subprocess failure assertion. |
| F4 P4 | accept | Reflow touched docs and remove extra blank line. |

The owner authorized committing the completed changes on 2026-10-16.

- Default suite on final runtime implementation: 5761 passed, 37 skipped in
  200.70 seconds at configured parallelism. Subsequent changes are accepted
  test-fixture/diagnostic and prose corrections only; targeted reruns below
  verify those corrections on SQLite and PostgreSQL.
- Final runtime helper suite: 54 passed. Final runtime mypy: 476 files clean;
  ruff and doc-path checks passed. Source search found no chmod/fchmod,
  forced 0600/0700 modes or deleted helper names in Weft runtime/current specs.

- Final focused SQLite verification after test-fixture fixes: 599 passed,
  1 PostgreSQL-only skip. Canonical PostgreSQL wrapper ran the affected
  context/init/dump/checkpoint/spill nodes: 13 passed, with automatic container
  cleanup. The init fixture's schema ownership was exercised on both backends.
- Final static checks after fixture fixes: ruff passed; mypy passed for 476
  source files; git diff --check passed. No production changes followed the
  successful default suite.

- Final follow-up review: PASS. Reviewer verified live binary staging,
  TextIOWrapper error cleanup, encoding/newline parity, collision ownership,
  harness-owned init setup, local UUID patch and subprocess diagnostics.
  Optional unused SimpleNamespace import inside the child script removed;
  final atomic and documentation checks rerun after that trivial cleanup.
- Traceability reconciliation: CTX.5, Large Output Handling and CLI-6 contain
  the revised contracts, implementation mappings and reciprocal plan links;
  runtime docstrings cite their governing specs. Plan and index closed together.
- Closure checks after status/index update and child-script cleanup: 60 helper,
  plan-metadata and spec-hygiene tests passed; ruff, format check,
  check-doc-paths, coalesce-check and git diff --check passed.
