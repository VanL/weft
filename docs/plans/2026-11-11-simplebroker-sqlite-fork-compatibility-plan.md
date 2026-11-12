# SimpleBroker SQLite Fork Compatibility Preparation

Status: completed
Source specs: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-13.2], [DJ-17.3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
Superseded by: none

Class: 5

Plan type: implementation with spec revision. This is a companion compatibility
preparation and qualification slice. During pre-release candidate qualification
it is not a release, a dependency-floor change, or authorization to change the
original Weft checkout; the landing change does carry the floor and lock bump
(merge rule, Spec Baseline). The coordinating repository
is SimpleBroker, plan `2026-11-11-runtime-boundaries-and-failure-handling-plan.md`,
D1 and Task 7. Its source baseline is `75616556`; exact candidate source and test
commands belong in this plan's execution evidence when qualification runs.

## Goal

Remove the unconditional promise that a retained client can recover SQLite use
in a child after a caller forks with a live parent connection. Preserve Weft's
child-local client bookkeeping and its propagation of SimpleBroker refusal.
Prove the safe close-before-fork path and the ordinary spawn runtime path with
real broker and process tests. Prepare this change for coordinated adoption of
the SimpleBroker fix; do not invent a released dependency version.

## Source Documents and Required Reading

Read `AGENTS.md`, `docs/agent-context/context.index.yaml`,
`docs/program-theory.md` [WT-2a.5], [WT-2a.8], [WT-5], [WT-7],
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`, and the source specs above.
Read `weft/client/_client.py`, `weft/commands/submission.py`,
`integrations/weft_django/weft_django/lifecycle.py`, and their test files below.
There is no local module theory for these modules.

Comprehension questions and expected answers:

1. Does clearing an inherited Python session repair SQLite's inherited native
   state? No. SimpleBroker owns that boundary; new sessions cannot bypass it.
2. Does Weft task launch use caller fork? No. Its runtime uses a fresh spawn
   image. The changed boundary is external embedding, including Django hosts.
3. Who decides whether a child may open SQLite? SimpleBroker. Weft preserves
   cleanup custody and propagates backend errors, without a fallback.

## Spec Baseline

`00d929e942812e78ae92c7339c2ed0f86391b407` is the Weft spec and source baseline.
The baseline promises child-owned bounded use after inherited-session close in
[PY-1], [PY-3], and [DJ-13.2]. Tests currently fork after real retained SQLite
submission and assert child submission succeeds, keeping the parent open.

Promotion strategy: B, atomic text and compatibility tests. Existing ownership
mappings remain valid because Weft already propagates the dependency error.
Promotion Baseline Identifier: D1-D5 were applied on 2026-11-11 in the working
tree over `2cbbfd4e`, in the same uncommitted change as the tests and the
floor bump; the landing commit is the durable identifier. No code slice
preceded the plan reviews.

Merge rule: the preparation branch is not mergeable. Weft is locked to a
SimpleBroker release without the guard (`pyproject.toml` floor and `uv.lock`
at 8.8.1 at this baseline), so promoted D1-D5 and refusal-required tests would
be red against the lock and would describe behavior the locked dependency does
not have. One landing change carries D1-D5, the tests, and the dependency floor
and lock bump to the real released SimpleBroker containing the guard. There is
no earlier separate landing of any of the three, and no test is gated on the
SimpleBroker version: [WT-2a.9] and [ALT-WT-018] refuse that compatibility code.
Qualification is re-run against the installed released distribution before
that change lands.

## Current Structure and Hidden Couplings

`WeftClient._reset_after_fork()` calls public `BrokerSession.close()` and clears
its inherited session/queue ownership before selecting bounded or retained
child state. Django `_guard_process()` replaces PID, lock, generation, and local
storage before closing only the inherited current-owner client. Neither path
catches a subsequent submission `RuntimeError` or retries through another
runner. Keep those owners and runtime paths unchanged unless a failing real
probe disproves this account.

SQLite native state is process-wide, so closing only the client is insufficient
if another SQLite owner remains open. A Django ORM SQLite connection is also a
SQLite lifetime even though SimpleBroker cannot detect arbitrary raw handles.
The held-registry-lock probe must retain its synchronization because replacing
inherited Python locks is independently required even when SQLite is refused.
The test dependency source must be selected explicitly in both pytest workers
and their subprocesses; installed editable dependencies must not select a
mutable lane accidentally.

## Invariants and Difference Register

| Boundary | Disposition |
| --- | --- |
| Inherited client/session abandonment | Preserve public child-safe detach; no inherited native cleanup guarantee |
| Live parent SQLite handle at caller fork | Narrow support; propagate detectable process-wide RuntimeError |
| Fully closed SQLite resources before fork | Preserve real child bounded submission and explicit re-entry |
| Django registry lock replacement before access | Preserve even when submission is refused |
| Parent after child refusal | Preserve parent usability and its retained ownership |
| Weft runtime process launch | Preserve spawn and existing Config transport |
| PostgreSQL/Redis | No new SQLite prohibition or backend recovery promise |
| PreparedSubmission retained/bounded selection | Preserve selection, qualify by the same backend boundary |

No runtime workaround, fork hook, process-launch change, raw-SQLite guard,
dependency pin/floor/lock change before the landing change, or
suspended-generator support is in scope. At landing the floor moves in
`pyproject.toml`, the integration and extension manifests, the lock, and the
[SB-0.1] minimum-version sentence in `04-SimpleBroker_Integration.md`.

## Proposed Spec Delta

### D1: [PY-1] inherited-client paragraph

Replace only the span beginning “A forked child first abandons” and ending
“handles remain usable after close.” Preserve the preceding thread/custody
requirements and the following exception-priority paragraph.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```text
A forked child first abandons the inherited handle through SimpleBroker's public
child-safe `BrokerSession.close()` path, then selects child-owned bounded state
unless explicitly re-entered in the child. This resets Python ownership only;
backend operations remain subject to SimpleBroker's process boundaries. Built-in
SQLite use is unsupported if any SQLite connection was open, opening, or closing
at fork. When SimpleBroker detects one of its managed lifetimes at a Python
fork, later managed SQLite operations in that child raise `RuntimeError`.
The refusal is process-wide: it covers other Weft roots and targets in that
child and every descendant of it, and creating or re-entering a client cannot
repair that process. Close all SQLite
resources before fork, or use spawn/exec or a clean forkserver. The guard does
not make inherited SQLite finalization or interpreter shutdown safe and cannot
detect arbitrary raw SQLite connections. Weft propagates the refusal without
reconnecting around it. Successful client close is idempotent and returns the
client to bounded behavior. Already returned `Task` and `PreparedSubmission`
handles remain usable after close subject to these backend process boundaries.
```

### D2: [PY-3] prepared submission ownership

Replace only the sentence beginning “After scope exit or in a forked child” and
ending “fork-inherited session.” Keep captured-context and bounded ownership.

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```text
After scope exit or in a forked child it selects bounded ownership with its
captured context and never borrows a fork-inherited session. That selection does
not bypass the SQLite process boundary in [PY-1]; a refused child submission
propagates the backend `RuntimeError`.
```

### D3: [DJ-13.2] registry process reset

Replace only the final sentence beginning “The child then closes only” and
ending “vanished parent threads.” Keep the preceding PID-before-lock rule.

Replacement (`docs/specifications/13C-Using_Weft_With_Django.md`):

```text
The child then closes only the fork-surviving current context's inherited
session through the public SimpleBroker path and creates child-owned Python
state; it never traverses vanished parent threads. This does not make inherited
SQLite state safe. The [PY-1] SQLite process boundary applies to Django-facing
submissions, including prepared on-commit callbacks; backend refusal propagates
without a reconnect fallback. Close all SQLite resources before host-process
fork, or use a fresh process image. Closing only the Weft client is insufficient
when another SQLite owner, including Django's ORM, remains open. Django registry
reset does not promise safe inherited native-resource finalization or normal
child interpreter shutdown.
```

### D4: [DJ-17.3] test expectations

Replace “fork replacement” in explicit-client lifecycle with “safe pre-fork
close and unsafe SQLite fork refusal”. Replace the process inheritance bullet:

Replacement (`docs/specifications/13C-Using_Weft_With_Django.md`):

```text
- process inheritance: prove a child replaces inherited registry locks and
  owner-local state before use without changing the parent's resources; for
  built-in SQLite prove refusal after a live parent handle crosses fork and
  successful child submission when all SQLite resources closed before fork
```

### D5: [SB-0.4] session ownership

Insert after the paragraph ending “shutdown after the last lease closes.”

Replacement (`docs/specifications/04-SimpleBroker_Integration.md`):

```text
Child-safe inherited-handle close is Python ownership detachment, not repair of
SQLite state inherited across fork. Built-in SQLite operations follow the
process boundary in [PY-1]: a caller must close all SQLite resources before
fork or use a fresh process image. Weft propagates SimpleBroker's detected-fork
`RuntimeError` without replacing the runner to evade it. Weft's own task runtime
continues to use spawn. This compatibility rule adds no SQLite restriction to
PostgreSQL or Redis and does not extend their inherited-resource contracts.
```

## Tasks and Stop Gates

1. Review this plan and exact delta independently across agent families. Before
   promotion, run the existing real Django held-lock fork probe against the
   clean SimpleBroker baseline to establish that the described route executes.
   Stop if Weft intercepts the backend error or requires a runtime repair.
2. Promote D1-D5 and update related-plan backlinks, Django README host-fork
   guidance, and an Unreleased changelog entry. Parameterize the existing real
   fork probes in `tests/helpers/client_fork_probe.py`,
   `tests/core/test_client.py`,
   `integrations/weft_django/tests/fixture_project/fixture_project/lifecycle_fork_probe.py`,
   and `integrations/weft_django/tests/test_lifecycle.py` for live-parent refusal
   and all-parent-resources-closed success. These tests are shared across SQLite
   and PostgreSQL CI: require refusal only for built-in SQLite with a live parent
   handle; preserve the existing PostgreSQL successful child path in both modes.
   Assert the exact refusal boundary: client lookup succeeds and the first
   submission raises `RuntimeError`. Context construction opens no SQLite
   connection when the database file already exists (`weft/context.py`
   `_ensure_database` returns early), so an earlier failure is a failure for
   the wrong reason and fails the test. On the refusal arm the Django probe's
   payload carries `child_replaced_lock`, `child_closed_inherited`, and the
   error type and message, so the held-lock proof and inherited-session close
   are pinned there too; the sibling thread keeps holding the old lock until
   after `waitpid` in both arms. The safe arm closes through public paths only
   (`client.close()` or `request_finished_receiver`); it does not call
   `gc.collect()` or any private close. If the candidate refuses on the safe
   arm, stop and report it as a Weft explicit-close ownership finding; do not
   patch the probe. Durable rows are counted by a fresh observer after every
   process has closed: an acknowledged task id is not evidence of storage (the
   baseline acknowledged 20 and stored 1). Keep OS fork, SQLite, real
   submission, and Django registry code real. Only manager startup remains suppressed, as
   in the existing probes, because execution is not the claim. Exercise both
   bounded and explicitly re-entered child client routes; assert RuntimeError
   diagnostics, no child accepted spawn row on refusal, durable parent rows,
   parent continued use, and successful safe-path child rows. Keep subprocess
   timeouts and os._exit teardown: normal inherited SQLite shutdown is outside
   the contract. Prove RED against clean baseline core for the intended missing
   refusal, not an import/setup error. Stop if tests need mocks inside storage
   or if guard behavior differs from the reviewed SimpleBroker D1. If the
   probes' exception handling changes, update `docs/ruff-suppression-registry.md`
   (RUFF-SUP-378 counts `BLE001` there). The changelog has no `Unreleased`
   section at this baseline; this task creates it.
3. After the coordinating agent signals that the integrated SimpleBroker
   candidate is frozen, qualify GREEN against its source; never point tests at
   a lane under edit. Run the focused client, submission-facade, multi-queue
   watcher, and Django lifecycle modules plus the existing fresh-spawn Config
   transport control. Repeat the shared fork controls on PostgreSQL with the
   installed pinned extension, using the direct invocation in Verification. Run doc, lint, format,
   and changed-file type gates. Get independent final review of the exact diff.
   Stop at reviewable qualification. Landing follows the merge rule in Spec
   Baseline: one owner-authorized change with the floor and lock bump.

## Verification and Test Value

The existing positive-only fork probes conceal the unsafe lifetime. The new
parameterized refusal cases protect propagation and absence of an accepted
child write; the safe cases protect usable close-before-fork behavior. The
Django case independently protects registry reset while a vanished sibling
thread owned its old lock. No new production test seam is needed.

Use the repo virtualenv without synchronization. In this supervised isolated
checkout `.envrc` is absent; use the existing `.venv` link and explicit
`PYTHONPATH` containing the selected SimpleBroker core source, Weft checkout,
and Django integration source. Do not override extension source: qualification
uses the pinned installed `simplebroker-pg` distribution in Weft's environment
(4.8.1 at this baseline), not the concurrent PostgreSQL implementation lane.
Do not reduce configured xdist parallelism.

Import provenance is recorded from inside each run, not from the outer shell.
Both fork probes add to their JSON payload, in the parent and in the forked
child, `simplebroker.__file__` and `simplebroker.__version__`, and on the
PostgreSQL lane `simplebroker_pg.__file__` and its version. The committed
tests assert only that the parent and child values equal what the pytest
process itself imported, so the same assertion holds for a candidate on
`PYTHONPATH` and for the installed release at landing. Whether those values
are the intended candidate and the pinned extension is a qualification-record
check, made from the recorded payload; any other value stops qualification.

`bin/pytest-pg` is not used for this qualification. It accepts only positional
test paths (so `-k fork` is a usage error), and it runs pytest through
`uv run --with-editable . --with simplebroker-pg[dev]`, an overlay resolved at
run time that `UV_NO_SYNC` does not pin. The PostgreSQL controls run directly
with the repo interpreter against an explicit DSN for a disposable database,
selecting the two fork tests by node id. The test helper writes a passwordless
target into the project config, so the disposable database either uses local
trust authentication or the run also exports `WEFT_BACKEND_PASSWORD` and
`BROKER_BACKEND_PASSWORD`, as the wrapper does.

Canonical commands after environment selection:

```bash
./.venv/bin/python -m pytest tests/core/test_client.py tests/core/test_client_submission_facade.py tests/tasks/test_multiqueue_watcher.py integrations/weft_django/tests/test_lifecycle.py tests/system/test_config_transport.py
BROKER_TEST_BACKEND=postgres WEFT_PG_TEST_DSN="$DISPOSABLE_PG_DSN" ./.venv/bin/python -m pytest tests/core/test_client.py::test_client_fork_reset_uses_child_bounded_state integrations/weft_django/tests/test_lifecycle.py::test_real_fork_replaces_held_parent_lock_before_registry_access
./.venv/bin/python -m pytest tests/specs/
./.venv/bin/ruff check tests/helpers/client_fork_probe.py tests/core/test_client.py integrations/weft_django/tests/test_lifecycle.py integrations/weft_django/tests/fixture_project/fixture_project/lifecycle_fork_probe.py
./.venv/bin/ruff format --check tests/helpers/client_fork_probe.py tests/core/test_client.py integrations/weft_django/tests/test_lifecycle.py integrations/weft_django/tests/fixture_project/fixture_project/lifecycle_fork_probe.py
./.venv/bin/mypy tests/helpers/client_fork_probe.py tests/core/test_client.py integrations/weft_django/tests/test_lifecycle.py integrations/weft_django/tests/fixture_project/fixture_project/lifecycle_fork_probe.py --config-file pyproject.toml
./.venv/bin/python bin/check-doc-paths
git diff --check
```

## Rollout, Rollback, and Success Signals

This slice prepares documentation and executable compatibility proof only.
Do not publish it as evidence that the existing declared dependency floor
contains a guard. Before coordinated release, select the actual released
SimpleBroker containing D1, put the dependency floor and lock bump in the same
landing change as D1-D5 and the tests (merge rule, Spec Baseline), and repeat
qualification with that installed distribution. There is no data migration or
new one-way door here. Revert the companion docs/tests to reverse preparation;
do not recommend reverting the SQLite safety refusal to restore unsafe use.
Success is a propagated refusal before a child spawn row is accepted, continued
parent use, durable safe-path child submission, and an unchanged fresh-spawn
runtime. A passing candidate-source run is not released-package qualification.

## Deviation Log

| Date | Proposed deviation | Status |
| --- | --- | --- |
| 2026-11-11 | Narrow inherited-client success to the supported SQLite process boundary | promoted from D1-D5 by owner direction |
| 2026-11-11 | Qualify against a frozen SimpleBroker candidate source before any release exists | qualified against the installed released SimpleBroker 8.8.2 and `simplebroker-pg` 4.8.2 instead, because that release was published first; RED still uses the 8.8.1 source |
| 2026-11-11 | Probes record `simplebroker.__file__` | the origin comes from the import spec, because the import-boundary test forbids non-exported SimpleBroker attributes; `simplebroker-pg` has no `__version__`, so its version is read from distribution metadata |
| 2026-11-11 | [SB-0.1] minimum-version sentence moves with the floor | changed from 8.6.0 / 4.6.0 to 8.8.2 / 4.8.2; the sentence had not followed the earlier floor changes |

## Review Log

### Theory Fit

- Verdict: fits.
- Rests on: [WT-2a.5] fresh runtime process images and [WT-2a.8] queue mechanics
  owned by SimpleBroker. Weft retains task orchestration ownership.
- Refuses: [WT-5] rejects owning queue mechanics; [WT-2a.5] and [WT-2a.8] refuse
  a reconnect fallback or a second launch path. Neither is introduced.
- Predicts: without any Weft runtime edit, the same real Django path will
  propagate SimpleBroker refusal, while pre-fork close and spawn succeed. A
  swallowed error or required Weft storage workaround falsifies this fit.
- Fits worst: existing child-owned state language sounds like recovery. The
  narrower promise separates Python bookkeeping from usable backend state,
  which adds a caveat to the client surface but exposes its actual owner.

### Reviews

Fresh-eyes self-review: the scope preserves registry lock replacement and the
parent lifetime; process-wide SQLite hazards include unrelated ORM handles.
The tests must not claim normal child shutdown safety. No runtime change is
justified by the read-only trace. Cross-family plan review is required before
promotion and is coordinated by the parent agent; final review is required
before completion.

Plan review 1, 2026-11-11: Anthropic Claude 2.1.293, read-only tool inventory,
on the OpenAI Codex draft. Verdict REQUEST CHANGES. P1-1 (the PostgreSQL
wrapper command could not run and its extension source was an unpinned
overlay) and P1-2 (no merge rule; the landing was not atomic) accepted. P2-3
(held-lock proof on the refusal arm), P2-4 (exact refusal boundary) and P2-5
(safe arm and durability witnesses) accepted. Optional notes on D1 wording,
the suppression registry, the changelog section and theory citations accepted.
The type-checking and watcher-retry documentation notes were not adopted: the
probes use no new SimpleBroker API, and retry timing is SimpleBroker's contract.

Plan review 2, 2026-11-11: Google Antigravity (`agy` 1.5.5,
gemini-4-argon-medium, plan mode, native reads only) on the revision finished
by Anthropic Claude after the Codex session ended. Verdict PASS WITH FINDINGS;
every finding of review 1 confirmed resolved, anchors and node ids confirmed.
W1 accepted: Theory Fit now sits under this section, where the completion gate
reads it. W2 accepted: the direct PostgreSQL command states its password
requirement. W3 accepted: committed tests compare probe provenance with the
pytest process's own imports, not with a candidate path. W4 accepted: scope
wording distinguishes preparation from the landing change, and the
version-gating refusal cites [WT-2a.9] and [ALT-WT-018].

Owner direction on 2026-11-11 to carry out this plan adopts D1-D5.

### Completion Review

- Reviewer family: Google (Antigravity `agy` 1.5.6, gemini-4-argon-medium)
- Author family: OpenAI Codex (draft), Anthropic Claude (revision and
  implementation)
- Invocation: `agy --mode plan --sandbox --disable-slash-commands --model
  gemini-4-argon-medium --output-format json --print-timeout 900s`, stdin
  closed, native reads only; exit 0, status SUCCESS, no denied actions,
  275.5s; working-tree status unchanged across the run.
- Scope: the complete uncommitted change (spec deltas, both probes and tests,
  README, changelog, floor bump, this plan).
- Findings: F1 [P2], the full-suite line in Execution Evidence was still a
  placeholder and the completion record was not yet written; both are done
  here. F2 [P3], the `RUFF-SUP-378` registry row named the two fork tests by
  earlier names; corrected. The reviewer confirmed D1-D5 match the promoted
  text, found no remaining spec promise of SQLite use after a live parent
  connection crosses fork, and judged every arm non-vacuous.

Final Completion Review Verdict: APPROVE

## Execution Evidence

Author comprehension answers: the public session close detaches Python
ownership; it cannot repair SQLite C bookkeeping. Weft launches with spawn.
SimpleBroker owns admission; Weft must let the refusal escape. The baseline
read-only source trace found no catch/reconnect path in either client reset or
Django registry reset. Baseline execution before promotion: the existing client and Django fork tests
passed (2 tests), and plan metadata passed (12 tests), at configured default
parallelism. A separate real Django request-lifecycle probe acknowledged 20
child submissions but retained only 1 after parent request finish closed its
retained lease. No errors occurred. The probe used Python 3.14 and the clean
SimpleBroker source baseline. The retained-session SQLite exposure is therefore
confirmed through Django, not inferred from the core client alone.

Release status, 2026-11-11: SimpleBroker 8.8.2 and `simplebroker-pg` 4.8.2
were published with the D1 guard (tag commit `984ce3ca` in the SimpleBroker
repository). The real released version the merge rule waits for therefore
exists: the landing change raises the floor and lock to 8.8.2 / 4.8.2.

Candidate smoke before that release, SimpleBroker source overlaid on
`PYTHONPATH`, Weft sources unmodified, import paths verified inside the run:
the default suite reported 6986 passed, 51 skipped and 1 failed; the focused
client, submission-facade, multi-queue watcher, Django lifecycle and
spawn-transport modules reported 232 passed, 2 skipped and 2 failed. The
failures were exactly
`tests/core/test_client.py::test_client_fork_reset_uses_child_bounded_state`
and
`integrations/weft_django/tests/test_lifecycle.py::test_real_fork_replaces_held_parent_lock_before_registry_access`,
each with the propagated `SQLite access is unsafe after fork` error. Weft
neither caught nor retried around the refusal. Those two tests fail against
8.8.2 until Task 2 rewrites them. This was a source-overlay smoke, not
qualification against the installed release.

Implementation, 2026-11-11 (Anthropic Claude, after the owner directed the plan
to be carried out). No runtime module changed: `weft/client/_client.py` and
`integrations/weft_django/weft_django/lifecycle.py` are untouched, as predicted
in Theory Fit.

- Specs: D1-D5 applied to `14-Python_API_Surfaces.md` ([PY-1], [PY-3]),
  `13C-Using_Weft_With_Django.md` ([DJ-13.2], [DJ-17.3]) and
  `04-SimpleBroker_Integration.md` ([SB-0.4], [SB-0.1] floors), each with a
  backlink to this plan.
- Tests: `tests/helpers/client_fork_probe.py` and the Django
  `lifecycle_fork_probe.py` take a parent mode, `live` or `closed`; the two
  fork tests keep their names and are parameterized over both. Each arm
  asserts the probe's and the forked child's broker provenance against the
  pytest process's own imports, the inherited-session close, and durable rows
  read by a fresh client after every process closed. The Django arms also
  assert the replaced registry lock while a sibling thread holds the old one
  until the child is reaped, and that lookup succeeds before the submission
  outcome. Refusal is required only for built-in SQLite with a live parent
  handle; PostgreSQL succeeds in both modes.
- Docs: Django README host-fork guidance; a new `Unreleased` changelog section
  covering the floors and the behavior change. The suppression registry is
  unchanged: both probes keep one catch-all in `main`.

Evidence (macOS 27.0.1 arm64, CPython 3.14.9, default xdist):

- RED, SimpleBroker 8.8.1 source on `PYTHONPATH` (provenance asserted as that
  source): both `live` arms fail because the child's submission is
  acknowledged where refusal is required; both `closed` arms pass.
- GREEN, installed SimpleBroker 8.8.2: the four fork cases pass on SQLite.
  The safe arm did not refuse, so public close leaves the process clean.
- PostgreSQL, disposable `postgres:18` container, direct node-id invocation
  with `WEFT_PG_TEST_DSN` and the backend password variables, installed
  `simplebroker-pg` 4.8.2: 4 passed; the child succeeds in both modes.
- Focused modules (client, submission facade, multi-queue watcher, Django
  lifecycle, spawn config transport): 236 passed, 2 skipped (PostgreSQL-only).
- Full default suite against installed 8.8.2: 6988 passed, 51 skipped. Its
  first run had one failure, the import-boundary test rejecting
  `simplebroker.__file__` in the probe (see Deviation Log); the rerun after
  that fix is the result recorded here. Django integration suite: 166 passed,
  1 skipped.
- `tests/specs` and `tests/architecture` pass; `ruff check`, `ruff format
  --check` and mypy pass on the four changed test files; `bin/check-doc-paths`
  and `git diff --check` pass.
