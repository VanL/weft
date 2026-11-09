# Measured Memory Admission and Startup-Window Parity

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md [MANAGER.18]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/00-Quick_Reference.md (Environment Variables)
Superseded by: none

Class: 5 with hardening. The change edits normative [MA-1.8], [MANAGER.18]
and [MF-5] text (class 5) and changes the Manager's launch admission decision
on the durable spine (risky trigger, so the hardening checklist applies). Spec
baseline: `8acaddf3` (`perf/manager-dispatch`: `perf/admission-base` plus
optimization tracks A-H, including Track G's startup-window formula). Plan
type: implementation with spec revision; promotion strategy B (atomic: rule
text, code and tests land in one change).

## Owner direction (2026-11-09)

The repository owner asked for this change (owner session, relayed by the
coordinator):

- "We should not be more conservative on SQLite than on PG."
- "Start with the pg config, but then see if you can improve it on sqlite."
- On a fixed ceiling: "Sqlite may need a ceiling, but it can be way higher."
- "Can we size to memory? Determine how much memory we have and then adapt
  to that?"

The coordinator proposed a measured memory gate and the owner is proceeding
with it. This direction supersedes "Owner decisions" item 2 and "Direction 3"
(options S1, S2 and S3) of
[`2026-11-08-startup-window-settlement-plan.md`](./2026-11-08-startup-window-settlement-plan.md).

## Goal

SQLite serialized ordinary startup: one unfinished startup at a time,
whatever its gate said, because `_startup_window_allows_launch` returned
False off PostgreSQL and `_startup_dispatch_blocked` used a readiness limit of
one off PostgreSQL. On the 1,250-task stress workload (coordinator
measurement) SQLite ran about 70 tasks at once against about 114 on
PostgreSQL, limited by that serialization. With the TID gate disabled (the
SQLite default), nothing else bounded process creation.

This plan adds one measured resource gate that exists on every backend, host
memory, and applies the existing startup-window formula to every gate that
exists, taking the smallest window. Then:

- SQLite gets the same W-wide startup window as PostgreSQL while host memory
  is well below its limit; no backend serializes startup by rule.
- Every backend refuses new ordinary launches while measured host memory use
  is at or above `T - ceil(T*0.1)`, with no per-task memory charge.
- A positive SQLite ceiling keeps its live/unknown-TID gate as an extra gate.
- PostgreSQL keeps its connection gate; both gates must allow a launch.

No new setting is added. W and `WEFT_ADMISSION_RESERVE_FRACTION` keep their
values and single bindings. The memory reserve is a fixed constant,
`ADMISSION_MEMORY_RESERVE_FRACTION = 0.1` (see "Why the memory reserve is
fixed").

## Source documents and reading

- `docs/program-theory.md`: [WT-0]; [WT-2a.13] (unknown is not proof);
  [WT-2a.14] (submission never blocks); [WT-2a.15] (standing refusals are
  published and name their ending); [WT-4.2] "Classical CS models over blunt
  ad-hoc fixes" ("measured admission control") and "Queued work costs
  nothing; live processes cost" ("Admission gates process creation using
  measured pressure (`C - R - U`), never prospective per-task connection
  debt"); [WT-4.4] "One path on all platforms", "As few concepts as
  possible", "No capability diminution"; [WT-7] "Measured admission is a soft
  guard" and "Contention failures test possession of the theory" ("Wanting
  fewer workers, serialization, or another reduction in contention to make
  tests pass is an immediate stop condition"); [ALT-WT-001] (no guessed
  per-task charge); [ALT-WT-002] (no fixed caps); [REV-WT-003].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8]: the admission
  paragraphs ("Ordinary admission ...", "On PostgreSQL, unset or zero ...",
  "Every ordinary PostgreSQL launch requires ..."), the interest sentence
  ("There are at most W outstanding interests (one on SQLite)."), the reserve
  paragraph, the drain-wait paragraph, the exception sentence and the
  implementation mapping.
- `docs/specifications/07-System_Invariants.md` [MANAGER.18]: the mirrored
  paragraphs, the drain-wait paragraph and the mapping.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: the
  dispatch-refusal reason inventory.
- `docs/plans/2026-11-08-startup-window-settlement-plan.md` (Track G): the
  N(U) formula this plan generalizes; its Direction 3 is superseded here.
- `docs/plans/2026-11-05-manager-published-refusals-plan.md`: how lane
  refusals are projected (`_dispatch_lane_reasons`, `_dispatch_lane_refusals`,
  `DISPATCH_REFUSAL_DESCRIPTORS`).

Comprehension check (answer before editing; expected answers inline):

1. With the TID gate disabled on SQLite, which gate decides whether a public
   launch is admitted, and which reads happen? Only the memory gate: one
   `psutil.virtual_memory()` call and no task-state snapshot read.
2. Does a task's `memory_mb` limit, or its default, change admission? No. It
   never enters `U_m` or `L_m`; only measured host memory counts.
3. On PostgreSQL, does a memory refusal start or advance the drain-wait
   episode? No. The episode follows the connection gate only.

## Current structure (at the baseline)

- `weft/core/admission.py::AdmissionController.evaluate(used, backend, now)`
  computed one capacity (`_admission_capacity`) and the lanes it blocks, and
  tracked the PostgreSQL drain-wait episode. The Manager never called it on
  SQLite with a zero ceiling.
- `weft/core/admission.py::_startup_window_allows_launch(unfinished, backend,
  capacity)` returned False for any unfinished startup off PostgreSQL.
- `weft/core/manager.py::Manager._admission_enabled` returned False on SQLite
  with a zero ceiling, so `_admission_allows_queue` admitted without any
  observation and `_schedule_admission_retry_for_source` scheduled no retry.
- `Manager._startup_dispatch_blocked` used a readiness limit of W on
  PostgreSQL and 1 elsewhere, reading `MANAGER_STARTUP_FAST_PATH_WINDOW`
  through `weft/core/manager.py`'s own import.
- `Manager._record_admission_decision` kept one reason
  (`admission_usage_unavailable` or `measured_capacity_exhausted`) and one
  measurement for every blocked lane; `_dispatch_lane_reasons` and
  `_dispatch_lane_refusals` project it into `dispatch_refusals`.
- `weft/_constants.py::DISPATCH_REFUSAL_DESCRIPTORS` is the closed reason
  inventory; `weft/core/dispatch_refusals.py::dispatch_refusals_observation`
  rejects any reason not in it.

## Design

### Memory gate (all backends)

On every fresh admission observation (`Manager._admission_allows_queue`, the
same cadence and owner as the PostgreSQL connection read), the Manager calls
`psutil.virtual_memory()` once (`Manager._observe_admission_memory`): total
`T`, available `A`. No process walk. A raised `OSError` or `RuntimeError` is
an unknown observation.

`weft/core/admission.py::_memory_capacity`:

- usage `U_m = T - A`, which includes every other program, as PostgreSQL
  usage includes unattributed connections;
- reserve `R_m = ceil(T*0.1)` from the fixed constant
  `weft/_constants.py::ADMISSION_MEMORY_RESERVE_FRACTION` (no slot floor,
  which is a connection-room concept);
- public limit `L_m = T - R_m`, internal limit `T`;
- public allowed while `U_m < L_m`, internal allowed while `U_m < T`.

There is no per-task charge, and the default `memory_mb` limit is never
used ([WT-4.2], [ALT-WT-001]).

### Why the memory reserve is fixed

The brief proposed the same `WEFT_ADMISSION_RESERVE_FRACTION` for memory
unless a theory or spec reason called for a separate one. Implementation
found the reason. [MA-1.8] defines that fraction as the backend gate's
operating reserve, valid anywhere in `[0,1)`, and operators raise it to leave
a shared database server's connections to its other clients;
`tests/system/test_measured_admission_pg.py::test_native_postgres_ceiling_denies_real_pressure_and_resumes`
sets it to 0.75 for exactly that ("It changes reserve policy, never server
capacity"). With a shared fraction, that valid configuration withheld 75% of
host memory: on the authoring host (53% in use) the Manager refused every
public launch (`admission_state` with `reason` `measured_memory_exhausted`,
`memory_reserve` 103,079,215,104 bytes) and the test timed out. That is a
capability diminution with no theory basis. The two reserves protect
different populations (other clients of a database server versus other
programs on this host), so the memory gate uses its own reserve. To add no
setting ([WT-4.4] "As few concepts as possible"), it is a fixed module
constant equal to the backend fraction's default, read through
`weft.core.admission`, so an experiment can patch it. Whether the owner wants
it fixed, tied to the backend fraction after all, or exposed as a setting is
an open owner decision.

### Gate combination

`AdmissionController.evaluate(used, memory, backend, now)`:

- the backend gate exists on PostgreSQL always and on SQLite only under a
  positive ceiling (`AdmissionController.backend_gate_exists`); `used` is
  ignored otherwise, and the Manager does not read it;
- an unknown observation on any existing gate blocks both lanes;
- otherwise the blocked lanes are the union of each gate's blocked lanes;
- the PostgreSQL drain-wait episode follows the backend gate only, so a
  memory refusal is published as a refusal but never starts, advances or
  ends a drain-wait episode (one memory-gate path on all backends);
- `AdmissionDecision.gates` lists each existing gate's capacity (None for
  unknown); `AdmissionDecision.refusal(lane)` names the refusing gate for a
  blocked lane: unknown -> `admission_usage_unavailable` with no
  measurements; backend gate refuses the lane -> `measured_capacity_exhausted`
  with its measurements; otherwise `measured_memory_exhausted` with the memory
  measurements in bytes.

Refusals are recorded per lane (`Manager._dispatch_admission_refusals`), so a
public lane refused by connections and an internal lane refused by memory
each name their own gate. The `admission_state` serve log adds
`memory_used`, `memory_reserve`, `memory_public_limit` and `memory_total`, and
its rate-limit identity includes the refusal reason, so a change of refusing
gate is logged once.

### Startup window

For each gate with public limit `L`, usage `U`, `H = floor(4L/5)` and `W = 6`:
`N(U) = min(W, max(1, floor(W(L-U)/(L-H))))` if `H >= W`, else 1
(`_gate_startup_window`). The window is the smallest `N` over
`decision.gates`, and an unknown gate denies overlap
(`_startup_window_allows_launch`). The readiness pre-check
`_startup_window_full(unfinished)` is `unfinished >= W` on every backend. Both
read W through the `weft.core.admission` module binding, so a coordinator
experiment that patches `weft.core.admission.MANAGER_STARTUP_FAST_PATH_WINDOW`
changes the readiness bound and the predicate together. The Manager no longer
imports W.

At the measured host (T = 128 GiB), `L_m` is about 115.2 GiB and
`H_m` about 92.2 GiB: the memory window is W below about 72% of total memory
in use and narrows to one as use approaches 90%.

A positive SQLite ceiling counts this Manager's launches from before process
creation, so its gate already sees its own startups; the same formula applies
to it for one path, and it only narrows near that ceiling.

### Launch-failure retry

With a gate on every backend, admission is never disabled, so
`_schedule_admission_retry_for_source` now schedules the existing one-second
lane retry after a launch-worker failure on SQLite with a zero ceiling, as it
already did on PostgreSQL. `Manager._admission_enabled` is removed.

### macOS available memory

On macOS, psutil's `available` is free plus inactive pages, which includes
reclaimable file cache. `U_m` therefore excludes cache the kernel can evict,
and memory compression and swap are not measured separately. [MA-1.8] states
this; the code does not armor it: the gate is a sampled soft guard ([WT-7]).
The coordinator's sweep measures swap and memory pressure.

## Invariants and constraints

- Submission never blocks; queued work costs nothing; accepted work stays
  queued while refused ([WT-2a.14], [MF-1]).
- No per-task memory or connection charge; `memory_mb` is never an admission
  input ([ALT-WT-001]).
- Every ordinary launch requires a fresh observation of every existing gate;
  positive admission is never cached across launches.
- Unknown observation on any gate defers dispatch on both lanes and preserves
  accepted work, custody and drain-wait clocks.
- Zero unfinished startups always pass the window layer; N <= W everywhere;
  at most W startup interests on every backend.
- Settlement, custody handoff, checkpoint barriers, the missing-publication
  fallback and the `startup_feedback_pending` refusal are unchanged.
- PostgreSQL connection-gate math, the reserve's slot floor and the
  drain-wait rules for the connection gate are unchanged.
- No new setting, lane, family cap or timer. W, the startup fraction and
  the backend reserve fraction keep their values and bindings; the only new
  constant is the fixed memory reserve fraction.
- Review gates: no drive-by refactor; no mock of Manager queues, processes or
  the admission decision; host memory is controlled only by patching
  `psutil.virtual_memory`, the external dependency.

Hidden couplings:

- `dispatch_refusals_observation` rejects unknown reasons, so the new reason
  is added to `DISPATCH_REFUSAL_DESCRIPTORS` in the same change as its
  producer; readers and producers ship together ([WT-2a.9]).
- Tests that assert a full window of W now depend on host memory as well;
  they pin it through `tests/helpers/manager_dispatch.py::pin_host_memory`.

## Observable-difference register

| Change | Class | Theory basis |
|--------|-------|--------------|
| SQLite with no TID ceiling (the default): up to N_mem unfinished startups (W while memory use is at or below about 72% of total) instead of one | A (owner direction 2026-11-09) | [WT-4.4] "One path on all platforms"; [WT-7] "Contention failures test possession of the theory" (serialization is a stop condition, not a design); [WT-4.2] "measured admission control" |
| SQLite with a positive ceiling: window is min(N_tid, N_mem) instead of one | A | as above; the TID gate keeps its live/unknown accounting ([MA-1.8]) |
| Every backend: public launches refused while `U_m >= T - ceil(T*0.1)`; internal launches while available memory is zero | A (owner direction: "size to memory") | [WT-4.2] "Queued work costs nothing; live processes cost": measured pressure (`C - R - U`) applied to memory, with no prospective per-task charge ([ALT-WT-001]); usage includes external pressure like unattributed connections |
| SQLite with no TID ceiling now has a process bound (host memory); [MA-1.8] previously said a disabled gate supplies no implicit process-count bound | A | as above; the bound is measured, not a count ([ALT-WT-002]) |
| PostgreSQL: window is min(N_conn, N_mem); narrower than before only when host memory use is above about 72% of total | A | [WT-4.2] measured admission; the per-startup headroom share holds on every gate |
| Unknown memory observation (psutil raises `OSError` or `RuntimeError`) defers dispatch on both lanes | A | [WT-2a.13] unknown is never proof; [MA-1.8] "Failed or unknown observation defers dispatch without rejecting accepted work" |
| New refusal reason `measured_memory_exhausted` (barrier `measured_resource`, ends `fresh_memory_permits`, byte measurements) in `dispatch_refusals` | A (contract addition) | [WT-2a.15] standing refusals are published facts naming their ending; [WT-2a.9] one current contract (reader and producer ship together) |
| Lane refusals are per lane: the internal lane may name a different gate than the public lane | C (more precise, nothing lost) | [WT-2a.15] "names the evidence" |
| `admission_state` serve log carries `memory_*` fields and logs once when the refusing gate changes within the same state | C | [WT-4.4] "Observable with standard tools" |
| After a launch-worker failure on SQLite with no TID ceiling, the existing one-second retry (or an earlier child or launch wake) now applies as on PostgreSQL: a public-launch failure delays the public lane, and an internal-launch failure delays both lanes (the internal-failure branch at `weft/core/manager.py` ~3535 is now active because `_admission_enabled` is removed) | A | [WT-4.4] "One path on all platforms"; a launch failure (for example a failed spawn under memory pressure) is evidence a fresh observation should precede the next launch |
| Each SQLite launch decision makes one `psutil.virtual_memory()` call and, with no TID ceiling, no broker read | N | none needed (no process walk; [WT-4.3] quiet cost unchanged) |
| Up to W startup notification interests on SQLite (was one) | C | follows from the window; same bound as PostgreSQL |
| PostgreSQL drain-wait: unchanged for the connection gate; memory refusals never touch it | N | none needed |
| `WEFT_ADMISSION_RESERVE_FRACTION` keeps meaning the backend gate's reserve only; the memory reserve is a fixed 10% | N (for existing configurations) | avoids the D a shared fraction caused: a valid large connection reserve would refuse all public work ([WT-4.4] "No capability diminution") |
| W read through one binding in `weft.core.admission` for both the readiness bound and the predicate | N | internal; keeps coordinator experiments coherent |

No row is a D.

## Spec Baseline

- `8acaddf3` - `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/00-Quick_Reference.md` at plan authoring time.

## Proposed Spec Delta

Promotion strategy B (atomic), all files in this change.

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | B | [MA-1.8] admission paragraph, PostgreSQL/SQLite paragraph, new memory-gate paragraph, window paragraph, interest sentence, reserve paragraph, drain-wait sentences, exception sentence, implementation mapping, startup-decision mapping; Related Plans |
| docs/specifications/07-System_Invariants.md | B | [MANAGER.18] admission paragraph, PostgreSQL/SQLite paragraph, new memory-gate paragraph, window paragraph, drain-wait episode sentences, mapping; Related Plans |
| docs/specifications/05-Message_Flow_and_State.md | B | [MF-5] dispatch-refusal reason inventory; Related Plans |
| docs/specifications/00-Quick_Reference.md | B | `WEFT_ADMISSION_MAX_CONNECTIONS` and `WEFT_ADMISSION_RESERVE_FRACTION` rows |

### [MA-1.8] and [MANAGER.18] admission paragraph

Replace the whole paragraph from "Ordinary admission uses one coherent
SimpleBroker observed-usage population." through "a disabled resource gate
does not disable family classification or launch custody." (identical in
both files).

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
Ordinary admission measures one coherent SimpleBroker observed-usage population (the backend gate) and host memory (the memory gate, below). For the backend gate, available capacity is the configured applicable ceiling minus the configured operating reserve minus observed usage. A positive available value permits consideration of one eligible launch; it is not a claim that the task will consume one connection. There is no per-task connection budget, lifetime deficit, or ordinary child admission class. Usage includes external and unattributed pressure. Failed or unknown observation defers dispatch without rejecting accepted work. Admission and launch are not globally atomic. Internal runtime requests retain their documented reserve eligibility; their measured use participates in the same population. Under a positive ceiling, SQLite retains its live/unknown TID accounting and local-launch union. On SQLite, a disabled TID gate does not disable family classification, launch custody or the memory gate.
```

Clause audit: every sentence is kept; the first sentence now names both
gates, "Available capacity" is scoped to the backend gate, the SQLite
accounting sentence is scoped to a positive ceiling, and the last sentence
adds the memory gate.

### PostgreSQL/SQLite capacity paragraph (both files)

Replace its last two sentences, "On SQLite, unset or zero retains the existing
disabled resource mode; positive ceilings retain actual live/unknown-TID
accounting. A disabled SQLite gate supplies no implicit process-count bound."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
On SQLite, unset or zero disables the live/unknown-TID gate; positive ceilings retain actual live/unknown-TID accounting. A disabled TID gate supplies no implicit process-count bound; the memory gate still applies.
```

### New memory-gate paragraph (both files, after that paragraph)

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
Every backend also gates launches on measured host memory. On each fresh admission observation the Manager reads total host memory T and available memory A with one `psutil.virtual_memory()` call; it walks no processes. Memory usage is `U_m = T - A`, so it includes every other program's memory, as connection usage includes unattributed connections. The memory reserve is `R_m = ceil(T*0.1)`, the public limit is `L_m = T - R_m`, and the internal limit is T: an ordinary launch requires `U_m < L_m`, and internal runtime work requires `U_m < T`. The memory reserve is fixed: `WEFT_ADMISSION_RESERVE_FRACTION` is the backend gate's operating reserve, which operators raise to leave connections to other clients of a shared database server, and it does not apply to host memory. No task is charged memory: a task's `memory_mb` limit, including its default, never enters `U_m` or `L_m`, and queued work costs nothing. A failed memory observation is unknown and defers dispatch on both lanes, preserving accepted work and custody. A launch requires every gate that exists to allow its lane: the memory gate on every backend, and the backend gate on PostgreSQL or under a positive SQLite ceiling. The memory gate has no setting of its own. On macOS available memory includes reclaimable file cache, so `U_m` excludes cache the kernel can evict; memory compression and swap are not measured separately. Like the backend gate, the memory gate is a sampled soft guard.
```

### Startup window (both files)

Replace the span from "Every ordinary PostgreSQL launch requires a fresh
coherent usage observation" through "it adds no per-task resource charge,
family cap, or global admission atomicity." The rest of the paragraph, from
"The window is local to one Manager incarnation." onward, stays verbatim.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
Every ordinary launch requires a fresh observation of every gate that exists
and the existing measured admission decision. Let W=6. For one gate with public
limit L, observed usage U and H=floor(4L/5) (L=max(0,C-R) for the backend gate,
L=L_m for the memory gate), the gate permits
N(U)=min(W, max(1, floor(W(L-U)/(L-H)))) unfinished ordinary startups if H>=W:
the whole window W while U<=H, then fewer in proportion to measured headroom
L-U, down to one startup as U approaches L. A gate whose H<W permits one. A
Manager may have up to the smallest N(U) among the gates that exist unfinished
ordinary startups, and otherwise waits until fewer are unfinished before
authorizing a launch. The memory gate exists on every backend, so no backend
serializes startup by rule. A window wider than one never leaves an unfinished
startup less measured headroom on any gate, (L-U)/N(U), than the (L-H)/W share
each of W startups has at H; the shape uses no constant beyond H and W. Unknown
usage on any gate denies dispatch. Window occupancy
counts selected launch-worker requests and processes awaiting startup feedback
once per TID, starting before process creation; it does not count queued work
or running tasks whose startup feedback has settled. The window bounds
unobserved launch exposure, not resource consumption;
it adds no per-task resource charge, family cap, or global admission atomicity.
```

Clause audit: "fresh ... observation" and "existing measured admission
decision" are kept and extended to every gate; L, H and W keep their
definitions per gate; "If H>=W ... N(U)" is kept per gate; "Otherwise it
waits until no ordinary startup is unfinished" becomes "A gate whose H<W
permits one" plus the minimum rule; the headroom-share sentence is kept per
gate; "Unknown usage denies dispatch" covers any gate; the occupancy sentence
is verbatim; "SQLite retains serialized startup." is removed; "not connection
consumption" becomes "not resource consumption".

### Other [MA-1.8] sentences (spec 03 only)

- "There are at most W outstanding interests (one on SQLite)." becomes "There
  are at most W outstanding interests."
- Reserve paragraph: "reserve `R` is `max(ceil(C*f), 3 +
  int(liveness_monitor_enabled))`. Internal runtime work may use the reserve
  but still requires measured usage below C. Unset or zero
  `WEFT_ADMISSION_MAX_CONNECTIONS` selects the backend maximum on PostgreSQL
  and disables the resource gate only on SQLite." becomes

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
Reserve fraction is finite in `[0,1)` and defaults to `0.1`; reserve `R` is `max(ceil(C*f), 3 + int(liveness_monitor_enabled))`. The fraction does not apply to the memory gate, whose reserve is fixed at `ceil(T*0.1)` with no slot floor. Internal runtime work may use either reserve but still requires each existing gate's measured usage below its whole capacity, C or T. Unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` selects the backend maximum on PostgreSQL and disables only the live/unknown-TID gate on SQLite.
```

- Drain-wait: "while any configured lane is blocked." becomes "while its
  backend gate blocks any lane; the memory gate never starts, advances or
  ends an episode." and "Fresh capacity that opens all configured lanes ends
  the episode" becomes "Fresh backend capacity that opens all lanes ends the
  episode".
- Exceptions: after "SQLite catches only `BrokerError`, `OSError`, and
  `RuntimeError`" add ", and the memory observation catches only `OSError`
  and `RuntimeError`".
- Implementation mapping ([MA-1.8] Admission bullet and the startup-decision
  paragraph): name `AdmissionController.backend_gate_exists`,
  `_memory_capacity`, `HostMemory`, `AdmissionDecision.gates`,
  `AdmissionDecision.refusal`, `Manager._observe_admission_memory` and
  `_startup_window_full`.

### Other [MANAGER.18] sentences (spec 07 only)

- Drain-wait episode: "An episode starts at first observed lane denial, also
  on an unavailable observation, never on launch-failure restoration alone.
  It ends when a fresh observation opens all configured lanes" becomes "An
  episode starts at the backend gate's first observed lane denial, also on an
  unavailable backend observation, never on launch-failure restoration or a
  memory refusal alone. It ends when a fresh backend observation opens all
  lanes".
- Mapping: `_startup_window_allows_launch` owns the overlap predicate over
  every existing gate; `_memory_capacity` owns the memory gate;
  `Manager._observe_admission_memory` reads host memory.

### [MF-5] reason inventory (spec 05)

Insert `measured_memory_exhausted` after `measured_capacity_exhausted` and
`fresh_memory_permits` after `fresh_capacity_permits`, and add: "Each refused
lane names the gate that refuses it: a lane refused by the backend gate
reports measured_capacity_exhausted, a lane refused only by host memory
reports measured_memory_exhausted with byte measurements, and an unknown
observation on any gate reports admission_usage_unavailable."

### Quick Reference (spec 00)

`WEFT_ADMISSION_MAX_CONNECTIONS`: SQLite "unset or `0` disables resource
admission" becomes "disables the live/unknown-TID gate", plus "The host
memory gate applies on every backend and has no setting of its own."
`WEFT_ADMISSION_RESERVE_FRACTION`: add "It does not apply to the host memory
gate, whose reserve is fixed at 10% of total host memory."

## Tasks

1. RED tests (before any production edit).
   - `tests/core/test_admission.py`:
     `test_startup_window_is_the_smallest_measured_gate_window` (replaces
     `test_startup_overlap_narrows_with_measured_postgres_headroom`; literal
     boundaries at H and L for the connection scale and the byte scale, the
     multi-gate minimum in both orders, unknown gates);
     `test_one_window_binding_bounds_readiness_and_the_predicate`;
     `test_memory_capacity_reserves_a_fraction_of_measured_host_memory`;
     `test_admission_requires_every_existing_gate_and_names_the_refusing_gate`;
     `test_memory_refusal_does_not_start_a_postgres_drain_wait`;
     `test_backend_reserve_fraction_does_not_reserve_host_memory` (added when
     the PostgreSQL run exposed the shared-fraction coupling). Existing calls
     gain `memory=AMPLE`.
   - `tests/core/test_manager_family_maintenance.py`:
     `test_startup_window_overlaps_but_bounds_unobserved_processes` is
     parametrized over `startup_pg_manager` ceilings 300 and 0 and expects W
     on every backend (was 1 off PostgreSQL), with host memory pinned;
     `test_memory_at_reserve_refuses_public_launch_and_keeps_work_queued` is
     new.
   - Done when the Manager tests fail on the baseline for the intended reason
     (see the execution record).
2. Implement `weft/core/admission.py`: `HostMemory`, `_memory_capacity`,
   `_gate_startup_window`, `_startup_window_full`, the new
   `_startup_window_allows_launch(unfinished, gates)`,
   `AdmissionController.backend_gate_exists`, `evaluate(used, memory,
   backend, now)`, `AdmissionDecision.gates` and `.refusal`.
   - Stop if the change seems to need a new setting, a per-task charge, or a
     change to settlement or custody.
3. Implement `weft/core/manager.py`: `import psutil`;
   `_observe_admission_memory`; `_admission_allows_queue` reads the backend
   gate only when it exists and always reads memory; remove
   `_admission_enabled`; `_startup_dispatch_blocked` uses
   `_startup_window_full`; per-lane `_dispatch_admission_refusals`;
   `admission_state` memory fields and reason in its identity.
4. `weft/_constants.py`: add `measured_memory_exhausted` to
   `DISPATCH_REFUSAL_DESCRIPTORS`; add `ADMISSION_MEMORY_RESERVE_FRACTION`;
   update the startup-window and `WEFT_ADMISSION_MAX_CONNECTIONS_DEFAULT`
   docstrings.
5. Spec promotion (strategy B, same change): the delta above, Related Plans
   backlinks in specs 03, 05 and 07, the Quick Reference rows, and the README
   admission text.
6. Update dependent tests for the new API (`memory=` on `evaluate`) and pin
   host memory where a test asserts a full window or open admission:
   `tests/core/test_connection_admission.py`,
   `tests/core/test_work_conserving_admission.py`
   (`test_sqlite_zero_disables_only_the_tid_gate`),
   `tests/core/test_family_source_reconciliation.py`,
   `tests/core/test_manager.py`
   (`test_sqlite_disabled_tid_gate_dispatches_without_observing_backend`),
   `tests/core/test_manager_family_maintenance.py`
   (`test_settlement_and_admitted_launch_share_one_waiter_rebuild`, the
   per-lane refusal assertion), `tests/core/test_manager_checkpoint_volume.py`
   (a stale comment), `tests/helpers/manager_dispatch.py` (`pin_host_memory`).
7. Verify (commands below) and run a sensitivity check for each new test.

## Testing plan

- Seam: host memory is an external dependency. Tests control it by patching
  `psutil.virtual_memory` (`pin_host_memory`), so the Manager's observation,
  gate combination, startup window and refusal publication run for real. No
  Manager method, queue, process or admission decision is mocked.
- Pure tables (`tests/core/test_admission.py`): window formula boundaries at
  H and L, the minimum across gates, unknown gates, the memory capacity at
  and around the reserve, gate existence by backend and ceiling, per-lane
  refusal names, the drain-wait independence.
- Manager tests (`tests/core/test_manager_family_maintenance.py`): real
  fenced child processes prove W overlapping startups with and without a
  backend ceiling on both backends; a real Manager at the memory reserve
  publishes `measured_memory_exhausted` in its task-state snapshot, keeps the
  request in the source queue with nothing reserved, keeps the internal lane
  open, and launches the request once memory has room.
- Not run here: `tests/system/test_admission_stress.py` and the full suite
  (the coordinator runs the sweep).

## Verification and gates

```bash
./.venv/bin/python -m pytest -n 4 -m "" tests/core/test_admission.py tests/core/test_admission_recovery.py tests/core/test_connection_admission.py tests/core/test_family_dispatch_scaling.py tests/core/test_family_scheduling.py tests/core/test_family_scheduling_model.py tests/core/test_family_source_reconciliation.py tests/core/test_manager.py tests/core/test_manager_burst_bookkeeping.py tests/core/test_manager_checkpoint_volume.py tests/core/test_manager_family_maintenance.py tests/core/test_manager_runtime_connections.py tests/core/test_manager_services.py tests/core/test_manager_start_observer_failure.py tests/core/test_work_conserving_admission.py tests/system/test_child_admission.py tests/system/test_family_matrix.py tests/system/test_family_recovery.py tests/system/test_family_scheduling.py tests/system/test_measured_admission_pg.py
./bin/pytest-pg <the same targets>
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest -n 4 tests/specs -q
./bin/check-doc-paths
```

Runtime observation (coordinator sweep): see "What the sweep should watch".

## What the sweep should watch

- SQLite concurrency should rise from about 70 toward the PostgreSQL figure,
  because up to W startups now overlap. The limit then moves to the Manager's
  one-launch-per-turn dispatch and to SQLite writer contention.
- SQLite writer contention: W wrappers booting at once each write their first
  `weft.state.tasks.<tid>` snapshot and `weft.log.tasks` events. SimpleBroker
  waits up to `BUSY_TIMEOUT` (5,000 ms) and then retries "database is locked"
  with backoff; exhausted retries surface as `OperationalError` /
  `database is locked` in task wrapper logs and Manager debug logs. Grep the
  run's logs for `database is locked`, `OperationalError` and
  `startup_feedback_snapshot_missing` (a startup settled by the one-second
  missing-publication fallback, a sign of slow boots), and compare
  `startup_handoff` phase timings in `manager_phase_timing` against the
  serialized baseline.
- Memory: grep the Manager serve log for `admission_state` lines with
  `reason` `measured_memory_exhausted` and their `memory_used`,
  `memory_public_limit` and `memory_total` fields, and Manager checkpoints
  for `dispatch_refusals` items with that reason. On macOS also record swap
  (`sysctl vm.swapusage`) and `memory_pressure`, since `available` includes
  file cache.
- Refusal counts: `startup_feedback_pending` should fall on SQLite.

## Rollback

Revert the commit. That restores the predicate, the Manager admission path,
the refusal inventory, the specs and the tests together. Nothing persistent
changes: no queue shape, checkpoint field or setting. A snapshot written by a
new Manager with a `measured_memory_exhausted` refusal is read as
`assessment: unavailable` by an old reader, which is the existing behavior for
an unknown reason; the fact clears at the next publication. No one-way door.

## Out of scope

- Changing W, the startup fraction or the reserve fraction.
- A separate memory reserve setting or a fixed SQLite process ceiling.
- Per-task memory accounting, swap or pressure measurement, cgroup limits.
- Settlement, custody, checkpoint, interest or child boot cost changes.
- `CHANGELOG.md` (left to the landing change).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [MA-1.8] memory reserve | `R_m = ceil(T*f)` with `WEFT_ADMISSION_RESERVE_FRACTION` (the brief's default) | `R_m = ceil(T*0.1)` from the fixed `ADMISSION_MEMORY_RESERVE_FRACTION` | A shared fraction turned a valid 0.75 connection reserve into a 75% memory reserve and refused all public work (see "Why the memory reserve is fixed") | Applied in this change's [MA-1.8] and [MANAGER.18] delta; owner decision open |

## Fresh-eyes review (author)

- [ALT-WT-001]: nothing is charged per task; `memory_mb` is not read; queued
  work costs nothing; the memory gate uses only `T` and `A`.
- [ALT-WT-002]: no timer or cap was added; the memory read rides the existing
  admission observation and its one-second retry.
- Hot loops: a memory refusal records blocked lanes and the existing retry
  deadline, exactly as a connection refusal does.
- Division by zero: `L - H >= 2` whenever `H >= W`, for every gate.
- Large integers: `floor(L * 0.8)` and `ceil(T * 0.1)` were checked against
  exact integer arithmetic for byte-scale values (no mismatch in 400,000
  samples up to 10^13).
- One D-shaped risk is named as A: on a host whose memory use sits above
  `T - ceil(T*0.1)` (90%), every public launch now waits. That
  is the owner-directed behavior, and the refusal says so.
- Tests that depend on admission opening use real host memory unless they
  assert an exact window; a CI host above 90% memory use would refuse
  dispatch in those tests. That is the product behavior, not a test defect.

## Review and execution record

(Coordinator: cross-family review and benchmark results go here.)

Implementation evidence from the authoring agent (SQLite unless noted):

- RED on the baseline (a `git archive` copy of `8acaddf3` with the new
  Manager tests overlaid): both
  `test_startup_window_overlaps_but_bounds_unobserved_processes[300]` and
  `[0]` failed with "second startup did not progress before first feedback:
  received=[<one TID>]" (serialized startup);
  `test_memory_at_reserve_refuses_public_launch_and_keeps_work_queued` failed
  at `assert 0 == 1` (no measured-resource refusal: no memory gate). The pure
  tables call a new API, so their controls are mutations below.
- Sensitivity controls (one focused mutation of `weft/core/admission.py`
  each, restored byte-for-byte, then the target rerun): window over the first
  gate only -> the minimum-over-gates rows fail (`True is False` at
  `unfinished=3` with a narrower second gate); readiness bound hard-coded to 6
  -> the binding test fails (`_startup_window_full(7)` true at W=8); memory
  public limit `<=` -> the memory capacity row at the reserve fails; memory
  gate dropped from the lane union -> the gate table fails
  (`{'public'} != {'internal', 'public'}`); drain-wait on any block -> the
  drain-wait test fails (`'waiting' is None`); memory reserve from the
  backend fraction -> the decoupling test fails (`{'public'} != frozenset()`);
  memory refusal named `measured_capacity_exhausted` -> the Manager refusal
  test fails at the reason tuple.
- First PostgreSQL run: 2 failed, 939 passed, both
  `test_native_postgres_ceiling_denies_real_pressure_and_resumes` cases
  (shared reserve fraction; see "Why the memory reserve is fixed"). After the
  fix that test passed alone (2 passed).
- Final: the verification target list above on SQLite with `-n 4 -m ""`:
  946 passed, 10 skipped; the same targets through `./bin/pytest-pg`: 943
  passed, 13 skipped; `ruff check .` clean; `ruff format --check` on touched
  Python files clean; repo mypy: no issues in 503 source files;
  `tests/specs`: passed; `bin/check-doc-paths`: OK;
  `bin/check-dom15-fixtures`: OK.

### Cross-family review (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-09,
plan and implementation at `a946573b` against `8acaddf3`. Verdict: no blocker.
F1 (nit): the launch-failure register row described only the public-lane
delay; removing `_admission_enabled` also activates the internal-failure branch
that delays both lanes. Accepted, fixed (row expanded; no runtime change). The
reviewer found the separate memory reserve justified (the fixed 10% value
remains an owner decision), ran all 82 pure admission cases, and a
12,000-observation differential check against `8acaddf3` that preserved
PostgreSQL capacity and drain-wait results across known, exhausted and unknown
memory. Broker/process suites were not rerun by the reviewer. Throughput and
resource effects are measured by the coordinator's sweep.
