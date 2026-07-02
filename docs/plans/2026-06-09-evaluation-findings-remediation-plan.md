# Evaluation Findings Remediation Plan

Status: completed
Source specs: docs/specifications/13-Agent_Runtime.md [AR-0.0], [AR-2.2]; docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/05-Message_Flow_and_State.md (Large Output Handling); docs/specifications/07-System_Invariants.md (Context Invariants); docs/specifications/10-CLI_Interface.md [CLI-6]; docs/specifications/01-Core_Components.md [CC-3.1], [CC-3.2]; docs/specifications/00-Quick_Reference.md (CLI table); style/test-only tasks cite no spec and say so inline
Superseded by: none

## Goal

Fix the defects and gaps found in the 2026-06-09 project evaluation:

1. `spec.agent.tools[].approval_required: true` is silently ignored (a declared
   safety control that does nothing). Make it an explicit validation error.
2. Weft-owned data at rest is created with default umask: `.weft/` dirs are
   0775, `weft system dump` output and large-output spill files are 0644.
   Harden to owner-only (0700 dirs / 0600 files).
3. The macOS sandbox runner passes the full host environment into the
   sandboxed process. Replace with an explicit baseline allowlist plus an
   opt-in `env_passthrough` runner option (parity with the Docker runner's
   explicit env forwarding).
4. Documentation ledger gaps: missing CHANGELOG entry for 0.9.74, eight
   implemented CLI commands missing from the README Command Reference,
   `task ping` missing from the Quick Reference CLI table.
5. House-style drift: four sleep sites using literal intervals instead of
   named `_constants` values, a duplicated raw `"WEFT_CONTEXT"` env read,
   three redundant/unjustified late imports, five `__init__.py` files missing
   `from __future__ import annotations`, and a "no late imports" rule whose
   wording does not match actual (coherent) practice.
6. Two modules with zero test coverage: `weft/commands/diagnostics.py` and
   `weft/core/agents/provider_cli/settings.py`.
7. `weft/ext.py` presents itself as the public extension contract, but
   first-party runner plugins also import `weft.core` internals. State the
   actual stability boundary in the docs.

Each numbered finding is independent. Behavior-affecting work (items 1-3)
comes first; documentation and style cleanups follow; new tests last. Every
task is a separate commit so any single change can be reverted alone.

## Source Documents

Read these before starting (in this order):

- `CLAUDE.md` (repo root): §1.1 design philosophy, §4 house style (imports,
  type hints, constants, docstrings, error handling, testing), §5 dev
  commands, §8 agent boundaries. `AGENTS.md` carries the same content.
- `docs/agent-context/decision-hierarchy.md`: specs outrank plans.
- `docs/agent-context/principles.md` and
  `docs/agent-context/engineering-principles.md`: boundary validation
  ("canonicalize at boundaries, then stay strict"), explicit rejection over
  silent ignoring, real broker/process tests over mocks, traceability rules.
- `docs/agent-context/runbooks/testing-patterns.md`: harness selection.
- `docs/specifications/00-Overview_and_Architecture.md`: the trust model —
  user-level trust, the OS/filesystem is the security boundary. The
  permission hardening in this plan strengthens exactly that boundary.

Governing specs per finding:

- Finding 1: `docs/specifications/13-Agent_Runtime.md` [AR-0.0] ("Weft does
  not own ... approval-policy engines") and [AR-2.2] (agent field semantics —
  the `tools` bullet list). `docs/specifications/02-TaskSpec.md` [TS-1]
  (schema ownership; `AgentToolSection` is implemented in
  `weft/core/taskspec/model.py`). No spec defines `approval_required`
  behavior anywhere — that absence plus [AR-0.0] is why rejection (not
  enforcement) is the correct fix.
- Finding 2: `docs/specifications/07-System_Invariants.md` "Context
  Invariants" (CTX.1-CTX.4); `docs/specifications/05-Message_Flow_and_State.md`
  "Large Output Handling" section (spill);
  `docs/specifications/10-CLI_Interface.md` [CLI-6] (`weft system dump`).
- Finding 3: `docs/specifications/01-Core_Components.md` [CC-3.1], [CC-3.2]
  (runner plugin contract, cited by the plugin's own module docstring).
- Findings 4-7: documentation/tooling/style only. Source spec: none — each
  task says so.

Evaluation evidence (why these are real): verified 2026-06-09 by direct code
reading. `grep -rn "approval_required" weft/` shows the field is defined
(`weft/core/taskspec/model.py`), copied
(`weft/core/agents/tools.py`), and read by nothing else. `ls -la ~/.weft`
shows 0775 dirs; `weft/commands/_dump_support.py` opens its output with bare
`open(path, "w")`; `weft/core/tasks/base.py` `_spill_large_output` uses bare
`write_bytes`. The macOS plugin's `run_with_hooks` does
`env_vars = os.environ.copy()`.

## Current Repo State — Read This First

- The working tree may contain in-flight changes to
  `weft/commands/system.py` and `tests/commands/test_status.py`
  (status-reconciliation work). **Do not revert, commit, or "clean up"
  changes you did not make.** Task 12 touches `weft/commands/system.py`; if
  the file is dirty, make only the edit described there and leave everything
  else exactly as found. If `git status` shows other dirty files when you
  start, stop and ask the repo owner before proceeding.
- Line numbers in this plan were correct on 2026-06-09 and may have drifted.
  Always locate edit points by the quoted code or symbol name, not by line
  number alone.
- Environment setup (required before any verification command):

```bash
cd /Users/van/Developer/weft
. ./.envrc                # or `direnv allow` if you use direnv
uv sync --all-extras
```

  Then always use the repo-managed binaries: `./.venv/bin/python -m pytest`,
  `./.venv/bin/mypy`, `./.venv/bin/ruff`. Do not assume global installs.
- **Execution policy — commits:** implementing this plan AUTHORIZES local
  commits on the current branch (one commit per task, with the messages
  given). It does NOT authorize push, branch creation, tagging, or history
  rewriting. If your execution environment forbids local commits, stop at
  Task 1 and ask rather than accumulating one large uncommitted diff.

## Invariants and Constraints

Things this plan must NOT change:

- **No second execution path.** Nothing here may execute work outside
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- **TaskSpec immutability.** `spec` and `io` stay immutable after creation.
  The Task 2 validator runs at construction time; it does not add mutation.
- **No queue name, state transition, or result payload changes.**
- **Argv-only subprocess discipline.** The sandbox env change (Task 7) alters
  the environment dict only. The command must remain an argv list through
  `subprocess.Popen([...])`; never introduce `shell=True`.
- **Runtime-only `weft.state.*` queues stay excluded from dumps.** Task 6
  changes only the dump file's permissions, never its content or queue
  selection.
- **`weft system dump` content stays verbatim.** Do not wire redaction into
  the dump path: dump/load is a fidelity-preserving backup surface, and
  redacted values would corrupt a restore. The fix is file permissions plus a
  documentation warning.
- **Layering.** `weft/core` must not import `weft/commands`, `weft/cli`, or
  `weft/client`; `weft/commands` must not import `cli`/`client`
  (`tests/architecture/test_import_boundaries.py` enforces this).
  `weft/helpers` must not gain an import of `weft.context` (Task 3 adds a
  `context.py -> helpers` import; a cycle would break everything instantly).
- **Backward compatibility note (one-way doors):** Task 2 makes previously
  accepted (but inert) `approval_required: true` specs fail validation, and
  Task 7 stops passing the full host env into sandboxed processes. Both are
  deliberate contract corrections; both get CHANGELOG entries; rollback for
  either is `git revert` of that single commit. Nothing else in this plan
  changes behavior.

Review gates for the whole plan:

- No new dependencies.
- No new abstractions beyond the three small filesystem helpers in Task 3
  (which exist because three call sites would otherwise duplicate fd-mode
  logic). Do not generalize them further.
- No drive-by refactors. If you see adjacent code you dislike, leave it.
- No mock-heavy tests for filesystem or validation behavior — these are all
  testable with real files in `tmp_path` and real Pydantic models. The ONLY
  sanctioned monkeypatch targets in this plan are: `os.environ` (via
  `monkeypatch.setenv`/`delenv`), and `plugin.subprocess.Popen` /
  `plugin.run_monitored_subprocess` inside the macOS extension test (the true
  external boundary — spawning `sandbox-exec` requires macOS and a real
  sandbox). Mocking anything else is a stop-and-re-evaluate moment.
- Self-driven fresh-eyes review: completed by the plan author 2026-06-09 (see
  "Fresh-Eyes Review Record" at the end).
- External review: requested (see "Independent Review Loop") because Tasks 2,
  3-6, and 7 change runtime behavior at a contract boundary.

## File Map

Files created:

- `tests/commands/test_diagnostics.py` (Task 16)
- `tests/core/test_provider_cli_settings.py` (Task 17)

Files modified (by task number):

- T2: `weft/core/taskspec/model.py`, `tests/taskspec/test_taskspec.py`
- T2b: `docs/specifications/13-Agent_Runtime.md`,
  `docs/specifications/02-TaskSpec.md`
- T3: `weft/helpers/__init__.py`, `tests/system/test_helpers.py`
- T4: `weft/context.py`, `tests/context/` (existing context test file),
  `docs/specifications/07-System_Invariants.md`
- T5: `weft/core/tasks/base.py`, `tests/tasks/test_task_execution.py`
- T6: `weft/commands/_dump_support.py`, `tests/commands/test_dump_load.py`,
  `README.md`, `docs/specifications/10-CLI_Interface.md`
- T7: `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`,
  `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py`,
  `extensions/weft_macos_sandbox/README.md`,
  `extensions/weft_macos_sandbox/pyproject.toml` (version bump),
  `docs/specifications/01-Core_Components.md` (plan backlink)
- T8: `CHANGELOG.md`
- T9: `README.md`
- T10: `docs/specifications/00-Quick_Reference.md`
- T11: `weft/_constants.py`, `weft/commands/tasks.py`,
  `weft/helpers/__init__.py`, `weft/core/launcher.py`
- T12: `weft/_constants.py`, `weft/commands/queue.py`,
  `weft/commands/system.py`
- T13: `weft/core/taskspec/model.py`, `weft/commands/run.py`
- T14: `weft/__init__.py`, `weft/builtins/tasks/__init__.py`,
  `weft/commands/__init__.py`, `weft/core/__init__.py`,
  `weft/core/runners/__init__.py`
- T15: `CLAUDE.md`, `AGENTS.md`
- T17: `tests/conftest.py` (register the new module in `_SHARED_MODULES`)
- T18: `weft/ext.py`, `README.md`
- T19: `docs/plans/README.md` (index row), this file (status flip on
  completion)

---

## Tasks

Execute in order. Tasks 8-18 are mutually independent and may be reordered,
but keep one commit per task.

### Task 1: Preflight and baseline

- [ ] **Step 1.1** Run the environment setup block from "Current Repo State"
  above.
- [ ] **Step 1.2** Record the baseline. All three must pass before you change
  anything (so you never debug a pre-existing failure as if it were yours):

```bash
./.venv/bin/python -m pytest -q          # fast suite; ~2-4 min, all green
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Expected: pytest ends `passed` with only environment-conditional skips; mypy
prints `Success: no issues found in 177 source files` (count may drift);
ruff prints `All checks passed!`.

- [ ] **Step 1.3** Run `git status`. Expected dirty paths (any subset):
  `weft/commands/system.py` and `tests/commands/test_status.py` (in-flight
  status-reconciliation work — leave alone), plus this plan's own files:
  `docs/plans/2026-06-09-evaluation-findings-remediation-plan.md` and the
  index/count edit in `docs/plans/README.md`. Anything else dirty: stop and
  ask.
- [ ] **Step 1.4** If the plan file and its index row are not yet committed,
  commit them first so later task commits stay single-purpose:

```bash
git add docs/plans/2026-06-09-evaluation-findings-remediation-plan.md docs/plans/README.md
git commit -m "Add evaluation findings remediation plan"
```

### Task 2: Reject `approval_required: true` at TaskSpec validation

**Outcome:** constructing an `AgentToolSection` (and therefore any TaskSpec)
with `approval_required: true` raises a clear `ValidationError` instead of
being silently ignored. `false`/omitted keeps working unchanged.

**Why rejection, not enforcement:** spec [AR-0.0] says Weft does not own
approval-policy engines — approvals belong to systems built on top of Weft or
to a delegated provider CLI. The in-process `llm` backend
(`weft/core/agents/backends/llm.py`) runs in a detached background task
process with no UI to answer an approval prompt, and nothing anywhere reads
the flag today. The repo principle is "prefer explicit rejection over
silently ignoring unsupported fields" (engineering-principles.md, Secondary
Rules). No existing test or builtin sets `approval_required: true`
(`grep -rn "approval_required" tests/ extensions/` returns nothing), so no
fixture migration is needed.

- Files to touch: `weft/core/taskspec/model.py`,
  `tests/taskspec/test_taskspec.py`
- Read first: `docs/specifications/13-Agent_Runtime.md` [AR-0.0] (the
  "Boundary Decision" section near the top) and [AR-2.2];
  `weft/core/taskspec/model.py` — the `AgentToolSection` class (search for
  `class AgentToolSection`); two existing `@field_validator` usages in the
  same file (search `@field_validator`) for local style.
- Reuse: `field_validator` is already imported at the top of
  `model.py`. Do not add new imports.
- Not allowed: do NOT remove the `approval_required` field (removal plus
  `extra="forbid"` would produce a generic "extra field" error instead of an
  explanation, and would also change `ResolvedAgentTool` in
  `weft/core/agents/tools.py`, which extensions may construct). Do NOT touch
  `weft/core/agents/tools.py` or `weft/core/agents/backends/llm.py` at all.
- Stop if: you find any code path that actually reads
  `ResolvedAgentTool.approval_required` (there should be none). That would
  mean the evaluation finding is stale — stop and report instead of
  proceeding.

- [ ] **Step 2.1: Write the failing test.** In
  `tests/taskspec/test_taskspec.py`, append (match the file's existing
  import style — it already imports `pytest` and the taskspec models; add
  `AgentToolSection` to the existing `weft.core.taskspec` import if it is
  not already imported, and `ValidationError` from `pydantic`):

```python
def test_agent_tool_approval_required_true_is_rejected() -> None:
    """approval_required has no enforcement path; True must fail loudly.

    Spec: [AR-0.0], [AR-2.2].
    """
    with pytest.raises(ValidationError, match="approval_required"):
        AgentToolSection(
            name="dangerous_tool",
            kind="python",
            ref="tests.tasks.sample_targets:echo_payload",
            approval_required=True,
        )


def test_agent_tool_approval_required_false_still_validates() -> None:
    """The default (False) and explicit False stay accepted."""
    tool = AgentToolSection(
        name="safe_tool",
        kind="python",
        ref="tests.tasks.sample_targets:echo_payload",
        approval_required=False,
    )
    assert tool.approval_required is False
```

- [ ] **Step 2.2: Run it; confirm the first test FAILS.**

```bash
./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py -k approval -q
```

Expected: 1 failed (`DID NOT RAISE`), 1 passed.

- [ ] **Step 2.3: Implement.** In `weft/core/taskspec/model.py`, inside
  `class AgentToolSection`, directly after the field declarations and before
  `model_post_init`, add:

```python
    @field_validator("approval_required")
    @classmethod
    def _reject_unsupported_approval_required(cls, v: bool) -> bool:
        """Reject the unimplemented approval flag instead of ignoring it.

        Weft does not implement tool approval policy ([AR-0.0]); approvals
        belong to the calling system or to a delegated provider CLI.

        Spec: [AR-0.0], [AR-2.2]
        """
        if v:
            raise ValueError(
                "approval_required is not supported: Weft does not implement "
                "tool approval policy. Enforce approvals in the calling "
                "system, or use a provider_cli runtime where the provider "
                "CLI owns approvals. See "
                "docs/specifications/13-Agent_Runtime.md [AR-0.0]."
            )
        return v
```

- [ ] **Step 2.4: Run the test again; both pass.** Same command as 2.2.
  Expected: 2 passed.
- [ ] **Step 2.5: Run the blast-radius slice.**

```bash
./.venv/bin/python -m pytest tests/taskspec/ tests/core/test_agent_tools.py tests/core/test_llm_backend.py tests/core/test_agent_validation.py -q
```

Expected: all pass. If anything fails because it constructed a tool with
`approval_required=True`, that contradicts the grep in the task header —
stop and report; do not edit that test to make it pass.

- [ ] **Step 2.6: Commit.**

```bash
git add weft/core/taskspec/model.py tests/taskspec/test_taskspec.py
git commit -m "Reject unsupported approval_required on agent tools"
```

### Task 2b: Spec text and backlinks for the approval rejection

**Outcome:** the normative spec says what the code now does, and both touched
specs link back to this plan. Source spec: this task IS the spec update.

- Files to touch: `docs/specifications/13-Agent_Runtime.md`,
  `docs/specifications/02-TaskSpec.md`
- Read first: in 13-Agent_Runtime.md, the `### Agent Field Semantics
  [AR-2.2]` bullet list (it has one bullet per agent field) and the
  `_Implementation mapping:_` note that closes that section; the spec's
  `## Related Plans` section at the bottom (if present — append; if absent,
  create it after the last content section, mirroring how
  `docs/specifications/05-Message_Flow_and_State.md` formats its
  `## Related Plans` list).

- [ ] **Step 2b.1** In `13-Agent_Runtime.md` [AR-2.2], the `tools:` bullet
  currently ends with "...backend tool declarations." Append this sentence to
  that same bullet:

```text
  Tool descriptors accept `approval_required` for schema compatibility, but
  only `false` is valid: Weft does not implement tool approval policy
  ([AR-0.0]), and validation rejects `approval_required: true` with guidance
  to enforce approvals in the calling system or a delegated provider CLI.
```

- [ ] **Step 2b.2** In the same section's `_Implementation mapping:_` note,
  after the sentence "All fields listed above are implemented.", add:

```text
`approval_required: true` is rejected by a field validator on
`AgentToolSection` in `weft/core/taskspec/model.py`.
```

- [ ] **Step 2b.3** Add a backlink line to `## Related Plans` in BOTH
  `13-Agent_Runtime.md` and `02-TaskSpec.md` (02 already has a
  `## Related Plans` section near the bottom — append there):

```text
- [`docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`](../plans/2026-06-09-evaluation-findings-remediation-plan.md)
```

- [ ] **Step 2b.4** Run the spec-hygiene guard and commit:

```bash
./.venv/bin/python -m pytest tests/specs/ -q
git add docs/specifications/13-Agent_Runtime.md docs/specifications/02-TaskSpec.md
git commit -m "Document approval_required rejection in agent runtime spec"
```

### Task 3: Owner-only filesystem helpers

**Outcome:** three small helpers in `weft/helpers/__init__.py` that create
directories as 0700 and files as 0600, with tests. Tasks 4-6 consume them.
Source spec: none — shared plumbing for the Context/spill/dump tasks; the
spec-facing statements land in those tasks.

- Files to touch: `weft/helpers/__init__.py`, `tests/system/test_helpers.py`
- Read first: the module docstring and import block of
  `weft/helpers/__init__.py` (note: it imports `weft._constants` and
  `weft.ext` only — it must NOT gain a `weft.context` import);
  `tests/system/test_helpers.py` for test style; CLAUDE.md §4.5 docstring
  format.
- Reuse: nothing existing covers this (verified: the only mode-aware writes
  in the repo are SimpleBroker's own DB hardening, which lives at the
  SimpleBroker layer).
- Not allowed: no umask manipulation (process-global, thread-hostile); no
  general "permissions framework"; exactly these three functions. Windows
  note: `os.chmod` on Windows only toggles the read-only bit — these helpers
  are best-effort there, which matches the repo's POSIX-first hardening
  posture; do not add Windows-specific branches.

- [ ] **Step 3.1: Write the failing tests.** Append to
  `tests/system/test_helpers.py` (it already imports `Path` and the helpers
  module; extend its imports with the three new names and add
  `import os`, `import stat`, `import sys`, `import pytest` if missing):

```python
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_ensure_owner_only_dir_creates_and_tightens(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    ensure_owner_only_dir(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700

    # Pre-existing loose directory gets tightened, not just created.
    loose = tmp_path / "loose"
    loose.mkdir()
    os.chmod(loose, 0o775)
    ensure_owner_only_dir(loose)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_write_owner_only_bytes_is_0600(tmp_path: Path) -> None:
    target = tmp_path / "secret.dat"
    write_owner_only_bytes(target, b"payload")
    assert target.read_bytes() == b"payload"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    # Overwriting a pre-existing loose file tightens it.
    os.chmod(target, 0o644)
    write_owner_only_bytes(target, b"payload2")
    assert target.read_bytes() == b"payload2"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_open_owner_only_text_is_0600(tmp_path: Path) -> None:
    target = tmp_path / "export.jsonl"
    with open_owner_only_text(target) as handle:
        handle.write("line1\n")
    assert target.read_text(encoding="utf-8") == "line1\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
```

- [ ] **Step 3.2: Run; confirm FAIL** (ImportError / NameError):

```bash
./.venv/bin/python -m pytest tests/system/test_helpers.py -k owner_only -q
```

- [ ] **Step 3.3: Implement.** In `weft/helpers/__init__.py`, near the other
  filesystem helpers (`write_file_atomically` / `write_json_atomically` —
  search for `def write_json_atomically`), add:

```python
def ensure_owner_only_dir(path: Path | str) -> Path:
    """Create *path* (and parents) and restrict it to the owner (0700).

    Pre-existing directories are tightened with chmod as well, so upgrades
    repair directories created by older Weft versions with default umask.
    Only the leaf directory is tightened: intermediate parents created by
    mkdir(parents=True) keep default modes deliberately, because callers own
    the policy for paths above the Weft-owned leaf. On Windows, chmod only
    affects the read-only bit; this is best-effort there by design.

    Args:
        path: Directory to create or tighten.

    Returns:
        The directory as a Path.

    Spec: docs/specifications/07-System_Invariants.md (Context Invariants)
    """
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    return target


def write_owner_only_bytes(path: Path | str, data: bytes) -> None:
    """Write *data* to *path* readable only by the owner (0600).

    The 0600 mode is applied at open time for new files and re-applied with
    chmod so pre-existing looser files are tightened before content lands.

    Spec: docs/specifications/05-Message_Flow_and_State.md
    (Large Output Handling)
    """
    target = Path(path)
    fd = os.open(os.fspath(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # chmod by path (os.fchmod does not exist on Windows); the brief
        # path/fd window is acceptable under the user-level trust model.
        os.chmod(target, 0o600)
        handle = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        handle.write(data)


@contextmanager
def open_owner_only_text(
    path: Path | str, *, encoding: str = "utf-8"
) -> Iterator[TextIO]:
    """Open *path* for text writing with owner-only (0600) permissions.

    Same tightening semantics as write_owner_only_bytes, for streaming
    writers such as `weft system dump`.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-6]
    """
    target = Path(path)
    fd = os.open(os.fspath(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # chmod by path (os.fchmod does not exist on Windows); the brief
        # path/fd window is acceptable under the user-level trust model.
        os.chmod(target, 0o600)
        handle = os.fdopen(fd, "w", encoding=encoding)
    except BaseException:
        os.close(fd)
        raise
    with handle:
        yield handle
```

  Import notes: `os`, `Path`, `contextmanager`, and `Iterator` are already
  imported at the top of `weft/helpers/__init__.py`. Add `TextIO` to the
  existing `from typing import ...` line. Export all three names: this module
  re-exports via `weft/helpers/__init__.py` itself, so just confirm there is
  no `__all__` that would exclude them (if an `__all__` exists, append the
  three names).

- [ ] **Step 3.4: Run; confirm PASS.** Same command as 3.2 — 3 passed.
- [ ] **Step 3.5: Lint/type the touched files and commit.**

```bash
./.venv/bin/ruff check weft/helpers
./.venv/bin/mypy weft/helpers
git add weft/helpers/__init__.py tests/system/test_helpers.py
git commit -m "Add owner-only filesystem helpers"
```

### Task 4: Owner-only context directories

**Outcome:** `build_context()` creates the Weft-owned metadata directories
(`.weft/`, `outputs/`, the autostart dir) as 0700 and tightens pre-existing
ones. User-relocatable paths (a custom logs dir, a custom database parent)
keep default modes because Weft does not own their permission policy.

**Decision (explicit, and under test):** the default logs directory
(`.weft/logs`) is deliberately NOT chmod'd. Its protection comes from the
0700 `.weft/` parent — directory traversal is blocked at the parent — and
the test pins both halves of that argument: the parent's 0700 mode and the
`logs_dir == weft_dir / "logs"` placement premise. Custom `WEFT_LOGS_DIR`
and custom database-parent locations keep caller-owned modes. If you
believe logs need their own 0700, stop and ask; do not widen the hardening
scope unilaterally.

- Files to touch: `weft/context.py`, the `tests/context/` module that already
  tests `build_context` (locate it: `grep -rln "build_context" tests/context/`),
  `docs/specifications/07-System_Invariants.md`
- Read first: `weft/context.py` — the `if create_dirs:` block inside
  `build_context` (search for `create_dirs:`); the module docstring (it
  documents which dirs the context owns).
- Reuse: `ensure_owner_only_dir` from Task 3. Do not inline chmod logic here.
- Constraints: `weft/context.py` currently imports only stdlib,
  `simplebroker`, and `weft._constants`. Adding
  `from weft.helpers import ensure_owner_only_dir` is safe **because
  `weft/helpers` does not import `weft.context`** (verified 2026-06-09; its
  weft imports are `weft._constants` and `weft.ext`).
- Stop if: importing `weft.helpers` from `weft/context.py` produces an
  ImportError or circular-import error when running `./.venv/bin/python -c
  "import weft"`. That means the helpers module gained a context dependency
  since this plan was written — report it; do not work around it with a
  function-level import.

- [ ] **Step 4.1: Write the failing test.** In the located context test
  module, append (add `import os`, `import stat`, `import sys` to its imports
  if missing; it already has `tmp_path` style tests and imports
  `build_context`):

```python
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_build_context_creates_owner_only_metadata_dirs(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root, autostart=True)
    assert stat.S_IMODE(ctx.weft_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(ctx.outputs_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(ctx.autostart_dir.stat().st_mode) == 0o700
    # Decision under test: the DEFAULT logs dir is protected by its 0700
    # parent rather than its own mode (custom WEFT_LOGS_DIR locations keep
    # caller-owned modes). Pin the placement premise that protection rests on:
    assert ctx.logs_dir == ctx.weft_dir / "logs"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_build_context_tightens_preexisting_loose_weft_dir(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    loose = root / ".weft"
    loose.mkdir(exist_ok=True)
    os.chmod(loose, 0o775)
    build_context(spec_context=root)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700
```

  Mirror the module's existing `prepare_project_root` usage and import —
  neighboring tests in this module already route through it, which keeps
  these tests valid under the Postgres-backed suite (`bin/pytest-pg` sets
  the broker backend globally; `prepare_project_root` provides the
  per-project isolation).

- [ ] **Step 4.2: Run; confirm FAIL** (modes will be 0o755/0o775):

```bash
./.venv/bin/python -m pytest tests/context/ -k owner_only_metadata -q
./.venv/bin/python -m pytest tests/context/ -k tightens_preexisting -q
```

- [ ] **Step 4.3: Implement.** In `weft/context.py`, add the import (top of
  file, in the local-imports group):

```python
from weft.helpers import ensure_owner_only_dir
```

  Replace the current creation block:

```python
    if create_dirs:
        paths = {weft_dir, outputs_dir, logs_dir}
        if database_path is not None:
            paths.add(database_path.parent)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        if autostart_enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
```

  with:

```python
    if create_dirs:
        # Weft-owned metadata dirs are owner-only. User-relocatable paths
        # (custom logs dir, custom database parent) keep default modes:
        # Weft does not own their permission policy, and the default logs
        # dir sits inside the 0700 weft_dir.
        ensure_owner_only_dir(weft_dir)
        ensure_owner_only_dir(outputs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        if database_path is not None:
            database_path.parent.mkdir(parents=True, exist_ok=True)
        if autostart_enabled:
            ensure_owner_only_dir(autostart_dir)
```

- [ ] **Step 4.4: Run; confirm PASS**, then run the context + cycle check:

```bash
./.venv/bin/python -c "import weft"
./.venv/bin/python -m pytest tests/context/ tests/architecture/ -q
```

- [ ] **Step 4.5: Spec update.** In
  `docs/specifications/07-System_Invariants.md`, "Context Invariants"
  section, append after **CTX.4**:

```text
- **CTX.5**: Weft-created metadata directories (the `.weft/` project home,
  its `outputs/` child, and the autostart directory) are owner-only (0700),
  including tightening of pre-existing looser directories at context build.
  The default logs directory is protected by its 0700 parent rather than
  its own mode; custom `WEFT_LOGS_DIR` and custom database locations keep
  caller-owned modes.
```

  Update that section's `_Implementation mapping_` note (or add one directly
  under the "### Context Invariants" heading if absent) to include:
  `weft/context.py` (`build_context` directory creation) and
  `weft/helpers/__init__.py` (`ensure_owner_only_dir`). Add the backlink to
  the `## Related Plans` section of 07:

```text
- [`docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`](../plans/2026-06-09-evaluation-findings-remediation-plan.md)
```

- [ ] **Step 4.6: Commit.**

```bash
./.venv/bin/python -m pytest tests/specs/ -q
git add weft/context.py tests/context/ docs/specifications/07-System_Invariants.md
git commit -m "Create Weft context metadata directories owner-only"
```

### Task 5: Owner-only large-output spill files

**Outcome:** `_spill_large_output` writes `outputs/<tid>/output.dat` as 0600
inside a 0700 per-tid directory. This also protects the shared-`/tmp`
fallback location (`$TMPDIR/weft/outputs/<tid>/`) used when a task has no
project context. Source spec:
`docs/specifications/05-Message_Flow_and_State.md`, "Large Output Handling".

- Files to touch: `weft/core/tasks/base.py`,
  `tests/tasks/test_task_execution.py`
- Read first: `weft/core/tasks/base.py` — `_spill_large_output` and
  `_outputs_base_dir` (search those names); the existing spill coverage:
  `grep -n "spill\|large_output" tests/tasks/test_task_execution.py`.
- Reuse: `ensure_owner_only_dir` and `write_owner_only_bytes` from Task 3.
  `weft/core/tasks/base.py` already imports from `weft.helpers` (search
  `from weft.helpers import`) — extend that import list.
- Not allowed: do not change the spill threshold, the reference-message
  shape, or the `output_spilled` task-log event.

- [ ] **Step 5.1: Extend the existing spill test (red).** The target is
  `test_large_output_spills_to_disk` in
  `tests/tasks/test_task_execution.py` (it runs a real task whose output
  exceeds the broker limit and asserts on the `large_output` reference; its
  local variable for the spilled file path is `output_path` today — verify
  the name in the file and adapt if it has drifted). Append at the end of
  that test:

```python
    if sys.platform != "win32":
        assert stat.S_IMODE(Path(output_path).stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(output_path).parent.stat().st_mode) == 0o700
```

  Add `import stat` to the module (it already imports `sys` and `Path`). If
  the test no longer asserts on the spill reference, stop and report — do
  not invent a mock-based spill test; the real execution path is the
  required proof.

- [ ] **Step 5.2: Run; confirm the new assertions FAIL** (current modes are
  0644/0755):

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -k spill -q
```

  (If `-k spill` selects nothing, use the test name you found in 5.1.)

- [ ] **Step 5.3: Implement.** In `_spill_large_output`, replace:

```python
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "output.dat"
        output_path.write_bytes(encoded)
```

  with:

```python
        ensure_owner_only_dir(output_dir)
        output_path = output_dir / "output.dat"
        write_owner_only_bytes(output_path, encoded)
```

- [ ] **Step 5.4: Run; confirm PASS.** Same command as 5.2.
- [ ] **Step 5.5: Spec note.** In
  `docs/specifications/05-Message_Flow_and_State.md`, "Large Output
  Handling" section, the `_Implementation mapping_` note already names
  `_spill_large_output`. Append one sentence to that mapping note:

```text
Spill files are written owner-only (0600) inside an owner-only (0700)
per-tid directory via the shared helpers in `weft/helpers/__init__.py`.
```

  Add the plan backlink to 05's `## Related Plans` section (same line format
  as Task 4.5).

- [ ] **Step 5.6: Commit.**

```bash
git add weft/core/tasks/base.py tests/tasks/test_task_execution.py docs/specifications/05-Message_Flow_and_State.md
git commit -m "Write large-output spill files owner-only"
```

### Task 6: Owner-only `weft system dump` output

**Outcome:** the dump JSONL (which contains TaskSpec `env` values and command
args verbatim) is created 0600, pre-existing looser dump files are tightened,
and the docs say to treat dumps like secret files. Dump *content* is
deliberately unchanged (see Invariants). Source spec:
`docs/specifications/10-CLI_Interface.md` [CLI-6].

- Files to touch: `weft/commands/_dump_support.py`,
  `tests/commands/test_dump_load.py`, `README.md`,
  `docs/specifications/10-CLI_Interface.md`
- Read first: `weft/commands/_dump_support.py` — `cmd_dump` (the
  `with open(output_path, "w", encoding="utf-8") as f:` line);
  `tests/commands/test_dump_load.py` for how existing dump tests build a
  context.
- Reuse: `open_owner_only_text` from Task 3.

- [ ] **Step 6.1: Write the failing test.** Append to
  `tests/commands/test_dump_load.py` (match its existing imports; add
  `import os`, `import stat`, `import sys` if missing, and import `cmd_dump`
  the same way the module already does):

```python
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_cmd_dump_output_file_is_owner_only(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)  # the module's fixtures already use this
    out_path = tmp_path / "export.jsonl"
    out_path.write_text("stale", encoding="utf-8")
    os.chmod(out_path, 0o644)

    exit_code, _message = cmd_dump(output=str(out_path), context_path=str(root))

    assert exit_code == 0
    assert stat.S_IMODE(out_path.stat().st_mode) == 0o600
```

- [ ] **Step 6.2: Run; confirm FAIL** (mode stays 0o644 today):

```bash
./.venv/bin/python -m pytest tests/commands/test_dump_load.py -k owner_only -q
```

- [ ] **Step 6.3: Implement.** In `cmd_dump`, add
  `open_owner_only_text` to the module's existing `weft.helpers` import (or
  add `from weft.helpers import open_owner_only_text` if none exists), and
  replace:

```python
            with open(output_path, "w", encoding="utf-8") as f:
```

  with:

```python
            with open_owner_only_text(output_path) as f:
```

- [ ] **Step 6.4: Run; confirm PASS**, then the dump/load slice:

```bash
./.venv/bin/python -m pytest tests/commands/test_dump_load.py -q
```

- [ ] **Step 6.5: Docs.** In `README.md`, the paragraph that begins
  "`weft system dump` exports visible pending broker messages..." — append:

```text
Dump files contain TaskSpec command arguments and `spec.env` values
verbatim (dump/load is a fidelity-preserving surface, so nothing is
redacted); the file is created owner-only (0600). Treat dump files like
secret material.
```

  In `docs/specifications/10-CLI_Interface.md`, locate the `[CLI-6]`
  dump/load section and append the same fact as an implementation note
  (one sentence, matching the spec's nearby `_Implementation mapping_` /
  note style), and add the plan backlink to that spec's `## Related Plans`
  section (same line format as Task 4.5).

- [ ] **Step 6.6: Commit.**

```bash
./.venv/bin/python -m pytest tests/specs/ -q
git add weft/commands/_dump_support.py tests/commands/test_dump_load.py README.md docs/specifications/10-CLI_Interface.md
git commit -m "Create weft system dump output owner-only"
```

### Task 7: macOS sandbox runner — explicit env allowlist

**Outcome:** the sandbox runner stops inheriting the full host environment.
The child env becomes: a fixed baseline of session-plumbing variables, plus
host variables the spec opts into via the new
`spec.runner.options.env_passthrough` list, plus `spec.env` (which always
wins). This mirrors the Docker runner's explicit `_forward_host_env`
approach. **This is a deliberate behavior change** for sandbox users who
relied on implicit inheritance; the migration path is `env_passthrough` (for
host-derived values) or `spec.env` (for fixed values). Source spec:
`docs/specifications/01-Core_Components.md` [CC-3.1], [CC-3.2] (runner
contract); the env behavior itself is extension-owned and is documented in
the extension README.

- Files to touch:
  `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`,
  `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py`,
  `extensions/weft_macos_sandbox/README.md`
- Read first: in `plugin.py` — `MacOSSandboxRunner.__init__`,
  `run_with_hooks` (the `env_vars = os.environ.copy()` lines), and
  `MacOSSandboxRunnerPlugin.validate_taskspec`; the existing test
  `test_macos_sandbox_runner_publishes_runner_authority_handle` in the test
  file (you will clone its fake-`Popen` / fake-`run_monitored_subprocess`
  scaffolding — that is the sanctioned external boundary to fake);
  `extensions/weft_docker/weft_docker/profiles.py` `_forward_host_env` (the
  pattern being mirrored — do NOT import it across extensions; the
  eight-line equivalent lives locally).
- Constraints: argv-only `Popen([...])` stays exactly as is; only the `env=`
  dict changes. No new dependencies. Keep validation duplicated in both
  `__init__` and `validate_taskspec`, mirroring how `profile` is already
  validated in both places.

- [ ] **Step 7.1: Write the failing tests.** Append to
  `extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py`,
  reusing the existing module's imports. The snippet below is self-contained;
  it mirrors the fake scaffolding of
  `test_macos_sandbox_runner_publishes_runner_authority_handle` (faking only
  the true external boundary — `Popen` and the monitored-subprocess wait —
  exactly as that test already does):

```python
def test_sandbox_child_env_is_allowlisted_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    monkeypatch.setenv("WEFT_TEST_SECRET", "leak-me")
    monkeypatch.setenv("WEFT_TEST_OPTIN", "forwarded")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

    def fake_run_monitored_subprocess(**kwargs: Any) -> plugin.RunnerOutcome:
        return plugin.RunnerOutcome(
            status="ok",
            value=None,
            error=None,
            stdout="",
            stderr="",
            returncode=0,
            duration=0.0,
            runtime_handle=kwargs["runtime_handle"],
        )

    def fake_popen(argv: Any, **kwargs: Any) -> Any:
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(plugin, "process_create_time", lambda pid: 123.456)
    monkeypatch.setattr(
        plugin, "run_monitored_subprocess", fake_run_monitored_subprocess
    )

    runner = plugin.MacOSSandboxRunner(
        process_target="python3",
        args=[],
        env={"SPEC_VAR": "from-spec"},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=None,
        runner_options={
            "profile": str(profile),
            "env_passthrough": ["WEFT_TEST_OPTIN"],
        },
    )
    runner.run_with_hooks({})

    env = captured["env"]
    assert env["SPEC_VAR"] == "from-spec"
    assert env["WEFT_TEST_OPTIN"] == "forwarded"
    assert env["PATH"] == "/usr/bin:/bin"
    assert "WEFT_TEST_SECRET" not in env


def test_sandbox_env_passthrough_must_be_string_list(tmp_path: Path) -> None:
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="env_passthrough"):
        plugin.MacOSSandboxRunner(
            process_target="python3",
            args=[],
            env={},
            working_dir=None,
            timeout=None,
            limits=None,
            monitor_class=None,
            monitor_interval=None,
            runner_options={"profile": str(profile), "env_passthrough": "oops"},
        )
```

  The `{}` work item matches what the existing handle test passes to
  `run_with_hooks` (an empty work item exercises the env path without queue
  plumbing).

- [ ] **Step 7.2: Run; confirm FAIL** (the secret IS in the env today, and
  the constructor accepts `"oops"`):

```bash
./.venv/bin/python -m pytest extensions/weft_macos_sandbox/tests -q
```

- [ ] **Step 7.3: Implement.** In `plugin.py`:

  (a) Module-level constant, after the imports:

```python
_BASE_ENV_PASSTHROUGH: tuple[str, ...] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "USER",
)
"""Host env keys forwarded into sandboxed processes by default.

Session plumbing only: enough for host binaries to resolve paths, temp
space, and locale. Everything else requires spec.runner.options
env_passthrough (host-derived values) or spec.env (fixed values), so
secrets in the parent environment never leak into sandboxed tasks
implicitly.
"""
```

  (b) In `MacOSSandboxRunner.__init__`, after the `profile` validation block,
  add:

```python
        env_passthrough = options.get("env_passthrough")
        if env_passthrough is None:
            self._env_passthrough: tuple[str, ...] = ()
        else:
            if not isinstance(env_passthrough, list) or not all(
                isinstance(item, str) and item.strip() for item in env_passthrough
            ):
                raise ValueError(
                    "macOS sandbox runner option env_passthrough must be a "
                    "list of non-empty environment variable names"
                )
            self._env_passthrough = tuple(item.strip() for item in env_passthrough)
```

  (c) New method on `MacOSSandboxRunner`:

```python
    def _build_child_env(self) -> dict[str, str]:
        """Forward baseline plus opted-in host env keys, then spec env.

        Spec env always wins so TaskSpec-declared values cannot be shadowed
        by host state.
        """
        env_vars: dict[str, str] = {}
        for key in (*_BASE_ENV_PASSTHROUGH, *self._env_passthrough):
            value = os.environ.get(key)
            if value is not None:
                env_vars[key] = value
        env_vars.update(self._env)
        return env_vars
```

  (d) In `run_with_hooks`, replace:

```python
        env_vars = os.environ.copy()
        env_vars.update(self._env)
```

  with:

```python
        env_vars = self._build_child_env()
```

  (e) In `MacOSSandboxRunnerPlugin.validate_taskspec`, after the existing
  `profile` validation, add the same `env_passthrough` shape check as (b)
  (raising the same message), validating `options.get("env_passthrough")`
  when it is not None. Do not store anything — `validate_taskspec` only
  validates.

- [ ] **Step 7.4: Run; confirm PASS** (whole extension suite):

```bash
./.venv/bin/python -m pytest extensions/weft_macos_sandbox/tests -q
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
```

- [ ] **Step 7.5: Document and version.** In
  `extensions/weft_macos_sandbox/README.md`, add a short "Environment
  variables" section: list the `_BASE_ENV_PASSTHROUGH` names as the
  default-forwarded set, show an `env_passthrough` example in
  `spec.runner.options`, state that `spec.env` always wins, and add the
  migration note ("weft-macos-sandbox releases before 0.6.0 inherited the
  full host environment; set env_passthrough explicitly if you depended on
  that"). The extension is separately versioned: bump `version` in
  `extensions/weft_macos_sandbox/pyproject.toml` from `0.5.4` to `0.6.0`
  (breaking behavior change). Do not change the root `pyproject.toml` extras
  pins — weft itself does not require the new behavior.
- [ ] **Step 7.6: Spec backlink.** This task implements against
  `docs/specifications/01-Core_Components.md` [CC-3.1], [CC-3.2], so the
  bidirectional-traceability rule applies: append the plan backlink to that
  spec's existing `## Related Plans` section (near the bottom of the file),
  same line format as Task 2b.3, then run
  `./.venv/bin/python -m pytest tests/specs/ -q`.
- [ ] **Step 7.7: Commit.**

```bash
git add extensions/weft_macos_sandbox docs/specifications/01-Core_Components.md
git commit -m "Allowlist sandbox child environment instead of inheriting host env"
```

### Task 8: CHANGELOG — missing 0.9.74 entry plus entries for this plan

**Outcome:** the 0.9.74 release gets its missing entry, and the
behavior changes from Tasks 2-7 are recorded under an `## Unreleased`
heading. Source spec: none — release ledger maintenance.

- Files to touch: `CHANGELOG.md`
- Read first: the `## 0.9.75 - 2026-06-01` entry (style reference);
  `git log v0.9.73..v0.9.74 --oneline` (you should see exactly
  `ed8554e Reconcile orphan monitor service state` plus the release-bump
  commit);
  `docs/plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`
  (what that change did).

- [ ] **Step 8.1** Insert a new `## Unreleased` section at the very top
  (above `## 0.9.75 - 2026-06-01`):

```markdown
## Unreleased

### Changed

- **Breaking:** TaskSpec validation now rejects agent tool descriptors with
  `approval_required: true`. The flag was previously accepted but silently
  unenforced; Weft does not implement tool approval policy ([AR-0.0]).
  Enforce approvals in the calling system or use a `provider_cli` runtime.
- **Breaking:** the macOS sandbox runner (weft-macos-sandbox 0.6.0) no
  longer inherits the full host environment. Sandboxed processes now receive
  a fixed baseline (PATH/HOME/TMPDIR/locale and similar), plus host
  variables opted in via `spec.runner.options.env_passthrough`, plus
  `spec.env`.
- Weft-owned data at rest is now owner-only: `.weft/` metadata directories
  are created 0700 (and tightened on upgrade), large-output spill files and
  `weft system dump` exports are written 0600. Dump content is unchanged and
  still contains TaskSpec env/args verbatim; treat dump files as secrets.
```

  Note: the repo has not used an `## Unreleased` heading before (verified:
  no tooling reads CHANGELOG.md). Folding these entries into the next
  version heading is a manual step during release prep — say so in your
  handoff.

- [ ] **Step 8.2** Insert the missing release entry between the end of the
  0.9.75 section and `## 0.9.73 - 2026-05-30`:

```markdown
## 0.9.74 - 2026-05-31

### Fixed

- Reconciled orphan task-monitor service state so `weft status` classifies
  stale internal service log entries as superseded service records instead
  of reporting dead heartbeat/monitor services as live tasks.
```

- [ ] **Step 8.3** Verify against history and commit:

```bash
git log v0.9.73..v0.9.74 --oneline
git add CHANGELOG.md
git commit -m "Backfill 0.9.74 changelog entry and record unreleased changes"
```

### Task 9: README Command Reference — eight missing commands

**Outcome:** every implemented command appears in the README's
"## Command Reference". Source spec: none — README/CLI parity (the CLI shape
itself is owned by `docs/specifications/10-CLI_Interface.md`).

- Files to touch: `README.md`
- Read first: the `## Command Reference` section of README.md and its
  subsection style (bash blocks of `weft <verb> ... [--flag]` lines). Verify
  every synopsis below against the live CLI before pasting:
  `./.venv/bin/weft task stop --help` etc.

- [ ] **Step 9.1** Add to the task-related subsection (where
  `weft task list` already appears):

```bash
# Control running tasks
weft task stop [TID] [--all] [--pattern TEXT] [--context PATH]
weft task kill [TID] [--all] [--pattern TEXT] [--context PATH]

# Liveness probe (waits for a matching PONG)
weft task ping TID [--timeout SECONDS] [--context PATH]
```

- [ ] **Step 9.2** Add to the queue subsection:

```bash
# Queue introspection
weft queue exists NAME [--json]
weft queue stats NAME [--json]
```

- [ ] **Step 9.3** Add to the spec subsection:

```bash
# Delete a stored spec
weft spec delete NAME [--type TEXT] [--context PATH]
```

- [ ] **Step 9.4** Add to the system/maintenance subsection:

```bash
# Prune stale broker rows (dry-run by default)
weft system prune [--apply|--dry-run] [--family FAMILY] [--force] [--context PATH]

# Scan task evidence and emit JSONL (non-destructive)
weft system task-monitor [--once|--follow] [--sink stdout|disk] [--log-dir PATH] [--checkpoint PATH] [--no-checkpoint] [--context PATH]
```

- [ ] **Step 9.5** Commit.

```bash
git add README.md
git commit -m "Document task stop/kill/ping, queue exists/stats, spec delete, system prune/task-monitor in README"
```

### Task 10: Quick Reference — `task ping`

**Outcome:** the Quick Reference CLI table matches the registered CLI.
Source spec: `docs/specifications/00-Quick_Reference.md` is itself the doc
being fixed; `weft task ping` is specified in
`docs/specifications/10-CLI_Interface.md`.

- Files to touch: `docs/specifications/00-Quick_Reference.md`

- [ ] **Step 10.1** In the "Current top-level verbs and subcommands" table,
  change the `weft task` row from:

```text
| `weft task` | `list`, `status`, `stop`, `kill`, `tid` |
```

  to:

```text
| `weft task` | `list`, `status`, `stop`, `kill`, `ping`, `tid` |
```

- [ ] **Step 10.2** Verify by eye against `./.venv/bin/weft task --help`
  (only queue names have a conformance test —
  `tests/specs/quick_reference/test_queue_names.py`; the CLI table is not
  test-pinned, so the help output is the proof), then run the spec checks
  and commit:

```bash
./.venv/bin/python -m pytest tests/specs/ -q
git add docs/specifications/00-Quick_Reference.md
git commit -m "Add task ping to the Quick Reference CLI table"
```

### Task 11: Name the literal sleep intervals

**Outcome:** the four sleep sites that use inline literals get named
`_constants` values with docstrings, restoring the repo claim that "every
sleep uses a named constant." No behavior change (same numeric values).
Source spec: none — house-style conformance (CLAUDE.md §4.4).

- Files to touch: `weft/_constants.py`, `weft/commands/tasks.py`,
  `weft/helpers/__init__.py`, `weft/core/launcher.py`
- Read first: the existing poll-interval constants cluster in
  `weft/_constants.py` (search `TASK_PROCESS_POLL_INTERVAL`) for placement
  and docstring style.
- TDD note: red-green does not apply — values are unchanged, so there is no
  expressible failing behavior. The proof is (a) the full fast suite stays
  green and (b) `grep -n "time.sleep" weft/commands/tasks.py
  weft/core/launcher.py` shows no remaining numeric literals at these sites.

- [ ] **Step 11.1** In `weft/_constants.py`, next to the existing poll
  constants, add:

```python
TASK_EVIDENCE_POLL_INTERVAL: Final[float] = 0.05
"""Poll tick while waiting for terminal task evidence to appear.

Terminal evidence converges from queue reads plus process liveness; neither
exposes a blocking wait with timeout here, so callers poll with a small tick
bounded by the caller's deadline.
"""

TASK_PID_EXIT_POLL_INTERVAL: Final[float] = 0.02
"""Poll tick while waiting for task PIDs to exit after a kill.

psutil exposes no portable event-based process-exit wait for arbitrary
(non-child) PIDs, so the kill path polls with a small tick bounded by the
caller's deadline.
"""

ATOMIC_WRITE_RETRY_ATTEMPTS: Final[int] = 10
"""Retry budget for atomic replace when the target is transiently locked."""

ATOMIC_WRITE_RETRY_INTERVAL: Final[float] = 0.01
"""Pause between atomic-replace retries.

Windows file locks release asynchronously and emit no event we can wait on,
so the writer retries briefly instead.
"""

PARENT_LOSS_WAKE_INTERVAL_FLOOR: Final[float] = 0.05
PARENT_LOSS_WAKE_INTERVAL_CEILING: Final[float] = 0.2
"""Bounds for the parent-loss watcher wake interval.

Parent-process death has no portable notification, so the watcher polls;
the floor keeps the thread from spinning, the ceiling keeps orphan
detection prompt.
"""
```

- [ ] **Step 11.2** In `weft/commands/tasks.py`, find the two
  `time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))` lines (in
  the terminal-evidence wait loop) and the one
  `time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))` line (in
  the PID-exit wait). Replace `0.05` with `TASK_EVIDENCE_POLL_INTERVAL` and
  `0.02` with `TASK_PID_EXIT_POLL_INTERVAL`, adding both names to the
  module's existing `from weft._constants import (...)` block.
- [ ] **Step 11.3** In `weft/helpers/__init__.py`, in
  `write_file_atomically`, replace the local
  `retry_attempts = 10` / `retry_sleep_seconds = 0.01` assignments with the
  two `ATOMIC_WRITE_*` constants (extend the module's existing
  `from weft._constants import (...)`), updating the loop references.
- [ ] **Step 11.4** In `weft/core/launcher.py`, in
  `_start_parent_loss_watcher`, replace
  `wake_interval = min(max(poll_interval, 0.05), 0.2)` with:

```python
    wake_interval = min(
        max(poll_interval, PARENT_LOSS_WAKE_INTERVAL_FLOOR),
        PARENT_LOSS_WAKE_INTERVAL_CEILING,
    )
```

  (extend that module's `_constants` import).
- [ ] **Step 11.5** Verify and commit:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/system/test_helpers.py tests/tasks/ -q
./.venv/bin/ruff check weft
git add weft/_constants.py weft/commands/tasks.py weft/helpers/__init__.py weft/core/launcher.py
git commit -m "Name literal sleep intervals as documented constants"
```

  (If `tests/commands/test_task_commands.py` does not exist under that name,
  run `./.venv/bin/python -m pytest tests/commands -q` instead.)

### Task 12: Single source for the WEFT_CONTEXT env read

**Outcome:** the raw `os.environ.get("WEFT_CONTEXT")` string, currently
duplicated in two command modules, reads through one named constant.
Source spec: none — constants centralization (CLAUDE.md §4.4).

- Files to touch: `weft/_constants.py`, `weft/commands/queue.py`,
  `weft/commands/system.py`
- **Dirty-tree caution:** `weft/commands/system.py` may carry in-flight
  changes. Touch ONLY the `_resolve_context` helper's env read; do not
  reformat or reorder anything else in that file.
- Read first: `weft/_constants.py` around `WEFT_ENV_FILE_ENV` (the naming
  pattern for env-var-name constants); `_context` in
  `weft/commands/queue.py` and `_resolve_context` in
  `weft/commands/system.py`.
- TDD note: pure refactor, no behavior change; proof is the existing queue
  and status command tests staying green plus
  `grep -rn '"WEFT_CONTEXT"' weft/` showing only the `_constants.py`
  definition afterward.

- [ ] **Step 12.1** In `weft/_constants.py`, directly after
  `WEFT_ENV_FILE_ENV`, add:

```python
WEFT_CONTEXT_ENV: Final[str] = "WEFT_CONTEXT"
"""Environment variable naming the active project context directory."""
```

- [ ] **Step 12.2** In `weft/commands/queue.py`, replace
  `os.environ.get("WEFT_CONTEXT")` with `os.environ.get(WEFT_CONTEXT_ENV)`
  and add a NEW import line `from weft._constants import WEFT_CONTEXT_ENV`
  in the local-imports group (queue.py has no `weft._constants` import
  today, so there is no existing block to extend).
- [ ] **Step 12.3** Apply the same replacement in `weft/commands/system.py`
  (`_resolve_context`), adding `WEFT_CONTEXT_ENV` to that module's EXISTING
  `from weft._constants import (...)` block. **This edit is required — the
  task outcome is a single source for the env read, and the plan may not
  close without it.** The file may carry unrelated in-flight changes; use
  this NON-interactive choreography to commit only this plan's hunk (never
  `git add -p`):

```bash
git status --porcelain weft/commands/system.py tests/commands/test_status.py
# If output is empty (clean): just make the edit; Step 12.4 commits it. Skip the rest of 12.3.
# If dirty — snapshot and park the in-flight work first:
git diff weft/commands/system.py tests/commands/test_status.py > /tmp/weft-inflight.patch
git stash push -m "in-flight status-reconciliation (parked by remediation plan Task 12)" \
  weft/commands/system.py tests/commands/test_status.py
# Those files are now clean -> make the one-line edit plus the import change.
```

  After Step 12.4's commit, restore the parked work and verify it survived:

```bash
git stash pop
git status --porcelain weft/commands/system.py        # expect ' M' again (if it was dirty)
grep -c "_service_owner_tid_is_newer" weft/commands/system.py   # expect >= 1
```

  The in-flight hunks sit ~17 lines above `_resolve_context`, so the pop is
  expected to merge cleanly. If `git stash pop` reports a conflict: discard
  the conflicted working state with `git checkout -- weft/commands/system.py
  tests/commands/test_status.py` (your edit is already safe in the commit),
  then `git apply /tmp/weft-inflight.patch` and confirm
  `_service_owner_tid_is_newer` is back before `git stash drop`. If ANY of
  that fails, leave the stash in place (`git stash list` still holds the
  work — nothing is lost) and stop and report. Do not proceed to Task 19
  with the in-flight work missing or with the system.py edit unmade.
- [ ] **Step 12.4** Verify and commit (between stash push and stash pop when
  the file was dirty):

```bash
grep -rn '"WEFT_CONTEXT"' weft/   # expect ONLY the _constants.py definition line
./.venv/bin/python -m pytest tests/commands/test_status.py -q
ls tests/cli/ | grep -i queue     # then run the queue CLI test module it names
git add weft/_constants.py weft/commands/queue.py weft/commands/system.py
git commit -m "Read WEFT_CONTEXT through a named constant"
```

### Task 13: Remove redundant late imports

**Outcome:** three function-level stdlib imports with no cycle or
optional-dependency justification are removed/moved. Source spec: none —
house style (CLAUDE.md §4.2).

- Files to touch: `weft/core/taskspec/model.py`, `weft/commands/run.py`
- Read first: `weft/core/taskspec/model.py` line ~12 (`import time` is
  ALREADY at module top); the two method-level `import time` statements
  (search `        import time` — one in the TID field validator, one in a
  runtime-duration helper). `weft/commands/run.py` — the function-level
  `import threading` inside the interactive path (search
  `            import threading`); note the adjacent `prompt_toolkit`
  late import IS justified (optional dependency) — leave it alone.
- TDD note: deletion of redundant imports; no expressible failing test.
  Proof: suite slice green.

- [ ] **Step 13.1** Delete both method-level `import time` lines in
  `model.py` (the top-level import already serves them).
- [ ] **Step 13.2** In `weft/commands/run.py`, delete the function-level
  `import threading` line and add `import threading` to the module's
  top-of-file stdlib import group (alphabetized).
- [ ] **Step 13.3** Verify and commit:

```bash
./.venv/bin/python -m pytest tests/taskspec/ tests/commands/test_run.py -q
./.venv/bin/ruff check weft
git add weft/core/taskspec/model.py weft/commands/run.py
git commit -m "Remove redundant function-level stdlib imports"
```

### Task 14: `from __future__ import annotations` in package inits

**Outcome:** the five `__init__.py` files missing the future import gain it,
making the rule uniform. Source spec: none — house style (CLAUDE.md §4.2).

- Files to touch (the complete list, verified 2026-06-09 via
  `grep -rL "from __future__ import annotations" weft --include="*.py"`):
  - `weft/__init__.py`
  - `weft/builtins/tasks/__init__.py`
  - `weft/commands/__init__.py`
  - `weft/core/__init__.py`
  - `weft/core/runners/__init__.py`

- [ ] **Step 14.1** In each file, insert `from __future__ import
  annotations` as the first statement after the module docstring, followed
  by a blank line. Do not reorder anything else.
- [ ] **Step 14.2** Verify and commit:

```bash
grep -rL "from __future__ import annotations" weft --include="*.py"   # expect no output
./.venv/bin/python -c "import weft"
./.venv/bin/python -m pytest tests/architecture/ -q
git add weft/__init__.py weft/builtins/tasks/__init__.py weft/commands/__init__.py weft/core/__init__.py weft/core/runners/__init__.py
git commit -m "Add future annotations import to remaining package inits"
```

### Task 15: Align the late-import rule with actual policy

**Outcome:** CLAUDE.md/AGENTS.md §4.2 states the real (coherent) policy the
codebase follows instead of an absolute rule it visibly breaks 15+ times.
Source spec: none — contributor-guidance fix. **This edits repo guidance:
the repo owner approved it by approving this plan; do not reword beyond the
text below.**

- Files to touch: `CLAUDE.md`, `AGENTS.md` (same line in each — they carry
  identical §4.2 content)

- [ ] **Step 15.1** In both files, replace the line:

```text
- All imports at the top of the module (no late imports; avoid import loops by design)
```

  with:

```text
- Imports at the top of the module. Function-level imports are allowed only
  to break a real import cycle or to guard an optional dependency, and must
  carry a brief comment naming that reason; plain stdlib late imports are
  not allowed.
```

- [ ] **Step 15.2** Confirm the two files stayed in sync and commit:

```bash
diff <(grep -A2 "Imports at the top" CLAUDE.md) <(grep -A2 "Imports at the top" AGENTS.md)   # expect no output
git add CLAUDE.md AGENTS.md
git commit -m "Document the actual late-import policy"
```

### Task 16: Tests for `weft/commands/diagnostics.py`

**Outcome:** the previously untested shared diagnostic renderer gets direct
coverage. Source spec: none — coverage gap;
`format_runner_diagnostics` wraps `diagnostic_summary`
(`weft/core/runner_diagnostics.py`, Spec [CC-3.4]).

- Files to create: `tests/commands/test_diagnostics.py`
- Read first: `weft/commands/diagnostics.py` (17 lines) and
  `diagnostic_summary` in `weft/core/runner_diagnostics.py` (it joins
  `phase`, `pid`, `exitcode`, `alive`, `last_handshake`, then `message`,
  skipping `None`s).
- Test-classification requirement: every test module must carry a backend
  classification marker or collection fails with a UsageError. Use
  `pytestmark = [pytest.mark.shared]` (this test touches no broker).
- Not allowed: no mocks of any kind — this is a pure function.

- [ ] **Step 16.1** Create `tests/commands/test_diagnostics.py`:

```python
"""Tests for shared runner-diagnostic rendering helpers."""

from __future__ import annotations

import pytest

from weft.commands.diagnostics import format_runner_diagnostics

pytestmark = [pytest.mark.shared]


def test_format_runner_diagnostics_returns_none_for_empty_input() -> None:
    assert format_runner_diagnostics(None) is None
    assert format_runner_diagnostics({}) is None


def test_format_runner_diagnostics_renders_known_fields_in_order() -> None:
    summary = format_runner_diagnostics(
        {
            "phase": "spawn",
            "pid": 123,
            "exitcode": 1,
            "alive": False,
            "message": "runner exited before handshake",
            "unknown_key": "ignored",
        }
    )
    assert summary == (
        "phase=spawn, pid=123, exitcode=1, alive=False, "
        "message=runner exited before handshake"
    )


def test_format_runner_diagnostics_skips_absent_fields() -> None:
    assert format_runner_diagnostics({"pid": 7}) == "pid=7"
```

- [ ] **Step 16.2** Run red-then-green honestly: these should pass on first
  run (the code exists; the gap was coverage, not behavior). If any
  assertion fails, the rendering contract differs from this plan — read
  `diagnostic_summary` and fix the TEST's expected strings to match real
  behavior; do not change the production code in this task.

```bash
./.venv/bin/python -m pytest tests/commands/test_diagnostics.py -q
```

- [ ] **Step 16.3** Commit.

```bash
git add tests/commands/test_diagnostics.py
git commit -m "Cover shared runner-diagnostics rendering"
```

### Task 17: Tests for provider CLI project settings

**Outcome:** `weft/core/agents/provider_cli/settings.py` (load/ensure of
`.weft/agents.json`) gets real-file coverage. Source spec:
`docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-7] (cited by the
module's own docstring).

- Files to create: `tests/core/test_provider_cli_settings.py`
- Files to modify: `tests/conftest.py` — the test-audit policy test
  (`tests/specs/test_test_audit_policy.py`) requires every `tests/core`
  module to appear in the `_SHARED_MODULES` registry in `tests/conftest.py`;
  the module-level `pytestmark` alone satisfies only the collection gate,
  not the policy test. Without this registration, Task 19's full suite
  fails.
- Read first: `weft/core/agents/provider_cli/settings.py` — the module
  docstring and the four public callables; note "Missing project-local agent
  settings are treated as no project-local settings" and "Invalid settings
  ... are raised".
- Test design: REAL files in `tmp_path`, no mocks, no monkeypatch. Use the
  module's own writer (`ensure_provider_cli_project_executable`) to produce
  the file for the load test — a write/read round-trip proves the contract
  without hardcoding the JSON shape.
- These are characterization tests: if an assertion fails, read the module
  and fix the TEST to the actual contract (production behavior is not under
  review here). If the actual behavior looks wrong while doing so, stop and
  report rather than "fixing" production code in a coverage task.

- [ ] **Step 17.1** Create `tests/core/test_provider_cli_settings.py`:

```python
"""Tests for project-local delegated provider CLI settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.core.agents.provider_cli.settings import (
    ensure_provider_cli_project_executable,
    load_provider_cli_project_settings,
)

pytestmark = [pytest.mark.shared]


def test_load_without_project_settings_returns_empty(tmp_path: Path) -> None:
    settings = load_provider_cli_project_settings(
        "claude_code", spec_context=tmp_path
    )
    assert settings.executable is None


def test_ensure_then_load_round_trip(tmp_path: Path) -> None:
    (tmp_path / ".weft").mkdir()
    result = ensure_provider_cli_project_executable(
        "claude_code",
        executable="/usr/local/bin/claude",
        spec_context=tmp_path,
    )
    assert result.action == "created"
    assert result.executable == "/usr/local/bin/claude"

    settings = load_provider_cli_project_settings(
        "claude_code", spec_context=tmp_path
    )
    assert settings.executable == "/usr/local/bin/claude"


def test_ensure_preserves_existing_executable(tmp_path: Path) -> None:
    (tmp_path / ".weft").mkdir()
    ensure_provider_cli_project_executable(
        "claude_code", executable="/first", spec_context=tmp_path
    )
    result = ensure_provider_cli_project_executable(
        "claude_code", executable="/second", spec_context=tmp_path
    )
    assert result.action == "preserved"
    assert result.executable == "/first"


def test_invalid_settings_payload_raises(tmp_path: Path) -> None:
    weft_dir = tmp_path / ".weft"
    weft_dir.mkdir()
    (weft_dir / "agents.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_provider_cli_project_settings("claude_code", spec_context=tmp_path)
```

  (`agents.json` is `WEFT_AGENT_SETTINGS_FILENAME` in `weft/_constants.py`;
  if the literal in the last test ever drifts, import the constant instead.)

- [ ] **Step 17.2** Register the new module: in `tests/conftest.py`, add
  `"tests/core/test_provider_cli_settings.py",` to the `_SHARED_MODULES`
  collection, matching the existing entries' format and ordering exactly.
- [ ] **Step 17.3** Run; apply the characterization rule from the header if
  anything fails:

```bash
./.venv/bin/python -m pytest tests/core/test_provider_cli_settings.py tests/specs/test_test_audit_policy.py -q
```

- [ ] **Step 17.4** Commit.

```bash
git add tests/core/test_provider_cli_settings.py tests/conftest.py
git commit -m "Cover provider CLI project settings load and ensure paths"
```

### Task 18: State the real extension-API boundary

**Outcome:** `weft/ext.py` and the README stop implying that `weft.ext`
alone is a sufficient plugin API. Source spec: none — honest-docs fix; the
re-export/ABI design itself is explicitly out of scope (see Out of Scope).

- Files to touch: `weft/ext.py` (module docstring only), `README.md`
- Read first: `weft/ext.py` module docstring;
  `extensions/weft_docker/weft_docker/plugin.py` imports (the evidence: a
  real runner imports `weft.core.runners`, `weft.core.tasks.runner`,
  `weft.core.resource_monitor`, `weft._constants`, and more); the README
  section that introduces runner extensions (search "Runner extras" or
  "runner plugin" in README.md).

- [ ] **Step 18.1** Append to the `weft/ext.py` module docstring:

```text
Stability note: this module is the declared contract surface, but the
shipped first-party runner plugins also import `weft.core` internals that
carry no stability guarantee. Third-party runner plugins are not yet
supported at arm's length; if you build one anyway, pin an exact weft
version.
```

- [ ] **Step 18.2** In the README's runner-extension discussion, add the
  same fact in one sentence:

```text
Runner plugins are currently first-party: beyond `weft.ext`, the shipped
plugins use Weft core internals that have no stability contract yet, so
third-party runners should pin an exact weft version.
```

- [ ] **Step 18.3** Verify docstring-only change compiles and commit:

```bash
./.venv/bin/python -c "import weft.ext"
./.venv/bin/ruff check weft/ext.py
git add weft/ext.py README.md
git commit -m "Document the actual runner-plugin stability boundary"
```

### Task 19: Final gates and plan close-out

- [ ] **Step 19.1** Run the full final gates (all four must pass):

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest extensions/weft_macos_sandbox/tests -q
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

- [ ] **Step 19.2** Confirm documentation traceability landed: specs 01, 02,
  05, 07, 10, 13 each contain a `## Related Plans` link to this plan where
  Tasks 2b/4/5/6/7 added them; `tests/specs/` is green. Also confirm the
  Task 12 outcome held: `grep -rn '"WEFT_CONTEXT"' weft/` returns only the
  `_constants.py` definition — if it does not, Task 12 is unfinished and
  the plan may not close.
- [ ] **Step 19.3** Flip this plan's `Status: draft` to `Status: completed`
  and update its row in `docs/plans/README.md` to `` `completed` `` (the
  row itself already exists — only the status cell changes), then:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
git add docs/plans/
git commit -m "Mark evaluation findings remediation plan completed"
```

- [ ] **Step 19.4** Hand off: list any task you skipped or any
  stop-condition you hit, with the exact failing command output. If a
  correction during this work exposed a repeated failure mode, add a dated
  entry to `docs/lessons.md` per its "How To Add A Lesson" header.

---

## Testing Plan (summary)

| Task | Proof | Harness/fixtures | Must NOT mock |
|------|-------|------------------|---------------|
| 2 | red-green: ValidationError on `approval_required=True` | plain pytest + real Pydantic model | anything |
| 3 | red-green: stat modes on real tmp files/dirs | `tmp_path` | anything |
| 4 | red-green: `build_context` produces 0700 dirs; tighten case | `tmp_path` + real `build_context` | context/filesystem |
| 5 | red-green: real spilled file is 0600 in 0700 dir | existing real-execution spill test | the spill path (extend the real test) |
| 6 | red-green: `cmd_dump` output is 0600, loose file tightened | `tmp_path` + real `cmd_dump` | broker/dump internals |
| 7 | red-green: captured Popen env lacks host secrets, has baseline+opt-in+spec | extension test style: fake `Popen`/`run_monitored_subprocess` ONLY (real `sandbox-exec` needs macOS) | env assembly logic |
| 11-15 | no behavior change: suite slices green + greps | existing suites | n/a |
| 16-17 | new coverage on real objects/files | plain pytest, `tmp_path` | anything |

Cross-cutting rules:

- POSIX permission assertions are `@pytest.mark.skipif(sys.platform ==
  "win32", ...)` — Windows has no POSIX mode bits; the helpers are
  best-effort there by design.
- Every NEW test module must carry `pytestmark = [pytest.mark.shared]` or
  collection fails (backend-classification gate in `tests/conftest.py`).
  New `tests/core` modules must ALSO be registered in the `_SHARED_MODULES`
  list in `tests/conftest.py` — `tests/specs/test_test_audit_policy.py`
  enforces that registry beyond the marker (see Task 17.2).
- Assert with `stat.S_IMODE(path.stat().st_mode) == 0o700` (or `0o600`) —
  never compare `st_mode` raw.
- The tempting shortcut to refuse: monkeypatching `os.chmod`/`os.open` to
  "test" the helpers. The real filesystem in `tmp_path` is faster and true.
- Edge case deliberately in scope: tightening of PRE-EXISTING loose
  files/dirs (the upgrade path). Edge case deliberately out of scope:
  exotic umasks — chmod sets exact modes, so even a stricter ambient umask
  ends at exactly 0600/0700; still owner-only, which is the contract.

## Verification and Gates

Per-task verification commands are inline in each task. Final gates (Task
19.1) are the full fast suite, the macOS-sandbox extension suite, the
documented mypy invocation, and ruff. Full-suite gates are required because
Tasks 3-7 touch context construction, task spill, dump, and an extension —
surfaces exercised across many test directories.

Rollout/rollback: local library, no deployment sequencing. Each task is one
revertable commit; the two breaking changes (Tasks 2 and 7) are flagged in
CHANGELOG (Task 8) and revert cleanly in isolation. Post-merge observation:
after the next release, `ls -la ~/.weft` on an upgraded machine should show
`drwx------`, and a sandbox task relying on implicit env inheritance should
fail in an obvious way (missing variable) rather than silently leak.

## Independent Review Loop

This plan changes runtime behavior at contract boundaries (Tasks 2, 7) and
filesystem behavior on every context build (Task 4), so external review is
warranted in addition to the author's fresh-eyes pass.

- Reviewer: an agent from a different family than the author when available
  (per repo preference, e.g. Codex via the repo owner's tooling); otherwise
  a fresh zero-context session of any capable agent.
- Reviewer reads: this plan; `CLAUDE.md` §1.1 and §4;
  `docs/specifications/13-Agent_Runtime.md` [AR-0.0]/[AR-2.2];
  `weft/core/taskspec/model.py` (`AgentToolSection`);
  `weft/helpers/__init__.py`; `weft/context.py` (`build_context`);
  `weft/commands/_dump_support.py` (`cmd_dump`);
  `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`.
- Review prompt: "Read the plan at
  `docs/plans/2026-06-09-evaluation-findings-remediation-plan.md`. Carefully
  examine the plan and the associated code. Look for errors, bad ideas, and
  latent ambiguities. Don't do any implementation, but answer carefully:
  could you implement this confidently and correctly if asked?"
- Feedback handling: the author considers each point explicitly and either
  updates the plan, records why the current path stands, or records why the
  point is out of scope — in the Fresh-Eyes Review Record below.

## Out of Scope (explicitly not in this plan)

- **Manager/service registry-record interpretation consolidation.** The
  evaluation found three sites independently interpreting service/manager
  registry records (`weft/core/manager.py`, `weft/core/manager_runtime.py`,
  `weft/commands/system.py`). Consolidating into a pure shared kernel is
  leader-election-adjacent, high-consequence work that needs its own
  hardened plan — and there is active in-flight work on exactly that surface
  in `weft/commands/system.py` right now. Do not start it from this plan.
- **Double-fork containment** (workers in their own process group /
  `setsid` + `killpg`). Execution-path change with platform-divergent
  semantics (POSIX vs Windows); needs its own hardened plan per
  `docs/agent-context/runbooks/hardening-plans.md`.
- **Plugin ABI re-export design** (a `weft.ext.runtime` surface or declared
  in-tree-only policy). Owner decision; Task 18 only documents the status
  quo honestly.
- **Wiring redaction into `weft system dump`.** Deliberately rejected, not
  deferred: dump/load must round-trip verbatim (see Invariants).
- **`weft/client` test expansion** beyond current coverage; flagged in the
  evaluation as light, but scoping useful client tests needs its own pass.
- **mypy `python_version` floor alignment** (config says 3.13; package
  floor is 3.12). Changing it may surface an unknown volume of findings;
  owner decision, not bite-sized.
- **Any cleanup of `tests/core/test_manager.py` size or per-file fixture
  duplication** — drive-by refactor risk.

## Fresh-Eyes Review Record

- **2026-06-09, author pass 1** (separate re-read after drafting). Found and
  fixed: (a) Task 7's test snippet contained a placeholder for the
  `run_with_hooks` work item — resolved to the concrete `{}` the existing
  extension test uses; (b) the File Map assigned `CHANGELOG.md` to both
  Task 7 and Task 8 — consolidated all CHANGELOG edits under Task 8. Also
  verified during drafting (and dropped) two findings from the original
  evaluation that did not survive scrutiny: `docs/lessons.md` is not
  chronologically ordered anywhere, so there was no "out-of-order entry" to
  fix; and the mypy django/channels overrides are used by the CI invocation
  (`.github/workflows/test.yml` checks `integrations/weft_django` with the
  root config), so removing them would have broken CI.
- **2026-06-09, independent zero-context review** (fresh agent, full plan +
  code verification; every quoted edit target confirmed verbatim against
  the repo). 14 findings, all addressed in this revision:
  1 blocker — new `tests/core` modules must be registered in
  `tests/conftest.py::_SHARED_MODULES` or
  `tests/specs/test_test_audit_policy.py` fails (fixed: Task 17.2);
  preflight contradicted the actual dirty tree (fixed: Task 1.3/1.4 commit
  the plan first); interactive `git add -p` is un-executable by an agent
  (fixed: Task 12.3 conditional non-interactive flow); Tasks 4/6 test
  snippets bypassed `prepare_project_root` and would have broken
  Postgres-suite isolation discipline (fixed); Task 7 snippet was not
  self-contained (fixed: fakes inlined); backlink format diverged from the
  four specs' established convention (fixed); the macOS extension is
  separately versioned and needs its own bump for a breaking change (fixed:
  0.6.0 in Task 7.5); queue.py has no `_constants` import block to extend
  (fixed: new import line); helper fd-leak-on-fdopen-failure and
  chmod-vs-fchmod rationale (fixed in Task 3 code); leaf-only chmod scope
  documented; spill test named explicitly
  (`test_large_output_spills_to_disk`, variable `output_path`); Task 10.2
  no longer claims a conformance test pins the CLI table; `tmp_path: Path`
  typing made consistent; `## Unreleased` fold-in noted as a manual
  release-prep step.
- Reviewer's closing answer after fixes were scoped: implementable
  confidently once findings 1-5 were addressed; all 14 are now incorporated.
- **2026-06-09, repo-owner review** (6 findings, all verified against the
  repo and addressed):
  [P1] Task 12's dirty-file deferral could close the plan with the declared
  outcome unmet — replaced with a mandatory non-interactive stash/edit/
  commit/pop choreography (in-flight hunks verified ~17 lines clear of the
  edit point), a lossless conflict-recovery path, and a Task 19.2 gate that
  blocks close-out if the `WEFT_CONTEXT` grep still shows more than the
  constant definition.
  [P1] Spec 01 was cited as a Task 7 source but never backlinked — added
  Step 7.6 (spec 01 has an existing `## Related Plans` section at the
  bottom), added the file to the File Map and the Task 7 commit, and added
  spec 01 to the Task 19.2 traceability check.
  [P2] The logs-dir permission boundary is now an explicit, tested
  decision: default `.weft/logs` relies on its 0700 parent (test pins the
  parent mode and the placement premise); custom `WEFT_LOGS_DIR` stays
  caller-owned. Scope deliberately not widened to chmod logs itself.
  [P2] `ctx.autostart_dir` 0700 is now asserted (test passes
  `autostart=True` since the dir is only created when enabled).
  [P2] File Map now lists the extension `pyproject.toml` version bump and
  the spec 01 backlink under T7.
  [P3] Execution policy made explicit in "Current Repo State": the plan
  authorizes local commits only — no push, branches, tags, or history
  rewriting.
- External different-family review (per the Independent Review Loop
  section): not yet run; the plan remains `draft` until the repo owner
  triggers it or waives it.
