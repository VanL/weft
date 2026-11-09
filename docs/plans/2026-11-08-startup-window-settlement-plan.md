# Startup Window Narrows With Measured Headroom

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md [MANAGER.18]
Superseded by: none

Class: 5 with hardening. The change edits the normative [MA-1.8] and
[MANAGER.18] startup-window rule (class 5) and the Manager's launch admission
decision on the durable spine (risky trigger, so the hardening checklist
applies). Spec baseline: `08c2f87b` (`perf/admission-base`). Plan type:
implementation with spec revision; promotion strategy B (atomic: rule text,
code and tests land in one change).

**Owner approval required.** This plan changes an owner-set admission rule.
The spec delta below is applied on the branch so it can be reviewed and
benchmarked as one unit, but it is a proposal: it must not land on `main`
without the owner's decision on "Owner decisions" item 1. The owner permitted
changing startup-overlap policy in
[`2026-11-07-nested-admission-load-plan.md`](./2026-11-07-nested-admission-load-plan.md)
("Admission hot path"); the shape chosen here still needs the owner's yes.

## Goal

On PostgreSQL the Manager may overlap up to W=6 unfinished ordinary startups
while measured usage U is below H=floor(4L/5) of the usable limit L=C-R. At
U>=H the window drops to one, so every launch waits for the previous child to
become observable. On the 1,250-task nested workload (C=300, R=30, L=270,
H=216), usage sat between 228 and 274 for most of the run, the window was
one, and the Manager spent about 25-30% of wall time with a launch candidate
ready and startup blocked (launch return to work start p50 about 360 ms;
1,516-1,637 checkpoints with `startup_feedback_pending` refusals; no
measured-capacity refusals).

This plan replaces the drop from W to one with a window that narrows in
proportion to measured headroom above H:

    N(U) = min(W, max(1, floor(W (L - U) / (L - H))))

It equals W at U<=H, as today, and falls to one only as U approaches L. It
adds no constant: H and W keep their owner-set values, and the slope is
fixed by them. A window wider than one never gives an unfinished startup
less than (L-H)/W connections of measured headroom, the share each of the
current rule's W startups has at its threshold.

## Owner decisions

1. **Approve the narrowing window (implemented on this branch).** Accept
   N(U) above H in place of the drop to one. Rejecting it means reverting this
   branch; nothing else depends on it.
2. **SQLite's serialized startup (proposed, not implemented).** See
   "Direction 3". The startup window's stated purpose, bounding unobserved
   launch exposure, does not apply on SQLite, so the rule needs either a
   stated reason or a change. The owner should choose after a measured SQLite
   run.
3. **No change for earlier settlement (direction 1).** Recorded so the owner
   can confirm the reasoning; no action is needed unless the owner wants a
   SimpleBroker connect-time notification explored.

## The three candidate directions

### Direction 1: settle on the earliest measured evidence (no change)

A startup already settles on the first fact that can wake the Manager.

- The child's first attributed connection is opened by
  `BrokerSession.connect` in `MultiQueueWatcher.__init__`
  (`weft/core/tasks/multiqueue_watcher.py:188`), early in task construction.
- The child's first `weft.state.tasks.<tid>` row is written by
  `BaseTask._initialize_base_task_runtime` (`weft/core/tasks/base.py:387`),
  later in the same constructor. Only in-process work separates them: opening
  queue handles, building the observation config, and setting the process
  title.
- The Manager watches `weft.state.tasks.<tid>` from launch selection
  (notification-only interest, [MA-1.8]). The row wakes it;
  `_has_pending_observation` marks the publication, and the next turn calls
  `_reconcile_launch_now` -> `_advance_launch_custody`
  (`weft/core/manager.py:2732`), which settles on snapshot handoff or, on
  PostgreSQL, on attributed usage for the task's `connection_owner` in the
  same turn.
- A new connection makes no queue write and no notification. Seeing it before
  the snapshot would need either a fixed poll of `pg_stat_activity`
  (rejected: [ALT-WT-002], [WT-4.3]) or a SimpleBroker connect-time
  notification, which belongs to SimpleBroker ([WT-2a.8]) and would add one
  more NOTIFY to the existing every-listener fan-out. The gain would be the
  in-process gap above, a few milliseconds.
- The ~360 ms from launch return to work start is mostly interpreter spawn and
  imports before any connection exists. No settlement rule can observe a child
  that has not connected. That cost belongs to the task-boot tracks.

These ordering claims come from reading the code, not from a new measurement.

### Direction 2: the shape of the window (implemented)

The window bounds launches authorized against a usage reading that does not
yet include them ([MA-1.8]: "The window bounds unobserved launch exposure").
Today it is a two-level control: W below H, one above. With a feedback delay
of one child boot, a two-level control pins the window at one across the
whole band from H to L, even when tens of connections of measured headroom
remain. The classical form for bounding work in flight against a measured
free capacity is window flow control: the allowed in-flight amount follows
the free capacity, as a receiver's advertised window follows its free buffer
([WT-4.2] "Classical CS models over blunt ad-hoc fixes"). N(U) is that form,
with its scale taken from the current rule rather than from any per-task
estimate:

- For N(U)>=2, N(U) <= W(L-U)/(L-H), so (L-U)/N(U) >= (L-H)/W. Including the
  reserve, (L-U+R)/N(U) >= (L-H+R)/W because N(U) <= W. The current rule's
  tightest point is W startups at U=H-1, which gives (L-H+1)/W. The floor
  therefore moves by 1/W of a connection (at U=H only), and nowhere else does
  an unfinished startup stand on less measured headroom, to L or to C, than
  the current rule already accepts.
- Where N(U)=1 the rule is today's serialized rule.
- U still decides admission: every launch needs a fresh coherent observation
  and U<L. N(U) never enters U or available capacity, charges nothing to
  queued or running work, and ends for each TID at settlement. That is the
  line [ALT-WT-001] draws: no guessed per-task connection charge and no
  lifetime debt.
- No constant is added. (L-H)/W is in units of connections per startup, but
  it comes from the owner's H and W, not from a forecast of any task's use.
  The spec text says so, so the slope cannot later be read as a tunable
  per-task budget.

Values for the measured workload (L=270, H=216, (L-H)/W=9):

| U | N(U) |
|---|------|
| <=216 | 6 |
| 217-225 | 5 |
| 226-234 | 4 |
| 235-243 | 3 |
| 244-252 | 2 |
| 253-269 | 1 |
| >=270 | measured admission denies the launch |

For the stress test's pressure leg (C=300, 5% reserve, R=15, L=285, H=228,
(L-H)/W=9.5): N(U)=3 at U=250, 2 at 266, and 1 from 267.

Other shapes were considered and set aside:

- Moving H up or W up: tunes owner constants on one benchmark ([WT-4.4]
  "Measure; do not bake in inefficiency"; the 2026-11-07 plan: "Do not tune
  the reserve or startup window on the basis of a single raw peak").
- A window from a measured mean of connections per task: a learned
  prospective charge, still per-task connection debt ([WT-4.2], [ALT-WT-001],
  [WT-7] "The theory does not promise exact per-task connection
  forecasting").
- Dropping the window above H and relying on the reserve alone: unbounded
  speculation against one reading.

### Direction 3: SQLite stays serialized (proposed, not implemented)

On SQLite the window is always one: `_startup_dispatch_blocked` uses a limit
of 1 off PostgreSQL (`weft/core/manager.py:2134`) and
`_startup_window_allows_launch` refuses overlap off PostgreSQL. Every
ordinary launch records startup feedback, so this applies whether the SQLite
resource gate is enabled or not.

- With a positive SQLite ceiling, U is the union of live mapping TIDs,
  `_active_child_launches`, `_child_processes` and `_scheduler_residents`
  (`Manager._observe_admission_usage`). A launch enters U at its pre-launch
  checkpoint, before process creation. This Manager's launches are never
  unobserved, so the window has nothing to bound.
- With the gate disabled (the default), nothing is measured, and [MA-1.8]
  says "A disabled SQLite gate supplies no implicit process-count bound". In
  practice the window paces launches to one child boot at a time.
- So the window's stated purpose does not justify SQLite serialization.
  Removing it is an observable change, though: with the gate off, a
  600-root backlog would start processes as fast as the single-flight launch
  worker allows, with more SQLite writer contention during boots. That is a
  decision about whether default SQLite should have any process bound, and
  the 2026-11-07 plan already says to price process concurrency before
  recommending one.

Options for the owner: (S1) keep it, and restate the reason in [MA-1.8] as
deliberate launch pacing rather than exposure bounding; (S2) with a positive
SQLite ceiling, drop the window and let the measured gate alone decide; (S3)
also allow W startups with the gate disabled. S2 and S3 each need a SQLite
run of the nested workload (SQLite took 365 s on the base) before a
decision.

## Source documents and reading

- `docs/program-theory.md` [WT-4.1] (wake predicates read facts), [WT-4.2]
  (classical CS models; "Queued work costs nothing; live processes cost";
  measured pressure C-R-U), [WT-4.3] (no defensive polling caps), [WT-4.4]
  (no capability diminution; measure), [WT-7] ("Measured admission is a soft
  guard"), [ALT-WT-001], [ALT-WT-002], [REV-WT-003].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8], the paragraph
  that starts "Every ordinary PostgreSQL launch requires a fresh coherent
  usage observation"; the same paragraph in
  `docs/specifications/07-System_Invariants.md` [MANAGER.18].
- Retired plan `2026-10-20-bounded-startup-fast-path` (in git at `0ff8cfbf^`):
  the original W=4, H=L/2 rationale. The owner later set H=4L/5 and W=6 by
  hand (commits `d39accfe`, `3dd97f88`, `3ef3d8e3`).
- `docs/plans/2026-11-07-nested-admission-load-plan.md`, "Admission hot
  path": the owner permits changing startup-overlap policy.
- Track D, `docs/plans/2026-11-08-manager-stable-wait-set-plan.md` on branch
  `perf/manager-wait-set` (`eccbaf9c`): registers startup interest at launch
  selection. See "Overlap with other branches".

Comprehension check (answer before editing):

1. Does N(U) change whether a launch is admitted when no startup is
   unfinished? No. With zero unfinished startups the window always allows a
   launch; only the measured gate U<L decides.
2. Does a wider window release startup feedback, custody or a checkpoint
   barrier sooner? No. Settlement, durable custody and the undurable
   checkpoint gate are unchanged; the window only bounds how many unsettled
   startups may coexist.

## Current structure

- `weft/core/admission.py::_startup_window_allows_launch` (lines 28-44 at the
  baseline) is a pure predicate applied after a fresh, allowed resource
  decision. It receives the number of unfinished startups, the backend and
  the capacity mapping (`used`, `public_limit`). Today it returns
  `unfinished == 0 or (postgres and H >= W and U < H and unfinished < W)`.
- `weft/core/manager.py::Manager._admission_allows_queue` (about line 3433)
  checks `_startup_dispatch_blocked()` without measuring, then measures U,
  evaluates resource admission, then calls the predicate. A refusal sets
  `_startup_blocked` and a recheck deadline of
  `MANAGER_ADMISSION_RECHECK_SECONDS`.
- `Manager._startup_dispatch_blocked` (line 2134) is readiness only: blocked
  when a negative decision is remembered or when unfinished >= W (PostgreSQL)
  or >= 1 (SQLite). `_release_startup_feedback` -> `_resume_startup_dispatch`
  -> `_clear_admission_retry` clears the remembered decision on settlement;
  a child reap clears it too.
- `weft/_constants.py` holds `MANAGER_STARTUP_FAST_PATH_FRACTION = 0.8` and
  `MANAGER_STARTUP_FAST_PATH_WINDOW = 6`.

## Invariants and constraints

- Every ordinary PostgreSQL launch keeps a fresh coherent usage observation
  and the measured gate U<L. Unknown usage denies dispatch.
- Zero unfinished startups always permits a launch at the window layer.
- N(U) <= W everywhere; at most W notification interests (Track D's
  sentence "There are at most W outstanding interests" stays true).
- No new constant, environment setting, per-task charge, lane or family cap.
  The constants keep their values.
- SQLite behaviour, H<W behaviour (tiny ceilings), unknown-usage behaviour,
  and below-H behaviour are unchanged.
- Settlement, custody handoff, checkpoint durability gates, the
  missing-publication fallback and published refusals are unchanged. The
  `startup_feedback_pending` refusal keeps its reason and ending condition.
- A negative decision is still remembered until membership changes, a child
  is reaped, or the recheck deadline arrives (no hot loop).
- Review gates: no drive-by refactor; no new execution path; no mock in place
  of real PostgreSQL usage in the system proof.

## Observable-difference register

| Change | Class | Theory basis |
|--------|-------|--------------|
| PostgreSQL, H<=U<L: up to N(U) unfinished startups instead of one (e.g. 4 at U=226-234 for L=270) | A (owner-decided rule change; owner decision 1) | [WT-4.2] classical model (window flow control against measured free capacity); [WT-4.2] measured C-R-U admission is unchanged; per-startup headroom never below the current rule's share at H; not [ALT-WT-001] (no charge in U, transient, no forecast) |
| More launches can be unobserved at once in that band, so short-lived overshoot past L into the reserve can be larger than today in that band, never larger than today's worst case at U=H-1 | A | [WT-7] "Measured admission is a soft guard"; safety oracle: zero native slot rejections on the pressured stress leg (coordinator benchmark) |
| `startup_feedback_pending` refusals above H start at occupancy N(U) instead of 1; same reason, scope and ending condition | C (fewer refusals, no lost information) | [WT-2a.15] unchanged |
| Below H, H<W, unknown usage, SQLite, settlement, custody, checkpoints | N | none needed |
| Constant docstring for `MANAGER_STARTUP_FAST_PATH_FRACTION` | N | none needed |

No row is a D.

## Spec baseline

- `08c2f87b` — `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/07-System_Invariants.md` at plan authoring time.

## Proposed spec delta

Promotion strategy B (atomic), both files on this branch.

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | B | [MA-1.8] window paragraph; Related Plans |
| docs/specifications/07-System_Invariants.md | B | [MANAGER.18] window paragraph; Related Plans |

In both files, replace the span from "Every ordinary PostgreSQL launch
requires" through "before authorizing one launch." (the first three
sentences of the window paragraph). The rest of the paragraph, from "Unknown
usage denies dispatch." onward, stays verbatim, including "SQLite retains
serialized startup." and "The window bounds unobserved launch exposure, not
connection consumption; it adds no per-task resource charge, family cap, or
global admission atomicity."

Replaced text:

> Every ordinary PostgreSQL launch requires a fresh coherent usage observation
> and the existing measured admission decision. Let L=max(0,C-R), H=floor(4L/5),
> and W=6. If H>=W and observed usage U<H, a Manager may have up to W unfinished
> ordinary startups. Otherwise it waits until no ordinary startup is unfinished
> before authorizing one launch.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
Every ordinary PostgreSQL launch requires a fresh coherent usage observation
and the existing measured admission decision. Let L=max(0,C-R), H=floor(4L/5),
and W=6. If H>=W, a Manager may have up to
N(U)=min(W, max(1, floor(W(L-U)/(L-H)))) unfinished ordinary startups at
observed usage U: the whole window W while U<=H, then fewer in proportion to
measured headroom L-U, down to one startup as U approaches L. Otherwise it
waits until no ordinary startup is unfinished before authorizing one launch.
A window wider than one never leaves an unfinished startup less measured
headroom, (L-U)/N(U), than the (L-H)/W share each of W startups has at H; the
shape uses no constant beyond H and W.
```

Replacement (`docs/specifications/07-System_Invariants.md`): the same text.

Clause audit of the replaced span: "fresh coherent usage observation" and
"existing measured admission decision" are kept; L, H and W keep their
definitions; "If H>=W" is kept as the overlap precondition; "U<H ... up to W"
becomes N(U)=W for U<=H (U=H now also allows W, and the formula is continuous
there); "Otherwise it waits until no ordinary startup is unfinished" is kept
for H<W, and N(U)=1 gives the same behaviour near L.

Implementation mappings are unchanged: [MA-1.8] and [MANAGER.18] already
name `weft/core/admission.py::_startup_window_allows_launch` as the owner of
the predicate.

## Tasks

1. Red unit table.
   - Files: `tests/core/test_admission.py`
     (`test_startup_overlap_narrows_with_measured_postgres_headroom`, which
     replaces `test_startup_overlap_requires_low_postgres_usage_and_room`).
   - Literal boundaries at L=270 (U=215, 216, 217, 243, 252, 253, 269, 300)
     and L=11, keeping the existing H<W, SQLite, unknown-usage and
     zero-unfinished cases.
   - Done when the table fails on the baseline for the cases above H
     (observed: 7 failures, each `False is True` for an allowed overlap above
     H).
2. Red PostgreSQL system test.
   - File: `tests/system/test_measured_admission_pg.py`
     (`test_startup_pressure_narrows_window_without_hot_loop_and_reopens`,
     which replaces
     `test_startup_pressure_waits_without_hot_loop_and_reopens_on_fresh_usage`).
   - Real Manager, real isolated PostgreSQL, real external sessions. C=200,
     10% reserve, so L=180, H=144 and N(U)=3 for 157<=U<=162. Four requests;
     two fenced startups launch at low usage; external sessions bring U to the
     middle of the three-startup band; a third startup must launch above H,
     the fourth must wait (no new usage observations over five turns, no
     pending source, PING still answered); releasing the sessions must reopen
     the window; all four outputs arrive. Every usage observation taken while
     pressure is held must lie in the band.
   - Done when it fails on the baseline at the wait for the third receipt
     (observed: `reactor evidence did not match before timeout`).
   - Do not mock usage or the admission decision; the test only wraps the
     real usage read to count observations.
3. Implement.
   - File: `weft/core/admission.py::_startup_window_allows_launch`. Compute
     N(U) with integer arithmetic `W * (L - U) // (L - H)`, clamp to
     `[1, W]`, return `unfinished < N`. L-H>=2 whenever H>=W, so there is
     no division by zero. U>L gives N=1, and the measured gate denies anyway.
   - Do not change `_startup_dispatch_blocked`: its pre-check against W stays
     a valid upper bound, and the measured decision applies the narrower
     window and remembers a refusal.
   - Update the `MANAGER_STARTUP_FAST_PATH_FRACTION` docstring in
     `weft/_constants.py`; keep both values.
   - Stop if the change seems to need a new constant, an environment setting,
     or any change to settlement or custody.
4. Spec promotion (strategy B, same change): apply the delta above to both
   spec files and add Related Plans backlinks.
5. Verify (commands below).

## Testing plan

- `tests/core/test_admission.py`: pure predicate, literal boundaries.
- `tests/system/test_measured_admission_pg.py`: real usage above H on
  PostgreSQL. This is the behaviour proof and the regression test.
- Unchanged, re-run as owners of neighbouring behaviour:
  `tests/core/test_manager_family_maintenance.py`
  (`test_startup_window_overlaps_but_bounds_unobserved_processes`: W
  overlap at low usage, out-of-order feedback, per-TID fallback),
  `tests/core/test_connection_admission.py`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_work_conserving_admission.py`.
- Not run here: `tests/system/test_admission_stress.py` (the coordinator
  benchmarks it, including the `[pg-pressure-*]` slot-rejection oracle).

## Verification and gates

```bash
./.venv/bin/python -m pytest tests/core/test_admission.py tests/core/test_connection_admission.py tests/core/test_admission_recovery.py tests/core/test_work_conserving_admission.py tests/core/test_manager_family_maintenance.py tests/system/test_measured_admission_pg.py -n 4 -q
./bin/pytest-pg tests/system/test_measured_admission_pg.py tests/core/test_admission.py tests/core/test_admission_recovery.py tests/core/test_connection_admission.py tests/core/test_work_conserving_admission.py tests/core/test_manager_family_maintenance.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Runtime observation (coordinator benchmark): on the nested PostgreSQL
workload, compare wall time, the share of Manager wall time with a candidate
pending and startup blocked, `startup_feedback_pending` versus
`measured_capacity_exhausted` refusal counts in checkpoints, peak sampled
usage, and native slot rejections (must stay zero on the pressured leg).

## Expected benchmark effect

- Startup-blocked wall time should fall from about 25-30%, because usage
  between 217 and 252 now allows two to five overlapping startups instead
  of one.
- Usage should spend more time near L, so `measured_capacity_exhausted`
  refusals should appear where today there are none. That is the measured
  gate doing its job.
- Throughput should improve moderately. A rough estimate, not a measurement,
  is 5-15% of wall time. The deeper limit is about 2.4 PostgreSQL connections
  per running task (about 110 concurrent tasks at L=270).

What could hide it:

- If usage sits mostly at or above 253, the window is still one there.
- Once usage reaches L, admission is capacity-bound and only lower
  per-task connection use (another track) raises throughput.
- Manager turn cost (checkpoint publications and usage reads per launch) may
  cap the launch rate before the window does.
- Run-to-run spread (the baseline varied between 423 and 445 s) can hide a
  small gain.

## Rollback

Revert the branch commit. That restores the predicate, the docstring, both
spec paragraphs and the tests together. Nothing persistent changes: no queue
shape, checkpoint field or setting. A Manager running either version reads
the same evidence. No one-way door.

## Out of scope

- Changing H, W or the reserve.
- SQLite startup pacing (owner decision 2).
- Any change to settlement, custody, checkpoints, wake interests (Track D)
  or child boot cost.
- SimpleBroker connect-time notification.

## Overlap with other branches

- Track D (`perf/manager-wait-set`, `eccbaf9c`) edits
  `Manager._activity_wait_queues` and `Manager._has_pending_observation` and
  the [MA-1.8] notification-interest paragraph. This plan touches neither
  function nor that paragraph. Its claim "at most W outstanding interests"
  still holds because N(U) <= W. Expect only trivial merge conflicts in the
  two Related Plans lists and the `docs/plans/README.md` index and count.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Fresh-eyes review (author)

- Checked against [ALT-WT-001]: no term enters U or available capacity;
  nothing is charged while queued; the window ends per TID at settlement.
  The slope (L-H)/W has units of connections per startup and is stated as
  the existing rule's share, not a per-task estimate. The owner must still
  accept that reading (decision 1).
- Checked hot loops: a refusal still sets `_startup_blocked` and the recheck
  deadline, and the system test asserts no new usage observation over five
  turns while blocked.
- Checked the H>=W guard against division by zero: L-H >= 2 whenever H>=6.
- Checked that U>=L with startups unfinished gives N=1 and a refusal, and
  that the measured gate refuses first anyway.
- The system test's band has six values (157-162). It targets 159 using a
  measured baseline, so a pool connection opening or closing in the Manager
  keeps the window at three. Four PostgreSQL runs of it passed. Its body
  takes about 7 s; about 30 s more is a join timeout on a fixture process at
  teardown, in teardown code the replaced test shares unchanged.

## Review and execution record

(Coordinator: cross-family review and benchmark results go here.)

Implementation evidence from the authoring agent:

- Red: the unit table failed on the baseline with seven `False is True`
  cases above H. The PostgreSQL system test failed on the baseline at the
  wait for the third receipt.
- Green: see "Verification and gates". Results are in the commit message and
  the handoff report.
