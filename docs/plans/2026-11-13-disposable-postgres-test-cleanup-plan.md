# Disposable PostgreSQL Test Cleanup Ownership

Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-3]
Superseded by: none

Class: 3

## Problem And Evidence

The full PostgreSQL release gate fails near one third completion on macOS with
`psycopg.OperationalError: Can't assign requested address`. The failure moves
between unrelated tests, exact failed tests pass in isolation, the PostgreSQL
container remains healthy, and live connections remain far below the server's
300-connection limit. The test helper both creates and drops a schema for each
temporary project root. Each drop opens another short-lived TCP connection,
even when `bin/pytest-pg` owns a disposable container that is removed after the
suite. The expanded suite crosses the host's finite ephemeral-port reuse
window. This is redundant harness cleanup, not product behavior or database
capacity.

## Change

`bin/pytest-pg` marks its wrapper-owned PostgreSQL target as disposable.
`tests/helpers/test_backend.py` treats container removal as the database cleanup
owner in that mode: per-root cleanup only forgets process-local preparation
records. Runs against an external or shared PostgreSQL DSN retain explicit
schema drops. Disposal authority requires the marker and DSN to come from the
same environment source. Isolation relies on root paths being unique within one
container lifetime, which the suite's `tmp_path` and `tempfile` roots guarantee.
`--keep-container` intentionally leaves its accumulated schemas for debugging;
the user then owns removal of that retained container.

No worker-count, timeout, product connection, or production cleanup behavior
changes. Removing the marker restores the prior explicit schema teardown.

## Verification

1. Unit tests prove the wrapper sets the marker, disposable cleanup avoids the
   backend plugin and clears matching cache entries, and ordinary cleanup still
   calls the plugin.
2. Ruff, mypy, and repository documentation gates pass.
3. A fresh `bin/pytest-pg --all` run completes at the normal worker count.
4. An independent reviewer checks the causal premise, ownership boundary, and
   falsifiability of the tests before this plan is completed.

## Review Log

Tier-1 attempt 1 used Antigravity Argon in plan/sandbox mode. Containment
denied a requested command, but the tool returned no review text, so the result
was invalid and not used.

Tier-1 attempt 2 used Claude Opus with the verified `Read,Grep,Glob`-only tool
surface. Verdict: `no blocker`. F1-F3 were accepted by making the two cleanup
paths independently falsifiable and naming the explicit-env precedence
contract. F4 was accepted by requiring marker and DSN provenance to match.
F5-F6 were accepted in the Change text. The non-actionable spec observation
was accepted by updating [TS-0]. The unchanged-concurrency PostgreSQL gate then
completed with 7,054 passed and 41 skipped.
