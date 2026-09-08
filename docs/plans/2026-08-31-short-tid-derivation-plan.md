# Short TID Derivation Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5]; docs/specifications/10-CLI_Interface.md [CLI-1.2.3]
Superseded by: none

Class: 5 — changes the normative short-form derivation in [OBS.5]; touches
every short-form producer and the process-title matcher, so hardening
applies. Plan type: implementation with spec revision. Promotion strategy:
**B — atomic** (the helper body, the [OBS.5] text, fixtures, and backlinks
land in one slice; there is no meaningful intermediate state). Depends on
[2026-08-31-registry-custody-contracts-plan.md](./2026-08-31-registry-custody-contracts-plan.md)
task 7 having introduced `tid_short_form` as the single owner with today's
formula. Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

Van, 2026-08-31: fold the logical counter into the short TID so the twelve
counter bits stop wasting ten bits of the ten-digit space. Split out of the
registry custody plan on Codex's recommendation so its promotion is
independently reviewable and revertible.

Verified basis (`simplebroker/_timestamp.py::_encode_hybrid_timestamp`,
`_constants.py`): a TID keeps `time.time_ns()` magnitude with the **low 12
bits cleared and replaced by the logical counter**, i.e. `(ns & ~0xFFF) |
counter`; `tid >> 12` counts 4,096-nanosecond grains, `tid & 0xFFF` is the
within-grain counter. Today's short form `tid[-10:]` is the TID mod 10¹⁰;
because 4096 and 10¹⁰ share a factor of 1024, counter-zero TIDs (the
common case) occupy only ≈ 9.77 million residues — even odds of a
collision at ~3,700 distinct full TIDs under independent uniform sampling
of residues (republication of one full TID is not another collision
candidate), and two
counter-zero TIDs collide when their values differ by a multiple of
4 × 10¹⁰ ns (40 s) to the grain. Today's form does distinguish all 4,096
counters within a grain; the new form must too.

**New derivation:** `grain = tid >> 12`, `counter = tid & 0xFFF`,
`short = f"{(grain + counter × 2_441_406) % 10**10:010d}"`, with
`2_441_406 = ⌊10¹⁰ / 4096⌋`. Counter-zero TIDs use the full 10¹⁰ space
(even odds at ~118,000 distinct full TIDs under that same sampling
assumption); all 4,096 counters stay distinct within a
grain (max offset 4095 × 2,441,406 = 9,997,557,570 < 10¹⁰); a burst TID
collides with a counter-zero TID when the latter's physical grain differs
by `counter × 9_999_998_976` nanoseconds **modulo 40,960 seconds**. This
includes offsets in either temporal direction after wraparound. The
10⁻¹⁰ per-pair probability assumes independent uniform residues; the
formula is deterministic, and correlated or periodic task schedules need
not follow that model. Compared with the old counter-zero form, the
residue space grows 1,024-fold, its uniform per-pair collision probability
falls 1,024-fold, and the population at equal birthday-collision
probability grows approximately 32-fold. Zero-padded to
ten characters explicitly ([OBS.5] says ten digits; `% 10**10` alone can
yield fewer).

## 2. Source Documents

- 07 [OBS.5] (~:255): "TID short form uses the low-order digits from the
  resolved 19-digit TID" — replaced. [OBS.4]: process titles
  `weft-{context_short}-{tid_short}:…` — format unchanged, digits change.
- 10 [CLI-1.2.3]: the ambiguity rule (promoted by the registry custody
  plan) — unchanged; the error simply fires less often.
- `weft/_constants.py::TASKSPEC_TID_SHORT_LENGTH` (:79) docstring says
  "last N digits" — update.

## 3. Context and Key Files

- `weft/helpers/__init__.py::tid_short_form` — the only body that changes
  (introduced by the registry custody plan with today's formula).
- Producers and the matcher, all already calling the helper after that
  plan: `base.py:253` (+ :678, :1845, :2245, :2335, :2391),
  `commands/tasks.py` (×5), `commands/system.py` (×4),
  `commands/_task_snapshot_reducer.py` (×3), `commands/task_monitor.py:424`,
  `task_monitor.py:1287`, `manager.py:547`, `core/pipelines.py:405`
  (default pipeline names — accepted change), `cli/app.py:1511`, and the
  process-title matcher `weft/liveness/host.py:47`.
- Test files touching short forms (17): `tests/cli/test_cli_list_task.py`,
  `tests/cli/test_cli_system.py`, `tests/cli/test_status.py`,
  `tests/commands/test_runtime_prune.py`, `tests/commands/test_status.py`,
  `tests/commands/test_task_commands.py`, `tests/commands/test_task_evidence.py`,
  `tests/commands/test_task_snapshot_reducer.py`, `tests/core/test_manager.py`,
  `tests/core/test_serve_log.py`, `tests/liveness/test_analysis.py`,
  `tests/tasks/test_liveness_monitor.py`, `tests/tasks/test_task_endpoints.py`,
  `tests/tasks/test_task_execution.py`, `tests/tasks/test_task_monitor.py`,
  `tests/tasks/test_task_observability.py`, `tests/test_harness_registration.py`.
  Fixtures that pin literal short/full pairs are updated; process-title
  assertions go through the helper.

## 4. Invariants and Constraints

- Ten decimal characters, zero-padded; [OBS.4] title format unchanged.
- Non-derivable `full` values (not 19-digit numeric) raise from the
  helper and are skipped by resolution (rule owned by the registry
  custody plan).
- Accepted user-visible changes, stated precisely (Codex): the
  *derivation* changes for every short TID computed after upgrade —
  existing rows and tasks included, since derivation is at read time —
  though some individual values coincide with the old form by chance; a
  short form copied before the upgrade is not guaranteed to resolve
  afterwards; default pipeline names (`pipeline-{short}`) change. No
  persisted contract depends on the digits (grep specs and README for any
  pinned short/full example and update it).
- No new abstraction; the helper body is the whole code change.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 07, 10 at plan authoring time (2026-08-31).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/07-System_Invariants.md | B — atomic | [OBS.5] replace |

### [OBS.5] — replace "TID short form uses the low-order digits from the resolved 19-digit TID"

> - **OBS.5**: the TID short form is exactly ten decimal characters
>   derived from the hybrid timestamp's components: `grain = tid >> 12`
>   (a count of 4,096-nanosecond grains — SimpleBroker clears the low 12
>   bits of `time.time_ns()` and stores the logical counter there),
>   `counter = tid & 0xFFF`, and `short = (grain + counter × 2_441_406)
>   mod 10¹⁰`, zero-padded to ten characters, with `2_441_406 = ⌊10¹⁰ /
>   4096⌋` so all 4,096 counters within one grain remain distinct. One
>   helper owns the derivation; no site slices digits from the decimal
>   TID. The form is not unique: a short form matching more than one full
>   TID is an ambiguity error per [CLI-1.2.3].

## 5. Tasks

1. **Independent review** of this plan and the delta (§8) — before any
   change.
2. **Red tests.** (a) Two counter-zero TIDs whose values differ by exactly
   `4 × 10¹⁰` ns (both multiples of 4096) have equal `tid[-10:]` today and
   distinct `tid_short_form()` after. (b) TIDs in one grain with counters
   0, 1, 1000, and 4095 are pairwise distinct (today's form already
   distinguishes them — pins no regression for bursts). (c) A value whose
   fold lands below 10⁹ renders zero-padded to ten characters. (d) A
   mapping row with an old-format stored `short` still resolves by `full`.
   (e) `weft/liveness/host.py`'s title matcher finds a task whose title
   carries the new form.
3. **Atomic slice (strategy B).** Change the helper body; land the [OBS.5]
   text with its mapping claim and `Spec:` backlink; update the
   `TASKSPEC_TID_SHORT_LENGTH` docstring; update fixtures across the 17
   test files; add a CHANGELOG line describing the visible change.
4. **Traceability reconciliation.** Deviation log closed; gates rerun.

## 6. Testing Plan

Pure-function tests for the helper (deterministic TID constants); real
`Consumer` + `broker_env` for (d) and (e). Nothing mocked.

## 7. Verification and Gates

`./.venv/bin/python -m pytest tests/liveness tests/tasks/test_task_observability.py tests/commands/test_task_commands.py -q`,
then the full suite + mypy + ruff. Rollback: revertable; no persisted
format change (stored `short` is display-only).

## 8. Independent Review Loop

Cross-family (Codex) review of this plan before task 3. Stance: check the
arithmetic (residue classes, the ⌊10¹⁰/4096⌋ constant, padding), and look
for any consumer that parses short forms back into anything.

## 9. Out of Scope

The ambiguity error and the helper's introduction (registry custody plan);
any change to TID generation.

## 10. Fresh-Eyes Review

Author pass 2026-08-31: this is the corrected derivation after the Codex
finding that the first draft used a microsecond model; the constant and
red tests were recomputed from the verified encoder.


## Review Record (append-only)

**2026-09-07 — claims correction (plan text only).** Review read the actual
`simplebroker/_timestamp.py::_encode_hybrid_timestamp` and
`simplebroker/_constants.py::LOGICAL_COUNTER_BITS`. The encoder clears the
low 12 nanosecond bits and inserts the counter, as this plan states. A
review arithmetic probe confirmed 4,096 distinct short values across all
counters in one grain and `2_441_406 × 4096 = 9_999_998_976` nanoseconds.
The algorithm is unchanged. Birthday estimates now explicitly assume
independent uniform residues across distinct full TIDs; the collision
offset is stated modulo 40,960 seconds, and 32× refers to population at
equal birthday probability, not per-pair collision probability. These are
review-probe results, not implementation or final independent-review
claims.

## Implementation Record (2026-09-07)

Class 5, hardened; strategy B atomic. Independent pre-implementation review
verified actual SimpleBroker low-12-bit encoding, all4096 counters distinct,
padding and deterministic period. Statistical claims assume uniform independent
residues. Plan4 introduced canonical helper/ambiguity before this implementation.
Work is isolated; commits remain in user-requested order, one per plan.
Full tests including slow, Ruff, mypy and independent final review remain gates.
Older process titles may become title-unconfirmed; exact PID/create-time
evidence is unchanged. Old stored shorts remain shape-only mapping metadata.


### Isolated implementation checkpoint

The sole production behavior change is the body of
`weft/helpers/__init__.py::tid_short_form`: split low-12-bit counter from
physical grain, apply the approved fold, and retain the existing ASCII and
19-character validation. `TASKSPEC_TID_SHORT_LENGTH` changes only its docstring.
[OBS.5] is promoted atomically with this body and the CLI spec backlink;
[CLI-1.2.3] ambiguity and stored-short semantics are unchanged. CHANGELOG
records the changed display values, process titles, default pipeline names,
and lack of compatibility for previously copied shorts.

Before any edit, original files were copied under `/tmp/weft-plan7-baseline/`
and written as Git blobs; its `manifest.tsv` records the full hashes. This
preserves the isolated pre-plan6 baseline for a three-way integration after
plan6 rather than copying whole overlapping constants or TaskMonitor test files.

Tests captured red before changing the helper: the old 40-second collision
and folded-grain padding assertions both failed; exhaustive within-grain
counter uniqueness already passed. The new helper passes those tests plus
fixed counter offsets and wraparound, all 4,096 counters, old-format stored
short resolution, and the unchanged dual-formula collision/batch-preflight
suite. Invalid ASCII/length validation tests are unchanged.

The 17 named fixture modules were audited. Derivation-dependent fixtures and
expectations now call the helper. Existing noncanonical stored shorts remain
in shape/history tests, including malformed neighbors, because those fields
are deliberately not derivation authority. `test_cli_system.py`,
`test_manager.py`, `test_task_endpoints.py`, and `test_harness_registration.py`
need no derivation edits: their remaining stored-short values exercise row
shape/history, and endpoint producers already use the helper. The existing
collision fixture uses a full-TID difference of `10**10 << 12`, which collides
under both formulas; its ambiguity guarantees remain intact.

Added a real Consumer publication and OS-title matcher regression in
`tests/system/test_short_tid.py`, with original process-title restoration and
no mocks. Its process-owning run is deferred until the concurrent earlier-plan
full gate ends. Pure-function, broker, and fixture checks can run meanwhile;
the full user-required suite, Ruff, mypy, and independent review remain final
integration gates. There are no spec deviations or new suppressions.

Checkpoint validation: 170 distinct pure-function/broker cases pass across the
short-form contracts, mapping contracts, snapshot reducer, liveness analysis,
serve logging, runtime prune, and status modules. Owned-file Ruff and narrow
mypy for the two production modules pass. The real Consumer/title case and
remaining process-owning modules have not run under the earlier-plan test hold.

After the process-test hold lifted, the real Consumer/new OS-title case passed.
The first attempt exposed missing runtime output/control routes in the new
test fixture; adding explicit inbox/outbox/ctrl routes fixed the fixture before
Consumer construction. Publication, old stored-short resolution, real process
title matching, and title restoration now have firing coverage without mocks.
The remaining broad process-owning modules and full integration gates remain
pending. No production correction was needed.

Independent review identified one literal title in the additional
`tests/liveness/test_host.py` module beyond the original 17-file inventory.
The matching-title fixture now derives its short through the helper. A separate
old-format literal pins that exact process identity remains live while the title
is unconfirmed after upgrade. All seven cases in that pure module pass; its
original file and Git blob were preserved before editing like the other files.

The final documentation inventory corrected README's pinned full/short pair:
`1837025672140161024` derives to `2595737344` through the actual helper.
Process-title examples now use ten digits. README and AGENTS now describe the
nanosecond-magnitude encoding and low-12-bit logical counter; the CLAUDE symlink
is unchanged. Grep of README, AGENTS, and current specifications found no
remaining low-digit derivation or hybrid-microsecond wording. Original README
and AGENTS files and Git blobs were saved before these edits.

Final root verification: 4,544 passed, 16 skipped in the full suite including
slow tests (`pytest -m '' -n 2 -vv`). Ruff, full mypy (187 source files),
and suppression reconciliation passed. Skips are 11 opt-in live-provider
cases and five PostgreSQL-only cases under SQLite. All 33 integration-focused
short-ID, host-title and CLI status tests passed before the full run.

Independent final integration review passed against plan 6: the helper formula
is the only production behavior change, the short-width constant is doc-only,
Monitor source/store/runtime are untouched, and the task-monitor test changes
preserve the full ownership matrix and PONG assertions. The CLI status merge
retains exact host create-time evidence alongside the new short derivation;
the changelog retains both migration entries. No source or test edits occurred
during the full gate. All seven plans have now passed their ordered commit
gates; each plan's implementation and verification record is committed alone.
