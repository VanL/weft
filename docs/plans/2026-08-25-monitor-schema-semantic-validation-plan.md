# Monitor Schema Semantic Validation and v5 Migration Repair Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]
Superseded by: none

Class: 5 — spec-changing and risky. This changes the normative acceptance
contract for persisted Monitor tables and repairs the only supported schema
migration edge. Hardening and independent review are mandatory.

## Goal

Restore Monitor startup and the v5-to-v6 migration for every schema layout
produced by supported Weft releases. Replace physical-layout equality with a
semantic compatibility check: validate only database properties that Weft's
named reads, named writes, identity rules, migrations, and required query
paths depend on. Remove tests and failure modes for irrelevant physical order
and irrelevant extra non-unique indexes.

The acceptance standard is deliberately two-sided:

1. A property that can change Weft's read values, write acceptance, row
   identity, migration result, or required query plan is validated and has a
   firing test.
2. A property with no such dependency is neither rejected nor given a
   synthetic permutation test.

## Source Documents

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4a] owns the
  Monitor sidecar schema, the v5-to-v6 migration, and startup verification.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] owns the Monitor's
  durable observation flow and its operational checkpoint behavior. It is a
  consulted invariant, not a schema-acceptance surface changed by this plan.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.13.1], and
  [OBS.17] constrain Monitor collation, cleanup proof, and deletion safety.
  This plan does not change those rules.
- `docs/plans/2026-08-10-canonical-contract-and-dead-code-cleanup-plan.md` is
  historical context for the strict schema check introduced in v0.9.95. Its
  exact-order decision is superseded by the spec delta below; the old plan is
  not normative.
- Released source history at `72bc99dc`, the six additive Monitor-column
  changes through `c438403a`, and tag `v0.9.94` define the real evolved v5
  fixture. Commit `7c245c91` introduced the failing ordered comparison shipped
  in v0.9.95.

## Context and Key Files

- `weft/core/monitor/store.py`: `_MonitorTableSpec`,
  `_MonitorTableAccess.verify_schema_structure()`, schema-version dispatch,
  and the v5-to-v6 transaction.
- `weft/core/monitor/sql.py`: canonical DDL and SQLite/PostgreSQL catalog
  queries. Every production `SELECT` and `INSERT` already names its columns.
- `tests/core/test_monitor_store.py`: schema rejection tests and the current
  false v5 fixture, which creates v6 first and changes only version metadata.
- `tests/core/test_monitor_sql.py`: SQL builder/catalog-query coverage if the
  column introspection query changes.
- `weft/core/monitor/task_monitor.py` and its focused tests: startup exposes
  store failure as operational degradation; task execution remains available.
- `docs/specifications/04-SimpleBroker_Integration.md`, `CHANGELOG.md`, this
  plan, `docs/plans/README.md`, and `docs/lessons.md`: normative wording,
  release note, traceability, and the durable migration-fixture lesson.

Read first:

- `_task_columns` and `_record_from_row()` in `store.py`: positional Python
  decoding follows an explicit `SELECT` projection, not table ordinal order.
- `upsert_task_record()`, task-message/deferred writers, and their SQL builders:
  every insert names its target columns.
- `ensure_schema()`: current v5 dispatch verifies v6 physical order before the
  data migration can run.
- The release-produced v5 DDL sequence: the original collation table ended in
  `updated_at_ns`; six later `ALTER TABLE ... ADD COLUMN` operations appended
  columns after it. Fresh v5 databases therefore have a different ordinal
  layout from evolved v5 databases even though both have the same usable
  schema.

Comprehension checks before implementation:

1. Why does `_record_from_row()` depend on `_task_columns` order while the
   database table does not?
2. Why must the v5 preflight prove migration inputs before v6 data validation,
   and why must the schema-version write remain the final transactional step?
3. Which order remains load-bearing? Answer: primary-key column order, required
   index column order, and code-owned query projection order.

## Invariants and Constraints

- Keep Monitor schema version 6. This fix changes acceptance and migration
  reachability, not the stored representation.
- Keep the v5-to-v6 migration transactional. A failed migration leaves version
  5 and all owned data unchanged.
- Do not rebuild a table to change column order. No data copy or order-only DDL
  is permitted.
- Keep all production reads and writes explicitly column-named. Do not add
  `SELECT *`, positional inserts, or cursor-description coupling.
- Validate current-version owned JSON and tombstone state exactly as today.
  This plan relaxes only non-semantic catalog equality.
- Preserve ordered primary-key definitions. They own row identity and conflict
  targets.
- Preserve each retained required named index's owner table, uniqueness,
  B-tree/non-partial form, validity/readiness, and ordered columns. `CREATE
  INDEX IF NOT EXISTS` makes a same-name wrong or unusable index a correctness
  risk. Additional non-unique indexes do not change accepted rows and are not
  an invalid schema inventory. A unique index fails only when its key is
  stronger than the primary key; an exact non-partial B-tree primary-key copy
  is redundant only when its key collations and PostgreSQL operator classes
  also match the primary index.
- Continue creating all ten schema-6 index names on new stores so a fresh store
  remains readable by v0.9.95-v0.9.97 during rollback. The new validator
  requires only evidence-backed indexes. Stopping creation or dropping a legacy
  index requires a future schema version.
- Validate required column names by lookup, independent of ordinal position.
  Validate backend-normalized storage family/capacity and nullability only
  where current reads or writes depend on them. Do not compare raw type tokens,
  default-expression spelling, DDL text, or catalog row order.
- An additional column is compatible only when Weft can omit it from every
  named insert: it is nullable, identity-backed, or has a catalog default that
  is provably a non-NULL SQL literal. `NOT NULL DEFAULT NULL`, an unproved
  default expression, and a non-nullable generated column are write-required
  and fail. The validator does not execute arbitrary default expressions and
  is not a general auditor for DBA-added triggers or `CHECK` constraints;
  those operator mutations remain outside this contract.
- Use backend-local normalization: SQLite affinity semantics are not forced to
  imitate PostgreSQL type names, and PostgreSQL 64-bit message/time columns
  must retain sufficient range.
- Do not introduce a generic schema framework or move this concern into
  SimpleBroker. These are Weft-owned sidecar tables.
- Do not add arbitrary column-permutation, type-spelling, index-ordering, or
  operator-mutation matrices. One release-lineage migration fixture and one
  representative firing case per semantic rule class are the test ceiling.
- Preserve unrelated worktree changes and do not commit without user
  instruction.

## Validation Decision Table

| Catalog property | Validate? | Reason and test policy |
|---|---|---|
| Required Monitor table exists | Yes | Named SQL cannot run without it; retain one firing missing-table case. |
| Required column exists by name | Yes | Named reads/writes require it; compare mappings/sets, not tuples, and retain representative missing-column coverage. |
| Physical column ordinal | No | No production SQL or decoder consumes it; cover only through the real evolved-v5 migration fixture. |
| Normalized required-column storage family/capacity | Yes | Text/integer mismatch or a narrow PostgreSQL integer can change values or reject valid IDs; use a small normalization unit table and one integration rejection. |
| Required-column nullability | Yes, when a current reader/writer relies on it | It affects accepted rows and possible reader values; do not pin irrelevant default syntax. |
| Extra safely omittable column | No rejection solely for existence | Named SQL can omit a nullable, identity-backed, or proven non-NULL-literal-defaulted extra; use one focused acceptance and write case, not a permutation suite. |
| Extra write-required column | Yes, reject | A `NOT NULL` extra with no default/generation breaks every named insert; use one firing case. |
| Primary-key columns and order | Yes | Identity and `ON CONFLICT` semantics depend on both. |
| Retained index table, uniqueness, access method, partial status, validity/readiness, columns, and column order | Yes | A same-name wrong or unusable shape blocks idempotent creation or cannot serve the required query. |
| Extra non-unique secondary index | No | It is operationally harmless; remove exact-inventory rejection. |
| Extra unique secondary index | Reject only when stronger than the primary key | It changes accepted rows unless it is a non-partial B-tree copy of the exact ordered primary-key columns with identical key collation and operator-class semantics. |
| DDL text, type aliases with the same backend semantics, default spelling, catalog order | No | None is consumed by runtime behavior; normalize or ignore. |
| v6 owned JSON/tombstone canonicality | Yes | Readers and cleanup safety depend on it; retain existing firing tests. |

### Column semantics to encode

The descriptor registry groups every required column exactly once:

- Text: meta `key`, `value_json`; collation `context_key`, `tid`, `name`,
  `runner`, `parent_tid`, `role`, `status`, `terminal_event`,
  `terminal_status`, `taskspec_summary_json`, `state_json`, `lifecycle_json`,
  `resources_json`, `diagnostics_json`, `bookkeeping_json`, `suspect_reason`,
  `disposition_reason`; message `context_key`, `tid`, `queue_name`, `event`,
  `status`; deferred `context_key`, `report_id`, `record_type`, `body_json`,
  `first_external_error`, `last_external_error`.
- 64-bit integer: meta `updated_at_ns`; collation `terminal_message_id`,
  `first_message_id`, `last_message_id`, `first_seen_at_ns`, `last_seen_at_ns`,
  `started_at_ns`, `completed_at_ns`, `summary_emitted_at_ns`,
  `raw_deleted_at_ns`, `suspect_at_ns`, `disposition_at_ns`,
  `task_control_deleted_at_ns`, `reserved_cleanup_checked_at_ns`,
  `orphan_raw_recovery_checked_at_ns`, `updated_at_ns`; message `message_id`,
  `observed_at_ns`, `selected_for_delete_at_ns`, `deleted_at_ns`; deferred
  `created_at_ns`, `updated_at_ns`, `last_attempt_at_ns`, `flushed_at_ns`.
- Ordinary integer: collation `terminal_seen`, `return_code`,
  `reserved_probe_needed`; deferred `attempt_count`.

SQLite accepts TEXT affinity for the text class and INTEGER affinity for both
integer classes. PostgreSQL accepts `text` or unbounded `character varying` for
text, `bigint` for the 64-bit class, and `integer` or `bigint` for the ordinary
integer class. Other types require evidence and a spec edit rather than a new
silent alias.

The actual schema must accept SQL `NULL` for every value the current writers
may pass as `None`: collation `name`, `runner`, `parent_tid`, `role`, `status`,
`terminal_event`, `terminal_status`, `terminal_message_id`, `return_code`,
`first_seen_at_ns`, `last_seen_at_ns`, `started_at_ns`, `completed_at_ns`,
`summary_emitted_at_ns`, `raw_deleted_at_ns`, `suspect_reason`, `suspect_at_ns`,
`disposition_reason`, `disposition_at_ns`, `task_control_deleted_at_ns`,
`reserved_cleanup_checked_at_ns`, and
`orphan_raw_recovery_checked_at_ns`; message `event`, `status`,
`observed_at_ns`, `selected_for_delete_at_ns`, and `deleted_at_ns`; deferred
`first_external_error`, `last_external_error`, and `flushed_at_ns`.

All other required columns must be catalog `NOT NULL` because readers or
writers rely on a value: meta `key`, `value_json`, `updated_at_ns`; collation
`context_key`, `tid`, `terminal_seen`, `first_message_id`, `last_message_id`,
`taskspec_summary_json`, `state_json`, `lifecycle_json`, `resources_json`,
`diagnostics_json`, `bookkeeping_json`, `reserved_probe_needed`,
`updated_at_ns`; message `context_key`, `tid`, `queue_name`, `message_id`;
deferred `context_key`, `report_id`, `record_type`, `body_json`,
`created_at_ns`, `updated_at_ns`, `attempt_count`, `last_attempt_at_ns`.
SQLite's canonical `key TEXT PRIMARY KEY` reports `notnull=0`; accept that one
backend-specific catalog exception because primary-key verification owns key
identity and Monitor writes non-null keys. PostgreSQL and all other listed
columns must report the declared nullability above.

SQLite introspection uses `PRAGMA table_xinfo` fields `name`, declared `type`,
`notnull`, `dflt_value`, `pk`, and `hidden`. PostgreSQL introspection uses
`information_schema.columns` fields `column_name`, `data_type`, `udt_name`,
`character_maximum_length`, `is_nullable`, `column_default`, `is_generated`,
and `is_identity`. Physical ordinal is intentionally not selected or compared.

### Index dependency audit and intended disposition

| Index | Owning query/dependency | Disposition |
|---|---|---|
| `idx_weft_monitor_collations_reserved_cleanup` | `select_reserved_cleanup_pending_tasks()` | Retain; leading equality/null predicates and `last_message_id` order match. |
| `idx_weft_monitor_collations_disposition_terminal` | `select_summary_ready_terminal_tasks()` and terminal disposition backfill | Retain subject to SQLite/PostgreSQL query-plan proof. |
| `idx_weft_monitor_collations_control_cleanup` | `select_unemitted_terminal_tasks()` | Retain; it bounds context, terminal state, and unemitted summaries. The similarly named OR-shaped cleanup selector is not its owner. |
| `idx_weft_monitor_collations_orphan_recovery` | `select_raw_deleted_task_log_recovery_tids()` and raw-not-deleted cleanup | Retain; it bounds recovery candidates but does not remove the final sort. |
| `idx_weft_monitor_collations_disposition_open` | `select_retirable_task_collations()` | Retain; it bounds disposed families. Open-summary selectors use the disposition-terminal index. |
| `idx_weft_monitor_deferred_pending` | pending deferred write/error selectors | Retain; it matches context, pending state, and creation order. |
| `idx_weft_monitor_collations_terminal` | No distinct query; its usable prefix is duplicated by the disposition-terminal index | Stop requiring, but keep creating for v0.9.95-v0.9.97 rollback compatibility. |
| `idx_weft_monitor_collations_last_seen` | No sargable `last_seen_at_ns` predicate; retirement uses `COALESCE` | Stop requiring, but keep creating for v0.9.95-v0.9.97 rollback compatibility. |
| `idx_weft_monitor_collations_reserved_probe` | Reserved cleanup is served by the longer reserved-cleanup index with the same prefix | Stop requiring, but keep creating for v0.9.95-v0.9.97 rollback compatibility. |
| `idx_weft_monitor_messages_tid` | The task-message primary key already begins `(context_key, tid)` | Stop requiring, but keep creating for v0.9.95-v0.9.97 rollback compatibility. |

Before changing the index registry, capture SQLite `EXPLAIN QUERY PLAN` and
PostgreSQL `EXPLAIN` evidence on representative cardinality for every proposed
retained index. If an index is not selected or does not materially bound the
owning query, stop requiring it and record the evidence rather than inventing a
test for it. Schema 6 still creates all ten for same-version rollback. Physical
pruning is deferred to a future version. This audit is a stop gate, not
permission to redesign queries.

The audit ran on SQLite 3.50.4 with 120,000 collation rows, 120,000 message
references, and 60,000 deferred rows. `EXPLAIN QUERY PLAN` selected all six
required indexes; dropping each weakened its owning plan. Dropping each of the
four legacy candidates either left the plan unchanged or caused the
task-message primary key to take over identically. PostgreSQL was checked by
B-tree left-prefix analysis only because `WEFT_PG_TEST_DSN` was not configured;
live PostgreSQL remains an unpassed gate.

## Spec Baseline

- `f59a1fe808274e45a851439e116acf572c79e330` — repository and governing specs
  at plan authoring time.
- Incident baseline: v0.9.95 through v0.9.97 reject a release-evolved v5
  collation table before the v5-to-v6 migration because
  `verify_schema_structure()` compares ordered column tuples.
- Plan type: implementation with spec revision.
- Promotion strategy: B — land the [SB-0.4a] contract wording, code, regression
  fixture, changelog, and backlinks atomically. The old exact-order wording
  must not coexist on main with the relaxed implementation.

## Proposed Spec Delta

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4a]

Replace “Schema 6 has this exact ordered table structure” with:

> Schema 6 requires the following named table columns and key structures. The
> displayed column order is descriptive, not a physical-layout contract.
> Monitor SQL names every selected and inserted column; physical column ordinal
> position must not affect schema acceptance, reads, writes, or migration.

Immediately after the four table lists, add:

> Startup validates each required column by name and by backend-compatible
> storage family, capacity, and required nullability. Equivalent backend type
> spellings are accepted. Raw DDL text, physical column position, default
> expression spelling, and catalog row order are not schema contracts.
> An additional column is accepted only when named Monitor inserts may safely
> omit it because it is nullable, identity-backed, or has a catalog default
> provably equal to a non-NULL SQL literal. An unproved default expression, a
> non-nullable generated column, and `NOT NULL DEFAULT NULL` are incompatible.
> Operator-added triggers and `CHECK` constraints remain outside Weft's
> schema-repair responsibility.

Add the exact text/integer/nullability groupings from `Column semantics to
encode` above as the compact normative schema descriptor. This is the source
for the implementation registry; the DDL builders remain an implementation of
it rather than the source of truth.

Replace “The exact schema-6 secondary-index inventory whose names begin
`idx_weft_monitor_` is” with:

> The required schema-6 secondary indexes are:

After that index list, add:

> Each required index name must resolve to the listed table, non-unique,
> non-partial B-tree form, ordered columns, and, on PostgreSQL, valid and ready
> catalog state because idempotent creation and current query behavior depend
> on that shape. Additional non-unique indexes do not invalidate the schema
> solely by existing. A unique secondary index is incompatible when its key is
> stronger than the primary key; an exact non-partial B-tree copy of the
> ordered primary key is redundant and accepted only when SQLite key
> collations or PostgreSQL key collations and operator classes match the
> primary index.

Move the bullets for `idx_weft_monitor_collations_terminal`,
`idx_weft_monitor_collations_last_seen`,
`idx_weft_monitor_collations_reserved_probe`, and
`idx_weft_monitor_messages_tid` out of the required-index list after the index
dependency gate confirms the disposition above, and add:

> Schema 6 creation continues to create these four non-required legacy indexes
> so a newly created v6 store remains acceptable to v0.9.95-v0.9.97 during
> rollback. Current validation does not require or reject them. Stopping their
> creation or removing an existing copy requires a future schema version.

Keep the six evidence-backed bullets as required. If the gate stops requiring
a further candidate, update this plan's decision table and proposed delta
before implementation continues; do not silently diverge. Schema 6 continues
creating it for rollback.

Replace the version-6 verification sentence with:

> Version 6 verifies the required tables, semantic required-column definitions
> independent of physical order, ordered primary keys, and required named-index
> shapes before reading owned data. It performs no schema DDL and does not
> recreate a missing required object.

Add to the v5-to-v6 migration paragraph:

> Version-5 preflight accepts both fresh and release-evolved physical column
> orders. Before any DDL it read-only verifies the version-5 meta, collation,
> and child-message tables, their named migration-input columns, and their
> primary keys. An already-present deferred-write table is verified at the
> same point. Only then may migration preparation create an absent
> deferred-write table, create a missing retained non-unique index, and drop
> the obsolete delete-state index. It must not create a missing data-bearing
> base table or add a missing base column. The migration then rewrites owned
> data, verifies version-6 semantic structure and owned data, and writes
> version 6 only as the transaction's final step.

Add this plan to [SB-0.4a]'s spec `Related Plans` section and keep the
`store.py`/`sql.py` implementation mapping reciprocal. [MF-5] remains
unchanged.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

Record only implementation behavior that differs from the approved proposed
delta; an intended Strategy-B spec revision is not a deviation.

## Implementation Record

- Failure-first fixture: the release-v5 base DDL plus its six actual additive
  columns failed current `HEAD` in
  `test_monitor_store_migrates_v5_owned_json_and_obsolete_delete_state` with
  `invalid Monitor table columns for weft_monitor_task_collations`. The
  version, checkpoint, and rows were unchanged because the failure preceded
  migration.
- Migration boundary: every v5 test now uses the shared release-lineage
  fixture. A missing data-bearing base table fails before DDL; an absent
  deferred-write table is the only table creation allowed by migration
  preparation. Data migration, v6 verification, and the version-last write
  remain one transaction.
- Strategy-B promotion baseline: repository
  `f59a1fe808274e45a851439e116acf572c79e330`; pre-promotion [SB-0.4a] blob
  `6f52fd10766dd4ebb10c489c08883973b1dcfaf3`; promoted working-tree spec blob
  `286874df848f23821dff06c8fedb958412e14869`. The exact spec diff is the
  [SB-0.4a] semantic column/index/migration change in this task's diff.
- Semantic validation: required columns are checked by name, backend storage
  family/capacity, and bidirectional nullability; safe extra columns are
  limited to nullable, identity-backed, or proven non-NULL literal-defaulted
  fields. Required index validation uses the six query-backed shapes and, on
  PostgreSQL, requires valid/ready state. Extra unique indexes fail only when
  their key or equality semantics are stronger than the primary index.
- Verification: focused Monitor/store and Monitor-surface suites, plan/spec
  hygiene, import boundaries, Ruff, mypy (187 source files), documentation
  path checks, DOM-15 fixture checks, and the full suite all pass. The full
  result is `4243 passed, 13 skipped`; the skips are the existing live-provider
  and backend-specific environment gates. Backstitch retains the same 1,587
  repository findings and the touched scope is identical after normalizing
  line shifts. Live PostgreSQL remains unpassed because `WEFT_PG_TEST_DSN` is
  unset.

## Tasks

1. Capture a firing release-lineage regression before changing validation.
   - Replace the false migration setup that calls current `ensure_schema()` and
     edits `schema_version` to 5.
   - Create one reusable v5 fixture helper from the historical base DDL and the
     six actual additive operations. It must support the pre-deferred-table and
     present-deferred-table release states, seed requested data, and assert the
     evolved ordinal layout without calling current `ensure_schema()`.
   - Use that helper in every v5 migration test, including success, generic
     rollback, present-raw-row rejection, and raw-probe-error rollback. No test
     may create v6 and backdate only the version metadata.
   - Prove current `HEAD` fails at the ordered-column gate before migration.
     Preserve that exact failure plus unchanged version, checkpoint, and owned
     rows in the implementation record.

2. Complete the index dependency gate, then promote the semantic contract.
   - Capture query-to-index and SQLite/PostgreSQL plan evidence for the table
     above. Stop requiring unowned/redundant indexes, but keep all ten in
     schema-6 creation for rollback compatibility. Stop and amend this plan if
     any proposed retained index lacks evidence or needs a query/index redesign.
   - Update [SB-0.4a] with the exact delta above, add the plan backlink, and
     update implementation mapping notes. Do not duplicate schema acceptance
     wording in [MF-5].
   - Replace name-only ordered table specs with the smallest Monitor-local
     semantic column descriptors needed for required name, normalized storage,
     bidirectional required-nullability checks, and extra-column omittability
     checks. Add a registry completeness assertion so every required column
     appears in one semantic class. Keep code-owned projection tuples separate
     because their order remains meaningful.
   - Add backend catalog queries only for the semantic fields used by the
     validator. Use the exact `table_xinfo` and `information_schema` fields
     named above. Extend index introspection with PostgreSQL access method and
     predicate plus SQLite partial status. Do not compare raw DDL strings or
     build a general database-schema abstraction.

3. Repair startup ordering and remove non-semantic gates.
   - Use the semantic meta-table check before reading schema version.
   - For v5: run a read-only structural/data-input preflight first. Replace the
     broad create-all call with a migration-specific preparation method limited
     to absent deferred-table creation, retained-index creation, and obsolete
     index removal. Migrate owned data, run v6 semantic structure/data
     verification, then write version 6 last.
   - For v6: remain read-only during verification.
   - Remove physical column tuple equality and exact Monitor index-inventory
     equality. Keep required table, column semantics, primary key, retained
     index shape, stronger-unique-index, extra write-required column, and
     owned-data validation.
   - Update errors to name the table/column/property that is semantically
     incompatible; do not mention expected physical order.

4. Rebalance tests around behavior that matters.
   - Convert all v5 migration tests to the release-derived helper. In the main
     success case, assert version 6, all owned JSON rewrites, tombstone
     handling, checkpoint continuity, normal read/write, and idempotent second
     startup. In every failure case, assert version, checkpoint, schema, and
     owned rows are unchanged.
   - Delete `test_monitor_store_v6_rejects_reordered_columns_without_repair`.
     The historical migration test is the sole regression proof for order
     independence; do not add permutation variants.
   - Delete and replace
     `test_monitor_store_v6_rejects_extra_monitor_index_without_repair` to
     prove one benign extra non-unique index does not block startup and one
     stronger unique index does. Accept one semantic copy of the primary key
     and reject one same-column copy with different equality semantics. Do not
     add a permutation matrix.
   - Retain required-table/column, primary-key, required-index shape, and v6
     owned-data failures. Add one table-driven registry-completeness test for
     every required descriptor, a small pure normalization table covering only
     the documented aliases, and one real database rejection per semantic rule
     class: incompatible required type; an expected-nullable writer field made
     `NOT NULL`; an expected-required field made nullable; extra write-required
     column; wrong retained-index method/partial form where the backend supports
     it. Keep the documented SQLite `meta.key` exception. Do not mutate every
     column.
   - Cover extra defaults with one small parameterized literal test and one
     firing write probe: accept a proven non-NULL literal, reject `DEFAULT
     NULL`, and reject one unproved expression that evaluates to NULL.
   - Run the same shared tests on PostgreSQL when `WEFT_PG_TEST_DSN` is
     configured. A missing live PostgreSQL gate must be reported, not implied
     to have passed.

5. Document, review, and release the repair.
   - At Strategy-B promotion, record the last pre-promotion commit, the promoted
     spec blob/commit identifier, and the exact spec diff in the verification
     record before implementation evidence is declared current.
   - Add a changelog entry that names affected versions v0.9.95 through
     v0.9.97, explains that the failure preceded migration, and states that no
     checkpoint or task data rewrite occurred on those failed starts.
   - Add a durable lesson: migration fixtures must be built from a real prior
     release lineage, and every strict catalog assertion needs a named runtime
     dependency.
   - Run focused and full gates, independent work review, and traceability
     reconciliation. Record every review finding and disposition in this plan.

## Testing Plan

- Primary incident test: a real, non-empty, evolved v5 SQLite schema migrates
  to v6 despite `updated_at_ns` preceding the six appended columns. The same
  test runs through the shared PostgreSQL backend when available.
- Migration correctness: canonical message IDs, deferred schema upgrade,
  obsolete tombstone cleanup, obsolete index removal, checkpoint preservation,
  version-last behavior, rollback on a migration failure, and idempotent v6
  reopen remain covered.
- Structural failures: missing required table/column, incompatible normalized
  required-column semantics, rejected `NULL` for a nullable writer field,
  write-required extra column, stronger unique index, wrong primary key,
  missing retained index, and same-name wrong index shape still fail without
  current-version repair.
- Non-contract acceptance: release-evolved physical order, equivalent listed
  backend type aliases, a safely omittable extra column, and one benign extra
  non-unique index do not fail. There is no arbitrary permutation suite.
- Runtime smoke: after the migrated store opens, read the old collation and
  checkpoint, write/read a new event, and read the deferred record. This proves
  the catalog rule against actual consumers rather than inspection alone.
- Test design ceiling: descriptor membership is table-driven, but do not create
  one database mutation per column, raw type spelling, index alias, or possible
  order. Database tests are per semantic rule class. Retained named indexes
  remain enumerated contract elements under the repository firing-test rule.

## Verification and Gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py tests/tasks/test_task_monitor.py tests/commands/test_task_monitor.py -q
BROKER_TEST_BACKEND=postgres WEFT_PG_TEST_DSN="$WEFT_PG_TEST_DSN" ./.venv/bin/python -m pytest tests/core/test_monitor_sql.py tests/core/test_monitor_store.py -q
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/architecture/test_import_boundaries.py -q
git diff --check
../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json --output /tmp/weft-monitor-schema-backstitch-after.json
```

Before implementation, capture the same backstitch command to
`/tmp/weft-monitor-schema-backstitch-before.json`. Repository-wide historical
debt may keep the command nonzero; the acceptance gate is no new finding keyed
to [SB-0.4a], the plan, or touched Monitor files. If `../backstitch` or
`WEFT_PG_TEST_DSN` is unavailable, record that gate as unpassed.

## Rollout and Rollback

- Release this as a patch. Stop and restart the TaskMonitor after deployment;
  task execution itself does not need a queue migration.
- Before retrying migration on production v5, take the existing backend-level
  backup required by the v5-to-v6 one-way migration. The v0.9.95-v0.9.97
  order-gate failures occurred before mutation, so their stores should still be
  version 5 with the last checkpoint intact.
- On successful startup, verify schema version 6, Monitor availability, the
  preserved checkpoint, and checkpoint advancement beyond the failed
  deployment boundary.
- Before successful migration, rollback is a code/package rollback against the
  unchanged v5 store. After version 6 commits, rollback to a version that only
  understands v5 requires restoring the pre-migration backup. This fix does
  not create a new downgrade path.
- A fresh v6 store created by the patch still contains all ten legacy v6 index
  names, so v0.9.95-v0.9.97 do not reject it solely because of index inventory.
  The patch's validator requires only indexes with a current dependency.
- Stop rollout if a release-produced schema fails for any property not named in
  the decision table, if migration attempts order-only DDL, or if checkpoint
  advancement does not resume after a successful v6 verification.

## Independent Review Loop

An independent plan reviewer must verify the decision table against every
Monitor SQL read/write, both backend catalog APIs, the historical v5 DDL
sequence, and the governing specs. The review must challenge both directions:
no load-bearing property may be relaxed, and no property without a concrete
consumer may remain strict.

After implementation, a separate work review must inspect the completed diff
and firing evidence for release-faithful fixture construction, version-last
transactionality, backend parity, accidental `SELECT *`/positional SQL,
overfitted permutation tests, and spec/code/test traceability. Findings are
fixed or explicitly rejected with evidence before completion.

## Review Log

| Stage | Finding | Disposition |
|---|---|---|
| Author fresh-eyes | Exact physical column and exact secondary-index inventory checks have no matching runtime consumer, while normalized column type/nullability was not checked at all. | Replaced “strict equals exact DDL” with the dependency-based decision table; removed order/inventory gates and added semantic column validation. |
| Independent plan review, pass 1 | The first draft allowed broad DDL before v5 preflight, left compatible extras and column semantics underspecified, carried all ten indexes without query ownership, converted only the main false v5 fixture, omitted the PostgreSQL backend selector, and duplicated the schema rule in [MF-5]. | Accepted. Required a read-only v5 preflight and migration-only DDL, exact extra/type/null/index semantics, an index-to-query disposition gate, one historical helper for every v5 test, explicit PostgreSQL selection and all-marker gate, and a single normative home in [SB-0.4a]. |
| Independent plan review, pass 2 | Stopping creation of four legacy indexes would break rollback to strict v0.9.95-v0.9.97; nullability was directional only; `DEFAULT NULL` was incorrectly treated as omittable; Strategy-B and empty-deviation mechanics were incomplete. | Accepted. Schema 6 keeps creating all ten but requires only evidence-backed indexes, required nullability is now bidirectional with the SQLite meta-key exception, extra defaults must be non-NULL, and the plan now records promotion identifiers plus the empty deviation-table shape. |
| Independent plan review, pass 3 | The implementation task still said directional nullability, firing cases did not cover both nullability directions or `DEFAULT NULL`, and the empty deviation table contained a sentinel row. | Accepted. Made nullability implementation/tests explicitly bidirectional, added the two-case default probe, and left the deviation table header-only until a real deviation exists. |
| Independent work review, pass 1 | A generated or arbitrary-expression default can still yield NULL and break named inserts; a unique copy of the exact primary key adds no constraint; PostgreSQL unusable indexes can report the expected shape while `indisvalid` or `indisready` is false. | Accepted. Extra-column acceptance now requires nullable, identity, or a proven non-NULL literal default and fires an actual write; exact non-partial B-tree primary-key copies are accepted while stronger unique keys still fail; PostgreSQL required indexes must be valid and ready. |
| Independent work review, pass 2 | Matching unique-index column names do not prove matching equality semantics: SQLite `COLLATE NOCASE` rejects primary-key-distinct values, with an analogous PostgreSQL operator-class/collation gap. The reviewer also observed stale spec wording while the pass-1 edits were still in flight. | Accepted. Redundant unique-index acceptance now compares candidate key semantics with the actual primary index through SQLite `index_xinfo` and PostgreSQL `indclass`/`indcollation`; a firing SQLite `NOCASE` case rejects the stronger index. The spec and plan now state the implemented proven-literal, semantic-PK-copy, and PostgreSQL validity/readiness rules. |
| Independent work review, final | Rechecked the default/generated-column boundary, SQLite and PostgreSQL primary-index equality metadata, PostgreSQL validity/readiness, release-v5 fixture, transaction ordering, and spec/code/test traceability. | PASS. No remaining finding; live PostgreSQL remains the disclosed environment gap. |

## Out of Scope

- A schema version 7, table rebuild, data reordering, or downgrade migration.
- Repairing arbitrary operator-added `CHECK` constraints, triggers, or
  expression indexes. The validator detects but does not repair write-blocking
  custom columns and unique indexes whose key is stronger than the primary
  key.
- Changing Monitor collation, retention, deletion, checkpoint, external JSONL,
  or task lifecycle semantics.
- Moving Monitor tables into SimpleBroker or changing queue dump/load behavior.
- Exhaustive catalog fuzzing or arbitrary DDL permutation/property testing.
- Refactoring unrelated Monitor or SQL code.

## Fresh-Eyes Review

The tempting small fix is to sort both column lists. That would restore this
database, but it would leave the policy error intact: catalog equality would
still be treated as correctness, the false v5 fixtures would remain, extra
non-unique indexes would still abort startup, and meaningful column semantics
would still be unchecked. The first plan draft also relaxed extra objects too
broadly and kept indexes on reputation rather than evidence. This revision
ties every retained rejection to read/write or query-plan behavior, requires a
read-only preflight before DDL, and gives order independence one shared
release-lineage fixture rather than a combinatorial test surface.
