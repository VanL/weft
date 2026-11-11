# Extension PEP 561 Typing, Release Gate Parity, and Specification Synchronization Plan

Status: completed
Source specs: `docs/program-theory.md` ([WT-0], [WT-2a.2], [WT-2a.9], [WT-2a.13], [WT-3], [WT-4], [ALT-WT-018], [ALT-WT-020], [WT-7]); `docs/specifications/00-Quick_Reference.md`; `docs/specifications/05-Message_Flow_and_State.md` ([MF-5], [MF-6]); `docs/specifications/07-System_Invariants.md` ([QUEUE.8], [OBS.13.1], [OBS.13.3], [OBS.13.7], [OBS.13.13]); `docs/specifications/08-Testing_Strategy.md` ([TS-1], [TS-3], [TS-4]); `docs/specifications/09-Implementation_Plan.md` ([IP-1]); `docs/specifications/09A-Implementation_Roadmap_Planned.md` ([IP-A1]); `docs/specifications/13C-Using_Weft_With_Django.md` ([DJ-19])
Superseded by: none

Class: 5 (`implementation with spec revision` across extension PEP 561 typing and test-tree `mypy` enforcement, local release/verification gate parity, stale specification synchronization, and executable spec-pin tests).

## 1. Goal

Close the repository-wide type-checking, local release-gate parity, and specification-drift defects verified in the 2026-11-10 deep-dive review (`HEAD = dbbf5526`, `v0.9.124`): add PEP 561 `py.typed` markers to all three extension packages, add all four extension/integration `tests/` trees to `bin/mypy-check` and `bin/release.py` and fix all `23` verified live `mypy` errors (plus any surfaced once `py.typed` enables full signature checking), wire missing lint/doc gates into `bin/release.py`, synchronize stale/contradictory spec text across `00`, `05`, `07`, `09`, `09A`, and `integrations/weft_django/README.md`, and add executable pin tests for `[QUEUE.8]`'s `multiqueue_watcher.py` SHA-256 and `00-Quick_Reference.md`'s CLI subcommands.

## 2. Source Documents

- **Program Theory (`docs/program-theory.md`)**:
  - `[WT-0]` (*Specs describe what is currently in the code*)
  - `[WT-2a.2]`, `[WT-3]`, & `[ALT-WT-020]` (*Queues are the only durable truth*; `MonitorStore` is strictly private to `TaskMonitor` for external log collation/export and never queried by `status` or `terminal_snapshot`)
  - `[WT-2a.9]` (*One current contract*)
  - `[WT-2a.13]` (*Death needs proof; task-owned terminal proof outranks supervisor projection*) & `[WT-3]` (*Evidence* row: one shared, broker-free liveness policy)
  - `[WT-4]` (§4.4 *Simplifications must preserve observable capabilities and latency bounds*; shared code and meaningful correctness checks)
  - `[ALT-WT-018]` (*Tests that pin process or dependency versions* — forbids tests asserting exact dependency versions, CI step order, or `bin/release.py` step order)
- **Agent Context & Runbooks**:
  - `AGENTS.md` / `CLAUDE.md` (§7 Handoff Protocol, §8 Agent Boundaries: *"Don't do: Modify CI/CD"* — any `.github/workflows/test.yml` change requires explicit owner authorization first!)
  - `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- **Governing Specifications**:
  - `docs/specifications/08-Testing_Strategy.md` `[TS-1]`, `[TS-3]`, `[TS-4]`
  - `docs/specifications/05-Message_Flow_and_State.md` `[MF-5]`, `[MF-6]`
  - `docs/specifications/07-System_Invariants.md` `[QUEUE.8]`, `[OBS.13.1]`, `[OBS.13.3]`, `[OBS.13.7]`, `[OBS.13.13]`
  - `docs/specifications/09-Implementation_Plan.md` ([IP-1]), `docs/specifications/09A-Implementation_Roadmap_Planned.md` ([IP-A1]), `docs/specifications/00-Quick_Reference.md`, `integrations/weft_django/README.md`

## 3. Context, Verified Evidence & Key Files

1. **Missing `py.typed` markers & `23` live `mypy` errors across `11` extension/integration test files (`[TS-3]`)**:
   - Verified by running `./.venv/bin/mypy integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests --config-file pyproject.toml` on `dbbf5526`: `Found 23 errors in 11 files (checked 27 source files)` (`[untyped-decorator]`, `[attr-defined]`, `[unused-ignore]`, `[no-any-return]`). Unlike `weft` and `weft_django`, `weft_docker`, `weft_macos_sandbox`, and `weft_microsandbox` omit `py.typed`, and `bin/mypy-check` and `bin/release.py` omit the four `tests/` directories.
2. **Local release gate omissions (`bin/release.py`)**:
   - `bin/release.py` omits `bin` from `ruff check`, and omits `bin/ruff_suppression_index.py --check`, `bin/check-doc-paths`, and `bin/check-dom15-fixtures` from `BASE_PRECHECK_COMMANDS`.
   - **CI boundary (`CLAUDE.md` §8)**: `.github/workflows/test.yml` also omits the four extension/integration `tests/` paths in its `mypy` step plus `bin/check-doc-paths`, `bin/check-dom15-fixtures`, and `bin/coalesce-check`, **but modifying `.github/workflows/test.yml` is gated on explicit owner authorization** (isolated in Optional Task 2.2 below).
3. **Verified stale/contradictory specification passages**:
   - `docs/specifications/05-Message_Flow_and_State.md`: late Queue Lifecycle bullets still say `jsonl_then_delete` retains folded raw `weft.log.tasks` rows until summary emission (contradicting `[OBS.13.3]` and earlier `[MF-6]` text) and still describe `TaskMonitor` destruction protection as probing host PIDs / classifying undecidable rows directly rather than `[OBS.13.7]`'s non-probing snapshot-presence boundary (`LivenessMonitor` is the sole classifier/deleter of `weft.state.tasks.<tid>`, and terminal task-log proof never overrides current non-terminal snapshot presence).
   - `docs/specifications/07-System_Invariants.md`: `[QUEUE.8]` pins `multiqueue_watcher.py` SHA-256 at `0.9.104` instead of current `dbbf5526` (`713060f2880b401220505e82f9fad11916740a8355cad026f869ac61dfdbf086`), and `[OBS.13.13]` leaves implicit that summary invalidation fires on changed selected control-observation evidence or task-log terminal proof superseding a prior control projection (`53b4ea12`).
   - `docs/specifications/09-Implementation_Plan.md` and `integrations/weft_django/README.md`: claim `terminal_snapshot` falls back to private `MonitorStore` rows after raw task-log retirement, directly contradicting `[WT-2a.2]`, `[ALT-WT-020]`, and `[OBS.13.1]` (`weft/commands/tasks.py::task_terminal_snapshot` never reads `MonitorStore`).
   - `docs/specifications/09A-Implementation_Roadmap_Planned.md`: lists short-to-full TID resolution as unbuilt despite `weft task tid` shipping.
   - `docs/specifications/00-Quick_Reference.md`: omits `task-monitor`, `prune`, and `stop` from `weft system`.

## 4. Invariants, Constraints, Rollout, and Rollback

- **Hardening**: `N/A — no risky trigger` (static typing, local release-helper parity, and specification synchronization; zero runtime execution-path or persistence-lifecycle changes).
- **Zero `# type: ignore` additions (`[TS-3]`)**: Fix every surfaced `mypy` error through genuine type annotations and typed helper fixtures; never add `# type: ignore`.
- **No process-detail pinning (`[ALT-WT-018]`)**: Do **not** add any test asserting `bin/release.py` step order or incidental command layout; keep `tests/system/test_release_script.py` limited to order-independent `[TS-3]`/`[TS-4]` target coverage (`_suite_path(target) in mypy_command`).
- **CI boundary (`CLAUDE.md` §8)**: Do **not** touch `.github/workflows/test.yml` unless the owner explicitly confirms Optional Task 2.2.
- **Commit isolation & rollback**: Slices 1, 2, and 3 land as separate atomic commits (`git revert <commit>` cleanly reverts any slice).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| `CLAUDE.md` §8 | Optional Task 2.2 gated `.github/workflows/test.yml` edits on explicit owner confirmation. | Skipped Optional Task 2.2 (`.github/workflows/*` untouched). | Owner explicitly instructed: *"Leave the CI checks alone"*. | None. |

## Spec Baseline

- **Baseline Commit / Tag**: `dbbf5526` (`v0.9.124` on `main`, `2026-11-10`)
- **Plan Type**: `implementation with spec revision`
- **Promotion Strategy**: **Strategy D / B (atomic with pin tests in Slice 3)** across `00-Quick_Reference.md`, `05-Message_Flow_and_State.md`, `07-System_Invariants.md`, `09-Implementation_Plan.md`, `09A-Implementation_Roadmap_Planned.md`, and `integrations/weft_django/README.md`.
- **Promotion Baseline Identifier**: `Slices 1–3 atomic commits on main (2026-11-10)`

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/05-Message_Flow_and_State.md` | D — clarification/reconciliation in Slice 3 | Queue Lifecycle Management (`jsonl_then_delete` raw-row deletion `[OBS.13.3]` & non-probing `TaskMonitor` snapshot-presence boundary `[OBS.13.7]`) |
| `docs/specifications/07-System_Invariants.md` | B — atomic with `test_multiqueue_watcher_pin.py` in Slice 3 | `[QUEUE.8]` SHA-256 pin; `[OBS.13.13]` summary-invalidation scope |
| `docs/specifications/09-Implementation_Plan.md` | D — reconciliation in Slice 3 | `[IP-1]` `terminal_snapshot` broker-only reads (`[WT-2a.2]`, `[ALT-WT-020]`, `[OBS.13.1]`) |
| `integrations/weft_django/README.md` | D — reconciliation in Slice 3 | Submission Handle (`status(tid)` / `terminal_snapshot(tid)` broker-only reads) |
| `docs/specifications/09A-Implementation_Roadmap_Planned.md` | D — reconciliation in Slice 3 | `[IP-A1]` `weft task tid` note |
| `docs/specifications/00-Quick_Reference.md` | B — atomic with CLI subcommand test in Slice 3 | `weft system` CLI Surface row |

### 1. `docs/specifications/05-Message_Flow_and_State.md` (`jsonl_then_delete` `[OBS.13.3]` & `TaskMonitor` Snapshot-Presence Boundary `[OBS.13.7]` — Slice 3)

- **Exact pre-change target A (`docs/specifications/05-Message_Flow_and_State.md:2002-2013`)** (`  is ever auto-requeued.` through `  pass. A row closes its lifecycle group only when it is the task's typed`):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
  is ever auto-requeued. `weft.log.tasks` is now
  table driven under collated ownership: the monitor scans visible
  rows in FIFO order up to `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT`, folds valid
  rows into the Monitor table and, in both `delete` and `jsonl_then_delete`
  modes, deletes those exact valid raw rows once the collation update and
  raw-message reference commit durably ([OBS.13.3]); in `jsonl_then_delete`
  mode, malformed/unrecognized raw rows and inferred runtime cleanup reports
  still require accepted external emission or durable deferred-report handoff
  before destructive cleanup, while family `task_lifetime_report` emission uses
  the durable Monitor store rows. Family summaries are emitted only
  after a
  completed FIFO high-water pass, never after a scan-limited or error-limited
  pass. A row closes its lifecycle group only when it is the task's typed
```

- **Exact pre-change target B (`docs/specifications/05-Message_Flow_and_State.md:2090-2102`)** (`- terminal-family readiness gates compare message IDs` through `  vacuously.`):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
- terminal-family readiness gates compare message IDs against the store's
  durable ingest checkpoint, never against host wall-clock time: with zero
  terminal retention, a terminal family is summary-ready (and control-cleanup
  ready) once `last_message_id` is at or behind the `weft.log.tasks`
  checkpoint. Wall-clock cutoffs remain correct only for observed-staleness
  windows (stale-open classification). In `jsonl_then_delete`, folded valid raw
  `weft.log.tasks` rows may be deleted after durable store commit ([OBS.13.3]),
  while no collated Monitor-store family may be retired before that family's
  `summary_emitted_at_ns` is set. `raw_deleted_at_ns` means verified deletion:
  the exact-row apply layer re-verifies each candidate per ID when a batch
  under-deletes, so a
  still-present row is never reported deleted and families cannot be marked
  vacuously.
```

- **Exact pre-change target C1 (`docs/specifications/05-Message_Flow_and_State.md:2202-2205`)** (`  project root when a mode enables it.` through `  for raw mode.`):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
  project root when a mode enables it. It uses the same canonical exact-delete
  helper after durable table ingestion for collated `delete` and
  `jsonl_then_delete` modes (with malformed/unrecognized raw rows in
  `jsonl_then_delete` gated on accepted external emission or deferred handoff),
  and after raw external emit for raw mode.
```

- **Exact pre-change target C2 (`docs/specifications/05-Message_Flow_and_State.md:2236-2245`)** (`  — is otherwise indistinguishable` through `  evidence-model statement. PONG exposes`):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
  — is otherwise indistinguishable from an abandoned family in the task log
  alone. Whole-family destruction protection (both at `stale_open` disposal
  time and at delete time, including already-disposed families with terminal
  task-log proof — terminal proof never overrides current non-terminal snapshot
  presence) is newest valid non-terminal `weft.state.tasks.<tid>` snapshot
  presence plus live service-registry ownership (`TaskMonitor` neither
  classifies snapshots nor probes host/runtime liveness; `LivenessMonitor` is
  the sole deleter of `weft.state.tasks.<tid>`). Independently, newest
  liveness-valid snapshot presence, including terminal rows, holds standard
  `T{tid}.ctrl_out` and `T{tid}.outbox` until `LivenessMonitor` retirement
  ([MF-5]). See [OBS.13.7] for the full
  evidence-model statement. PONG exposes
```

### 2. `docs/specifications/07-System_Invariants.md` (`[QUEUE.8]` & `[OBS.13.13]` — Slice 3)

- **Exact pre-change target A (`docs/specifications/07-System_Invariants.md:237-246`)** (``_Downstream exact copy_: Taut vendors `weft/core/tasks/multiqueue_watcher.py` `` through `runtime.`):

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
_Downstream exact copy_: Taut vendors `weft/core/tasks/multiqueue_watcher.py`
whole and unmodified as its scheduler block (Taut reactor restoration plan,
S1); current Weft `dbbf5526` (0.9.124) whole-file SHA-256 is
`713060f2880b401220505e82f9fad11916740a8355cad026f869ac61dfdbf086` (verified by
`tests/specs/test_multiqueue_watcher_pin.py`). The
[QUEUE.8] contract above, including the standalone `_sigint_handler` signal-frame
rule and owner-thread mutation between dispatch passes, is therefore a contract
for that copy as well. A change to this file must update this digest and be
recorded in the CHANGELOG so Taut can re-pin and re-record its digest; Taut
does not import Weft at runtime.
```

- **Exact pre-change target B (`docs/specifications/07-System_Invariants.md:742-746`)** (`    global-log bounds. Persisted task-owned control observations outrank later` through `    finite-horizon inspection shares the existing maintenance owner and budgets;`):

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
    global-log bounds. Persisted task-owned control observations outrank later
    Manager fallback; task-log terminal proof outranks both. Changed selected
    control-observation evidence (or task-log terminal proof superseding a prior
    control projection) invalidates summary verification and requires a corrected
    report with distinct stable evidence identity before cleanup/retirement. Bounded
    finite-horizon inspection shares the existing maintenance owner and budgets;
```

### 3. `docs/specifications/09-Implementation_Plan.md`, `integrations/weft_django/README.md`, `09A-Implementation_Roadmap_Planned.md`, and `00-Quick_Reference.md` (Slice 3)

- **Exact pre-change targets**:
  - `docs/specifications/09-Implementation_Plan.md:134-141` and `line 169`
  - `integrations/weft_django/README.md:141-145`
  - `docs/specifications/09A-Implementation_Roadmap_Planned.md:38-41`
  - `docs/specifications/00-Quick_Reference.md:118`

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
- Known-TID terminal snapshots are non-consuming observations. When they include
  exact acknowledgement targets, callers must acknowledge explicitly. This
  keeps read-only observers from stealing task results or mutating queue state.
  Known-TID terminal snapshots read broker runtime/log/control/outbox surfaces
  only and never query private Monitor-store tables ([WT-2a.2], [OBS.13.1]). A
  positive `timeout` bounds the observation and returns the latest
  nonterminal snapshot on expiry; it never publishes a task timeout or consumes
  a result.
```

Replacement (`docs/specifications/09-Implementation_Plan.md`):
```markdown
| Task terminal observation | `task_handle` and `namespace` | `Task.terminal_snapshot`, `client.tasks.terminal_snapshot`, `client.tasks.ack_terminal_snapshot` | Observes known-TID terminal evidence from broker queue surfaces (never private Monitor-store tables) without consuming result queues unless explicitly acknowledged. |
```

Replacement (`integrations/weft_django/README.md`):
```markdown
Module-level `status(tid)` and `terminal_snapshot(tid)` use Weft's compact
known-TID terminal snapshot path. They are read-only observations over broker
runtime, log, control, and outbox surfaces (never private Monitor-store tables).
Use `snapshot(tid)` when callers need diagnostic fields such as task metadata,
runtime details, or timestamps.
```

Replacement (`docs/specifications/09A-Implementation_Roadmap_Planned.md`):
```markdown
- extra operator-facing discovery helpers beyond the current command surface
  (`weft task tid` already ships short-to-full TID resolution)
- deeper OS-level process integration when the current helper layer is not
  enough
```

Replacement (`docs/specifications/00-Quick_Reference.md`):
```markdown
| `weft system` | `tidy`, `dump`, `builtins`, `load`, `task-monitor`, `prune`, `stop` |
```

## 5. Tasks

### Slice 1: Extension `py.typed` Markers & Extension/Integration Test-Tree `mypy` Cleanliness (`[TS-3]`)

1. **Task 1.1: Add `py.typed` to All Three Extensions & Fix All `mypy` Errors Across Extension/Integration `tests/`**
   - **Outcome**: `weft_docker`, `weft_macos_sandbox`, and `weft_microsandbox` ship `py.typed`, and `bin/mypy-check`, `bin/release.py`, `tests/system/test_release_script.py::test_build_precheck_commands_cover_release_gate_and_quality_gates` (`_suite_path(target) in mypy_command`), and `AGENTS.md`/`CLAUDE.md` §7 Handoff Protocol type-check `integrations/weft_django/tests`, `extensions/weft_docker/tests`, `extensions/weft_macos_sandbox/tests`, and `extensions/weft_microsandbox/tests` with `0` errors.
   - **Files to touch**: `extensions/weft_docker/weft_docker/py.typed` (new), `extensions/weft_macos_sandbox/weft_macos_sandbox/py.typed` (new), `extensions/weft_microsandbox/weft_microsandbox/py.typed` (new), `bin/mypy-check`, `bin/release.py`, `tests/system/test_release_script.py`, `integrations/weft_django/tests/test_weft_django.py`, `extensions/weft_docker/tests/*`, `extensions/weft_macos_sandbox/tests/*`, `extensions/weft_microsandbox/tests/*`.
   - **Constraints**: Confirm wheel inclusion in `extensions/*/pyproject.toml`; fix annotations genuinely with zero new `# type: ignore` comments (`[TS-3]`).
   - **Done when**: `./bin/mypy-check` checks all core, integration, and extension source + test trees with `0` errors.

### Slice 2: Local Release Gate Parity (`bin/release.py`)

2. **Task 2.1: Wire Missing Lint/Doc Gates in `bin/release.py`**
   - **Outcome**: `bin/release.py` (`BASE_PRECHECK_COMMANDS`) includes `"bin"` in `ruff check` and runs `bin/ruff_suppression_index.py --check`, `bin/check-doc-paths`, and `bin/check-dom15-fixtures` alongside the existing prechecks.
   - **Files to touch**: `bin/release.py`.
   - **Constraints (`[ALT-WT-018]`)**: Do **not** add any test pinning `bin/release.py` step order or incidental command layout (`[ALT-WT-018]`).
   - **Done when**: `pytest tests/system/test_release_script.py -q` and `python bin/release.py --dry-run` pass.

3. **Task 2.2 (Owner-Gated Optional Task — Do NOT Execute Without Explicit Owner Confirmation per `CLAUDE.md` §8): Align `.github/workflows/test.yml` Gates**
   - **Owner gate**: Per `CLAUDE.md` §8 (*"Don't do: Modify CI/CD"*), skip this task unless the owner explicitly authorizes editing `.github/workflows/test.yml`.
   - **Outcome (if authorized)**: Update `.github/workflows/test.yml` to run `./bin/mypy-check` (covering the four extension/integration `tests/` trees) and add `bin/check-doc-paths`, `bin/check-dom15-fixtures`, and `bin/coalesce-check`.

### Slice 3: Specification Synchronization & Executable Pin Tests

4. **Task 3.1: Apply Spec Reconciliations & Add `[QUEUE.8]` + `00-Quick_Reference.md` Pin Tests**
   - **Outcome**: Apply all §4c replacements across `05`, `07`, `09`, `09A`, `00`, and `integrations/weft_django/README.md`, add `## Related Plans` backlinks, add `tests/specs/test_multiqueue_watcher_pin.py` verifying `[QUEUE.8]`'s SHA-256 digest against `weft/core/tasks/multiqueue_watcher.py`, and add a spec test verifying `00-Quick_Reference.md`'s CLI subcommand rows against the Typer app (`weft/cli/app.py`).
   - **Files to touch**: `docs/specifications/05-Message_Flow_and_State.md`, `docs/specifications/07-System_Invariants.md`, `docs/specifications/09-Implementation_Plan.md`, `docs/specifications/09A-Implementation_Roadmap_Planned.md`, `docs/specifications/00-Quick_Reference.md`, `integrations/weft_django/README.md`, `tests/specs/test_multiqueue_watcher_pin.py` (new), `tests/specs/test_spec_hygiene.py`.
   - **Done when**: `pytest tests/specs/ -q` and `bin/check-doc-paths` exit `0`.

## 6. Testing Plan

- `./bin/mypy-check` exercises full static type checking across all `27+` previously unchecked extension/integration test modules (`[TS-3]`); `tests/specs/test_multiqueue_watcher_pin.py` and `tests/specs/test_spec_hygiene.py` mechanically guard `[QUEUE.8]` and `00-Quick_Reference.md` against future drift.

## 7. Verification and Gates

```bash
./bin/mypy-check
./.venv/bin/ruff check weft tests bin extensions integrations
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python bin/check-doc-paths --self-test && ./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python -m pytest -m "" tests/specs/ tests/system/test_release_script.py integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests -q
```

## 8. Independent Review Loop

### 8.1 Author's `[WT-7]` Theory Fit Account

- **Verdict**: Fits with strain.
- **Rests on**:
  - `[WT-0]` (*Specs describe what is currently in the code*): reconciles stale `05`, `07`, `09`, `09A`, `00`, and `weft_django/README.md` claims with the shipped implementation and adds executable spec-pin tests so `[QUEUE.8]` and `00-Quick_Reference.md` cannot silently drift again.
  - `[WT-2a.2]`, `[WT-3]` (*Task-internal working state*), & `[ALT-WT-020]`: removes the false claim in `09-Implementation_Plan.md` and `integrations/weft_django/README.md` that `terminal_snapshot` / `status(tid)` fall back to private `MonitorStore` tables.
  - `[WT-2a.13]` & `[WT-3]` (*Evidence* row): reconciles `05-Message_Flow_and_State.md`'s destruction-protection prose with `[OBS.13.7]` (`TaskMonitor` performs no snapshot classification or host-PID probe; `LivenessMonitor` is the sole deleter of `weft.state.tasks.<tid>`, and terminal proof never overrides current non-terminal snapshot presence) and `[OBS.13.13]` summary invalidation.
- **Refuses**:
  - `[ALT-WT-020]` sidecar fallback (public `terminal_snapshot` and `weft_django` status paths never read `MonitorStore`).
  - `[ALT-WT-018]` process-detail pinning (no tests asserting `bin/release.py` step order or dependency versions).
  - Adding `# type: ignore` suppressions to silence the `23` extension/integration test `mypy` errors (`[TS-3]`), or editing `.github/workflows/test.yml` without owner permission (`CLAUDE.md` §8).
- **Predicts**:
  - `./bin/mypy-check` passes with `0` errors across `weft`, `tests`, `bin`, `weft_django` (src + tests), and all three `extensions/*` (src + tests); `terminal_snapshot` returns identical results regardless of `MonitorStore` contents; any future byte edit to `weft/core/tasks/multiqueue_watcher.py` without updating `[QUEUE.8]` fails `tests/specs/test_multiqueue_watcher_pin.py`; any new `weft system` Typer command omitted from `00-Quick_Reference.md` fails `tests/specs/test_spec_hygiene.py`.
- **Fits worst**:
  - `[ALT-WT-018]` & `[WT-7]` *Altitude collapse (implementation lock-in)*: `tests/specs/test_multiqueue_watcher_pin.py` pins exact whole-file bytes of `multiqueue_watcher.py` rather than pure behavior, so even a comment change fails the test; this strain is accepted strictly because `[QUEUE.8]` carries an explicit downstream byte-copy contract with Taut (`_Downstream exact copy_`), making the existing contract executable without widening it.

### 8.2 Blind Other-Family `[WT-7]` Fit Account & Divergence Classification

- **Reviewer family & isolated invocation (`2026-11-10`)**: OpenAI Codex CLI (`codex exec --skip-git-repo-check -s read-only -C /tmp/weft-wt7-blind-6cb1b97f/p3 -c 'model_reasoning_effort="high"'`, thread `01a1c763-b875-77b0-994a-7af5a3076445`), given **only** `program-theory.md` and `boundary.diff`.
- **Blind account summary**:
  - *Verdict*: `fits with strain` (valid 5-part account).
  - *Rests on*: `[WT-2a.2]`, `[WT-3]` (*Task-internal working state*), `[WT-5]`, and `[ALT-WT-020]` for removing `MonitorStore` from `terminal_snapshot` / Django status while keeping private collation state for export; `[WT-3]` (*Evidence*) and `[WT-2a.13]` for shared broker-free liveness policy and terminal-proof precedence; `[WT-0]` and `[WT-4]` §4.4 for correcting specs and broadening static checking.
  - *Refuses*: `[ALT-WT-020]` (querying `MonitorStore` from status/result or forcing collation tables into queues), independent `TaskMonitor` liveness classification (`[WT-3]`), treating uncertainty as death (`[WT-2a.13]`), and `[ALT-WT-018]` (tests pinning release-process details).
  - *Predicts*: `terminal_snapshot` and Django `status` return identical results whether `MonitorStore` is absent or holds conflicting rows; crashes before/after collation commit preserve raw or durable export rows; active/undecidable snapshots block destructive cleanup without `TaskMonitor` probing PIDs; watcher byte edits fail the hash pin.
  - *Fits worst*: `[WT-4]` §4.4 (*"Simplifications must preserve observable capabilities and latency bounds"*): removing `MonitorStore` fallback while deleting raw `weft.log.tasks` rows earlier could appear to reduce terminal observation capability unless remaining control/outbox queues hold across the retirement window; also noted `[ALT-WT-018]` on whole-file hash pinning.
- **Divergence classification**:
  - **Apparent capability-loss strain resolved by shipped code & `[OBS.13.7]`/`[OBS.13.9]` (blind-input boundary artifact, not a code or theory deficiency)**: Because the blind reviewer saw only the spec diff (where `09-Implementation_Plan.md` previously claimed `MonitorStore` fallback), it could not see that shipped `weft/commands/tasks.py::task_terminal_snapshot` **already** never queried `MonitorStore` (removed in `2026-10-14-task-monitor-read-separation-and-simplification-plan.md` per `[ALT-WT-020]`), and that Replacement C2 (`[OBS.13.7]` + `[MF-5]` + `[OBS.13.9]`) explicitly holds standard `T{tid}.ctrl_out` and `T{tid}.outbox` across `LivenessMonitor` snapshot presence and task-local retention windows.
  - **Change refinement applied from `[ALT-WT-018]`**: Task 2.1 explicitly forbids adding any `bin/release.py` step-order test (`[ALT-WT-018]`) and removes non-bug `test_cli_list_task.py` churn (`F9`).

### 8.3 Cross-Family Plan Review Log

- **Post-split Round 1 (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `BLOCKED` (`F6`, plus `F8` and `F9` affecting Plan 3).
  - **Dispositions (all verified against live code/specs and applied in place)**:
    - `F6` (`[P2]`, §4c.1 Replacement C2 misstated `[OBS.13.7]`'s non-probing `TaskMonitor` snapshot-presence boundary and delete-time protection of terminal-proven families): **Accept (`fixed in plan`)** — rewrote Replacement C2 (`05-Message_Flow_and_State.md:2236-2245`) to match `07-System_Invariants.md:658-676` (`[OBS.13.7]`) verbatim: whole-family destruction protection at both `stale_open` disposal time and delete time (including already-disposed families with terminal task-log proof; terminal proof never overrides current non-terminal snapshot presence) is newest valid non-terminal `weft.state.tasks.<tid>` snapshot presence plus live service-registry ownership (`TaskMonitor` neither classifies snapshots nor probes host/runtime liveness; `LivenessMonitor` is the sole deleter), while newest liveness-valid snapshot presence including terminal rows independently holds `T{tid}.ctrl_out` and `T{tid}.outbox` until `LivenessMonitor` retirement (`[MF-5]`).
    - `F8` (`[P3]`, `[ALT-WT-020]` vs `[OBS.13.7]` citation precision): **Accept (`fixed in plan`)** — scoped `[ALT-WT-020]` strictly to `MonitorStore` privacy (`09-Implementation_Plan.md`, `weft_django/README.md`) and cited `[OBS.13.7]` / `[WT-2a.13]` / `[WT-3]` for `TaskMonitor`'s non-probing snapshot-presence boundary.
    - `F9` (`[P3]`, unnecessary `tests/cli/test_cli_list_task.py` edit): **Accept (`fixed in plan`)** — removed `test_cli_list_task.py` from §3, Task 2.1, and §7, and explicitly forbade `bin/release.py` step-order tests per `[ALT-WT-018]`.
- **Post-split Round 2 (`2026-11-10`)**: Caught that an interrupted file write left Plan 3's accepted `F6`/`F8`/`F9` and `R2-1` §8.2 blind account uncommitted on disk; committed to disk and re-verified in Round 3.
- **Post-split Round 3 Scoped Verification (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -C /Users/van/Developer/weft resume -c 'sandbox_mode="read-only"' -c 'model_reasoning_effort="high"' 01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `PASS` (`F6`, `F8`, `F9`, `R2-1`, and §8.1/§8.2 `[WT-7]` fit accounts verified aligned with `[OBS.13.7]` and `docs/program-theory.md`; zero open findings).

### 8.4 Post-Implementation Completion Review

- **Reviewer family**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c89d-df4b-7d72-8d07-a36619f2a3d6`, `2026-11-10`).
- **Round 1 dispositions (all verified `RED -> GREEN` and applied in Slice 3)**:
  - Nested subcommand coverage in `tests/specs/test_spec_hygiene.py`: expanded `_typer_cli_surface()` and `_parse_quick_reference_cli_surface()` to recursively inspect nested `typer.core.TyperGroup` subcommands (`weft queue alias add/list/remove`) rather than collapsing `alias` to a leaf command, and added `test_quick_reference_cli_surface_detects_nested_subcommand_drift`.
  - Bidirectional plan backlinks: added `## Related Plans` links to `integrations/weft_django/README.md` and `docs/specifications/09A-Implementation_Roadmap_Planned.md`.
  - Formatting: applied `ruff format` to `tests/specs/test_spec_hygiene.py`.
- **Round 2 verification**: Resumed thread `01a1c89d-df4b-7d72-8d07-a36619f2a3d6` over the updated diff and test evidence (`15 passed` in `tests/specs/`, wheel `py.typed` checks, `mypy-check` across `533` files, `ruff`, `check-doc-paths`, `check-dom15-fixtures`, and `release.py --dry-run`); zero remaining findings.
- Final Completion Review Verdict: APPROVE

## 9. Out of Scope

- Runtime bug fixes in core `weft/` or extensions (covered by the two companion bug-remediation plans).
- Unapproved edits to `.github/workflows/test.yml` unless the owner explicitly authorizes Task 2.2.

## 10. Fresh-Eyes Review

- Completed post-split author fresh-eyes pass and Round-1/2/3 disposition audit (`2026-11-10`): re-ran `mypy` across the 4 extension/integration `tests/` trees to confirm `23` errors in `11` files, aligned Replacement C2 word-for-word with `[OBS.13.7]`, enforced `[ALT-WT-018]` on `bin/release.py`, isolated `.github/workflows/test.yml` behind an explicit `CLAUDE.md` §8 owner gate, and verified all 11 `Replacement (...)` blocks byte-for-byte against `HEAD = dbbf5526`.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-10 | `dbbf5526` (post-split R1–R3) | §2, §3, §4, §4c.1 C2, Task 2.1, §7, §8 | Resolved Codex `F6`, `F8`, `F9`, `R2-1`, enforced `[ALT-WT-018]` on `bin/release.py`, and recorded blind `[WT-7]` fit account (`01a1c763-b875-77b0-994a-7af5a3076445`). | Completed (`Verdict: PASS` on `01a1c763-c263-7b31-96a5-17058e503b35`) |
| 2026-11-10 | Slices 1–3 (`ff5574b1`, `9db9fad8`, Slice 3) | Deviation Log, Spec Baseline, §8.4 | Recorded owner skip of Optional Task 2.2, resolved Codex completion-review Round 1 items (`01a1c89d-df4b-7d72-8d07-a36619f2a3d6`), and closed plan after Round 2 approval. | Completed (`APPROVE` on `01a1c89d-df4b-7d72-8d07-a36619f2a3d6`) |
