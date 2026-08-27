# SimpleBroker 7.5.1 Compatibility Upgrade Plan

Status: draft
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4]
Superseded by: none

Class: 5: spec-changing. The requested dependency update changes normative
package floors and requires a small source-typing adaptation. Hardening applies
because the queue dependency sits on the durable execution path, although no
Weft queue semantics, persisted format, or process lifecycle are intended to
change.

Escalation history: the version and lock changes began as the independently
reviewed release-prerequisite slice in
[`2026-08-25-manager-admission-control-plan.md`](./2026-08-25-manager-admission-control-plan.md).
The first full verification run then exposed three closeable-iterator typing
errors and one invalid SQLite target fixture in addition to the expected exact
floor assertions. Those compatibility changes are separable from admission
control, so this plan owns them before source, test, or normative spec edits.

## Goal

Upgrade Weft to the published `simplebroker` 7.5.1 and `simplebroker-pg`
3.10.0 pair, adopt the released public types and stricter backend-option
validation with the smallest possible compatibility edits, and inspect and
record the new PostgreSQL connection-pressure API without implementing Manager
admission control.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
  owns the coordinated dependency floor, delegation boundary, and isolated
  SimpleBroker configuration snapshot used by Weft.
- `../simplebroker/CHANGELOG.md` 7.5.0 and 7.5.1 records the public
  `Queue.backend_name` addition, PostgreSQL connection statistics, public
  closeable iterator types, and plugin-owned backend-option validation.
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-2],
  [SB-API-13] defines target option ownership and the PostgreSQL statistics
  helper. The released PyPI wheels, not the sibling checkout, are the
  compatibility authority for this slice.
- [`2026-08-25-manager-admission-control-plan.md`](./2026-08-25-manager-admission-control-plan.md)
  remains the draft consumer plan for the new PG helper. This slice supplies
  its released dependency prerequisite but implements no gate.
- [`2026-08-24-simplebroker-7-4-1-compatibility-plan.md`](./2026-08-24-simplebroker-7-4-1-compatibility-plan.md)
  is completed historical context for the current dependency and queue typing
  boundary. It is not normative.

## Context and Key Files

Files to modify:

- `pyproject.toml`, `uv.lock`: coordinated core and PG release floors;
- `weft/commands/queue.py`: preserve the public closeable-iterator contract in
  the three small compatibility generator adapters;
- `tests/system/test_optional_extras.py`: exact coordinated floor assertions;
- `tests/tasks/test_tasks_simple.py`: use a valid option-free SQLite
  `BrokerTarget` while retaining the public-target construction proof;
- `docs/specifications/04-SimpleBroker_Integration.md`, `README.md`, and
  `CHANGELOG.md`: normative floor and release traceability;
- `docs/lessons.md`: record the corrected PostgreSQL test-provisioning rule;
- this plan and `docs/plans/README.md`: execution record and index.

Reuse and boundaries:

- import the public package-root `simplebroker.CloseableIterator`; do not add a
  local protocol, cast, ignore, or private import;
- keep `_read_generator_after()`, `_peek_generator_after()`, and
  `_move_generator_after()` as the existing compatibility adapters. Their
  generator objects already have `close()` and forward closure through
  `yield from`; only the annotation must stop erasing that fact;
- keep `closing_queue_iterator()` as the single early-exit cleanup owner;
- do not add SQLite backend options. SimpleBroker 7.5.1 intentionally rejects
  them because the plugin implements none;
- inspect `simplebroker_pg.get_connection_stats` from the installed wheel.
  Weft must not import it in production until the admission-control spec is
  promoted.

Released API facts to record, not reimplement:

- `simplebroker_pg.get_connection_stats(queue: Queue) -> dict[str, int]` is a
  package-root function, not a `Queue` method;
- the ordinary dictionary has exactly `numbackends`, `max_connections`,
  `superuser_reserved_connections`, and `reserved_connections` integer keys;
- a non-PostgreSQL Queue is rejected before a connection is opened;
- the function uses the Queue's normal connection lease and the extension's
  private locked/retried core probe. It does not use sidecar or expose SQL;
- `numbackends` is a conservative, non-atomic server-wide snapshot that may
  include database-attached workers that do not consume client slots.

## Invariants and Constraints

- Queue payload, timestamp, move, settlement, watcher, and cleanup behavior do
  not change.
- Weft still passes one complete isolated `ResolvedConfig`; ambient
  `BROKER_*` values remain unable to redirect its broker.
- Backend API remains v7. Core 7.5.1 and PG 3.10.0 roll out and roll back as a
  pair because the extension requires the new core floor.
- Valid SQLite targets remain option-free. Invalid non-empty SQLite
  `backend_options` fail explicitly at the SimpleBroker plugin boundary.
- No admission config, Manager gate, PostgreSQL SQL, private probe import, or
  live-task counting enters this slice.
- Preserve unrelated dirty plan work and stage by explicit file list if the
  owner later lands this slice.

## Hardening Comprehension Checks

1. Why is `CloseableIterator[Any]` true for each adapter rather than a cast?
   The adapter is itself a generator with `close()`, and `yield from` forwards
   active close to the delegated SimpleBroker iterator.
2. Why is the SQLite fixture changed rather than production code? SQLite
   implements no backend options, and 7.5.1 intentionally rejects a non-empty
   mapping at the owning plugin boundary. Weft production supplies none.
3. Why must the packages move together? `simplebroker-pg` 3.10.0 declares
   `simplebroker>=7.5.1` and consumes the new private core probe.
4. Why does this slice not call `get_connection_stats()`? Availability of the
   primitive is not authority to add the separately specified Manager gate.
5. What stops rollout? Any changed valid queue payload/settlement behavior,
   config isolation regression, private API need, unrelated lock churn, or a
   real supported Weft path that depends on SQLite backend options.

## Spec Baseline and Promotion

- Baseline: `0f19367a5781ead326f632853b3c49bc07e9623d` for
  `docs/specifications/04-SimpleBroker_Integration.md` before this slice.
- Release baseline: PyPI `simplebroker` 7.5.1 and `simplebroker-pg` 3.10.0,
  published 2026-08-26. Frozen sync and installed-wheel import must prove that
  the sibling checkout is not supplying the runtime package.
- Promotion strategy B: update the exact dependency-floor statements,
  implementation compatibility, tests, README, changelog, and lockfile as one
  coherent slice. A mixed state offers no useful contract.

## Proposed Spec Delta

Exact [SB-0.1] replacement:

> Weft requires SimpleBroker 7.5.1 or newer. Installations using the optional
> PostgreSQL backend require `simplebroker-pg` 3.10.0 or newer. These
> coordinated floors provide backend API v7, bounded dump watermarks,
> immutable invocation/handle configuration snapshots, typed queue result
> overloads, public closeable queue iterator types, and the synchronized
> watcher lifecycle contract used by Weft.

Exact [SB-0.4] edit: replace `SimpleBroker 7.4.1's public
resolve_isolated_config()` with `SimpleBroker 7.5.1's public
resolve_isolated_config()` and add this plan beside the [SB-0] implementation
backlinks.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Tasks

1. Verify releases and capture red evidence.
   - Resolve exact versions from PyPI and confirm PG 3.10.0 requires core
     7.5.1.
   - Sync from the lock and inspect the installed wheel paths, package-root
     exports, function signature, field names, validation, and SQLite rejection.
   - Retain the first full-suite receipt: four exact-floor failures, one
     unsupported-SQLite-options fixture failure, and three mypy assignments
     where an adapter erased `CloseableIterator` to `Iterator[Any]`.

2. Apply the smallest compatibility changes.
   - Raise the root core floor and every `pg`/`all`/`dev` PG floor; update only
     the related lock data by running
     `uv lock --upgrade-package simplebroker --upgrade-package simplebroker-pg`.
     Expected churn is the two registry artifact records and Weft's generated
     `requires-dist` metadata; reject unrelated resolution changes.
   - Annotate the three existing generator adapters with public
     `CloseableIterator[Any]`. Do not change their bodies or callers.
   - Update exact floor tests and make the SQLite public-target fixture use an
     empty backend-options mapping.

3. Promote documentation and traceability.
   - Apply the exact [SB-0.1]/[SB-0.4] delta after independent review.
   - Update README version statements and the existing Unreleased changelog
     entry. Link the plan from the spec and keep the admission plan's release
     facts synchronized.

4. Verify and review.
   - Run focused queue, task, packaging, spec, and architecture tests, then the
     full fast suite against the locked pair.
   - Run full mypy, ruff, `uv lock --check`, frozen all-extras sync, installed
     API smoke, and `git diff --check`.
   - Use `bin/pytest-pg` for live PG evidence. It auto-provisions a temporary
     Docker database and injects `WEFT_PG_TEST_DSN`; an unset ambient DSN is not
     a test gap. Report a gap only when Docker/uv is unavailable or the wrapper
     cannot provision its database.
   - Complete independent plan/spec review before promotion and independent
     work review before any landing claim.

## Testing Plan

- `tests/system/test_optional_extras.py`: every declared floor is exact.
- `tests/tasks/test_tasks_simple.py`: Consumer accepts a valid public SQLite
  `BrokerTarget` and retains it unchanged.
- `tests/commands/test_queue.py`: real SQLite read, peek, and move adapters
  preserve output and deterministic early-close behavior.
- API smoke: installed versions are exact; `get_connection_stats` has signature
  `(Queue) -> dict[str, int]`; it is exported only from `simplebroker_pg`,
  rejects a SQLite Queue, and returns the documented exact key set on live PG.
- Full suite/type/lint gates catch broader dependency drift.

## Verification Commands and Expected Outcomes

Capture the backstitch baseline before spec promotion:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-sb751-backstitch-before.json || test $? -eq 1
```

The expected exit is `1` from existing repository debt. The baseline was
captured after this plan was added and before [SB-0.1]/[SB-0.4] promotion.

Run focused gates first, then full gates from the frozen environment:

```bash
. ./.envrc
uv lock --check
uv sync --all-extras --frozen
./.venv/bin/python -m pytest -q \
  tests/system/test_optional_extras.py \
  tests/tasks/test_tasks_simple.py \
  tests/commands/test_queue.py
./.venv/bin/python -m pytest -q \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py \
  tests/architecture/test_import_boundaries.py
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/pytest-pg --fast \
  tests/system/test_optional_extras.py \
  tests/tasks/test_tasks_simple.py \
  tests/commands/test_queue.py
git diff --check
```

Every command above must exit `0`; the full default suite may retain only its
documented PostgreSQL-gated skips.

Run this installed-artifact smoke probe; it must print `released API smoke
test: passed` and open no PostgreSQL connection:

```bash
./.venv/bin/python - <<'PY'
from importlib.metadata import version
from typing import get_type_hints

from simplebroker import Queue
from simplebroker_pg import get_connection_stats

assert version("simplebroker") == "7.5.1"
assert version("simplebroker-pg") == "3.10.0"
assert get_type_hints(get_connection_stats) == {
    "queue": Queue,
    "return": dict[str, int],
}
assert not hasattr(Queue, "get_connection_stats")
queue = Queue("released-api-probe")
try:
    assert queue.backend_name == "sqlite"
    try:
        get_connection_stats(queue)
    except ValueError as exc:
        assert str(exc) == "get_connection_stats() requires a PostgreSQL Queue"
    else:
        raise AssertionError("SQLite Queue was accepted")
finally:
    queue.close()
print("released API smoke test: passed")
PY
```

After promotion, rerun the exact backstitch command with output
`/tmp/weft-sb751-backstitch-after.json`. Compare `issues` by
`(severity, code, path, line, section_id, message)` after removing line numbers
for the touched spec and plan. Acceptance is no new error or warning keyed to
`docs/specifications/04-SimpleBroker_Integration.md`, this plan,
`weft/commands/queue.py`, `tests/system/test_optional_extras.py`, or
`tests/tasks/test_tasks_simple.py`. If `../backstitch` is unavailable, the gate
is unpassed and must be reported.

## Rollout and Rollback

Roll out core 7.5.1 and PG 3.10.0 together. No storage migration is introduced
by Weft. Rollback reverts the source/docs/tests, both dependency floors, and
the lock as one unit. Do not leave PG 3.10.0 installed with a core below 7.5.1.
The stricter SQLite option rejection is an upstream contract: any real caller
depending on unsupported SQLite options is a rollout stop requiring explicit
design, not a reason to restore silent acceptance in Weft.

## Independent Review

The reviewer must check that the version delta matches the released artifacts,
the iterator annotation is structurally true, the SQLite fixture was invalid
rather than evidence of a supported Weft path, and no admission-control
implementation leaked into this slice.

## Review Log

| Stage | Finding | Disposition |
|-------|---------|-------------|
| Author fresh-eyes | The first plan version treated API availability as a Weft-used capability and did not separate the exact spec delta, hardening questions, executable gates, or backstitch comparison. | Accepted. Kept [SB-0.1] limited to behavior this compatibility slice uses; recorded the PG API only as inspected context; added the missing hardening, spec, command, and traceability sections. |
| Independent plan/spec review, pass 1 | P1: proposed [SB-0.1] dropped typed result overloads; P1: mandatory executable/hardening structure was incomplete. P2: API use was overstated, API facts were underspecified, lock churn wording was wrong, and the deviation row was not a spec deviation. | Accepted all findings. Retained the existing overload contract, added exact released API facts, limited normative claims to current use, named mechanical lock regeneration and expected generated metadata, cleared the deviation table, and added required structure. Focused re-review pending. |
| Independent plan/spec review, pass 2 | PASS. The revised delta is minimal; API facts, lock churn, iterator typing, SQLite fixture, hardening gates, and backstitch acceptance match the released contract. | No further plan change required before promotion. |
| Owner correction during verification | An unset ambient `WEFT_PG_TEST_DSN` was initially treated as a live-PG gap. | Accepted. `bin/pytest-pg` was inspected and used; it auto-provisions PG18 in Docker, supplies the DSN, and cleans up. Added the durable lesson and made the wrapper the plan's required PG gate. |
| Independent completed-work review | PASS. No P0-P2 findings in dependency/lock facts, iterator typing, SQLite validation, spec/docs, tests, API analysis, or admission-control scope. | Accepted. Final current-state gates remain the completion authority. |
| Independent correction audit | PASS. The lesson and revised PG gate accurately describe `bin/pytest-pg`; the recorded PG18 statistics and focused test evidence match the run. | No further change required. |

## Completion Evidence

- dependency and installed API receipt: PyPI wheels installed as
  `simplebroker==7.5.1` and `simplebroker-pg==3.10.0`; package-root signature,
  resolved type hints, export boundary, exact source, and SQLite pre-I/O
  rejection passed.
- focused and full pytest: focused package/task/queue and spec/architecture
  suites passed; full default suite passed 4,262 with two expected PG-gated
  skips.
- mypy: passed 187 source files.
- ruff: passed repository-wide.
- lock/frozen sync/diff checks: `uv lock --check`, frozen all-extras sync,
  `git diff --check`, and backstitch comparison passed; backstitch retained its
  existing aggregate debt with zero new scoped error/warning.
- live PostgreSQL status: the auto-provisioned PG18 server returned the exact
  four keys with `max_connections=300` and `numbackends=1`; a second call on
  the same persistent Queue passed. Focused `bin/pytest-pg` passed 48 tests.
- independent review and dispositions: plan pass 1 findings were all accepted;
  plan pass 2 and completed-work review passed with no P0-P2 findings.
- implementation commit: fill only when the owner lands the explicit
  task-owned file set; status remains draft until then.
