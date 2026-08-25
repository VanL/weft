# LLM 0.33 Compatibility Upgrade Plan

Status: completed
Source specs: docs/specifications/13-Agent_Runtime.md [AR-3.1], [AR-4], [AR-5], [AR-6], [AR-7]; docs/specifications/07-System_Invariants.md [EXEC.5]-[EXEC.10]
Superseded by: none

Class: 5 — spec-editing and risky. The existing dependency crosses the built-in
agent execution boundary, and the required plan backlink edits the normative
agent-runtime spec. The intended runtime behavior does not change. Hardening is
mandatory because one-shot and persistent agent execution sit on the durable
spine.

## Goal

Upgrade Weft from LLM 0.32 to the published LLM 0.33 release, remove the
temporary direct `httpx` workaround that 0.33 supersedes, and preserve every
current Weft `llm` backend contract. Make adapter or fixture changes only when
a firing test against the released package proves they are necessary.

## Source Documents

- `docs/specifications/13-Agent_Runtime.md` [AR-3.1], [AR-4], [AR-5], [AR-6],
  and [AR-7] own structured-input normalization, public outputs, the adapter
  interface, persistent sessions, and the built-in `llm` behavior.
- `docs/specifications/07-System_Invariants.md` [EXEC.5]-[EXEC.10] own ordered
  result delivery, terminal drain, failure conversion, cleanup, and one-result
  selection around one-shot and persistent agent execution.
- The upstream [LLM 0.33 release](https://github.com/simonw/llm/releases/tag/0.33)
  and [official changelog](https://llm.datasette.io/en/stable/changelog.html)
  identify the OpenAI Python 3.x and `httpx2` migration.
- The upstream
  [0.33 package metadata](https://raw.githubusercontent.com/simonw/llm/0.33/pyproject.toml)
  declares Python 3.10+, `openai>3`, and `httpx2`.
- The upstream
  [0.32 to 0.33 comparison](https://github.com/simonw/llm/compare/0.32...0.33)
  is the code-level compatibility baseline for APIs used by Weft.
- `docs/plans/2026-08-13-simplebroker-7-3-dump-watermark-plan.md` records why
  direct `httpx>=0.28` was added for LLM 0.32. It is historical evidence, not
  a current behavior source.

## Context and Key Files

Files to modify:

- `pyproject.toml`, `uv.lock`: raise the LLM floor to 0.33, remove the direct
  `httpx` workaround, and resolve only the dependency changes forced by that
  pair of manifest edits.
- `tests/system/test_optional_extras.py`: replace the obsolete direct-`httpx`
  assertion with firing tests for the exact LLM floor and absence of a direct
  `httpx` requirement.
- `CHANGELOG.md`: describe the 0.33 upgrade, OpenAI/HTTP-client transition, and
  removal of the workaround without claiming new Weft behavior.
- `docs/specifications/13-Agent_Runtime.md`: add only the required backlink to
  this plan under `## Related Plans`.
- This plan and `docs/plans/README.md`: plan status, review, deviations, and
  verification evidence.

Files to modify only if released-package tests prove incompatibility:

- `weft/core/agents/backends/llm.py`: the one-shot and persistent adapter.
- `tests/fixtures/llm_test_models.py`: the real plugin-registered deterministic
  `llm.Model` used by integration tests.
- `tests/core/test_llm_backend.py`: direct adapter contract tests.
- `tests/tasks/test_agent_execution.py`, `tests/tasks/test_runner.py`: durable
  task and session-path regressions.

Read first:

- `weft/core/agents/backends/llm.py`: `LLMBackend` currently resolves the
  model, opens a conversation, calls `conversation.chain(...)`, and converts
  the final `Response` through `.text()`, `.json()`, and `.usage()`.
- `tests/fixtures/llm_test_models.py`: the test model subclasses the real
  upstream `Model`, accepts `Prompt`, records tool calls and usage through the
  upstream response API, and registers through the real pluggy hook.
- `tests/core/test_llm_backend.py`: fires text, JSON, message, schema, tool,
  options, instructions, template, structured-input, and max-turn contracts.
- `tests/tasks/test_agent_execution.py` and `tests/tasks/test_runner.py`: prove
  that the adapter stays on the existing one-shot and persistent task paths.
- `tests/system/test_optional_extras.py`: owns exact dependency-floor and root
  dependency assertions.

Current structure and released-package evidence:

- Weft imports neither `openai` nor `httpx`; `uv tree --invert` shows the
  current `httpx` installation is root-owned by Weft only.
- LLM 0.33 changes its built-in OpenAI transport to `openai>3` plus `httpx2`.
  The `Model`, `Prompt`, `Response`, `Tool`, `Conversation.chain`, and pluggy
  surfaces Weft uses remain available.
- An isolated Python 3.14 environment with released `llm==0.33`, the current
  Weft source, and no source edits passed all 12 tests in
  `tests/core/test_llm_backend.py`. This is compatibility evidence, not the
  post-change completion gate.
- The repository does not declare third-party LLM provider plugins. Operators
  may load arbitrary plugin modules through `runtime_config.plugin_modules`;
  plugins that pin OpenAI 2.x, construct an `httpx` client, or mock `httpx`
  need their own deployment-specific compatibility test.

Comprehension checks before editing:

1. Why is adopting LLM 0.33's native `messages=` input out of scope? [AR-3.1]
   currently requires deterministic flattening inside the adapter; a package
   update must not silently revise that public behavior.
2. Which tests prove that the dependency still works through Weft rather than
   only in an upstream unit seam? The real plugin-backed backend tests plus the
   task runner and persistent agent-session paths.

## Invariants and Constraints

- Preserve the current durable spine, queue names, TID identity, forward-only
  states, reserved policy, TaskSpec immutability, terminal handoff, and session
  cleanup behavior.
- Preserve [AR-7] exactly: model resolution, system instructions, named
  templates, Python tools, `options`, `max_turns`, `per_message`, `per_task`,
  and `text`/`json`/`messages` output modes must not change.
- Preserve [AR-3.1] adapter-local structured-message flattening. Do not adopt
  new upstream structured-message APIs in this compatibility slice.
- Keep the real upstream plugin and model API in tests. Do not mock
  `llm.get_model`, conversation chaining, tools, or response extraction.
- Do not add a second agent execution path, new logging behavior, embedding
  support, attachment behavior, reasoning output, or server-side tools.
- Remove direct `httpx` only after the dependency graph and source search prove
  it has no other Weft owner. `httpx2` remains upstream-owned by LLM/OpenAI;
  Weft must not add it directly.
- Expected lock movement is limited to removing `httpx` and updating LLM plus
  packages whose existing versions do not satisfy LLM 0.33. Inspect and reject
  unrelated opportunistic upgrades.
- Use only documented public `llm` package surfaces. Stop and re-plan if 0.33
  compatibility appears to require private imports or changes to Weft's public
  result contract.
- No persistence or queue format changes are intended. There is no one-way
  data migration.
- Preserve unrelated worktree changes. The existing local SimpleBroker 7.4.1
  commit is the baseline and must not be rewritten.

## Spec Baseline

- `7c29d696ee565d2940a98fc76fd3059ca9f34833` —
  `docs/specifications/13-Agent_Runtime.md` and
  `docs/specifications/07-System_Invariants.md` at plan authoring time.
- Upstream release baseline: tag `0.33` at
  `a463c6318f65a48ae185733a0655dca7bb00c3e1`; PyPI reported 0.33 as latest
  on 2026-08-24.
- Plan type: implementation with a traceability-only spec revision.
- Promotion strategy: B — atomically land the Related Plans backlink with the
  dependency, lock, test, and changelog changes. The spec's behavior text does
  not change, so no interim mixed contract is useful.
- Promotion baseline: authoring baseline
  `7c29d696ee565d2940a98fc76fd3059ca9f34833` plus the reviewed
  `docs/specifications/13-Agent_Runtime.md` diff that adds only this plan's
  Related Plans backlink. `git diff 7c29d696 --
  docs/specifications/13-Agent_Runtime.md` identifies the promoted worktree
  contract while the user reviews the uncommitted slice.

## Proposed Spec Delta

### `docs/specifications/13-Agent_Runtime.md` — `## Related Plans`

Add this row to the existing list:

> - [`docs/plans/2026-08-24-llm-0-33-compatibility-plan.md`](../plans/2026-08-24-llm-0-33-compatibility-plan.md)

No requirement, implementation mapping, or current backend behavior changes.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Tasks

1. Capture current and red dependency evidence.
   - Run the current 0.32 backend and packaging tests as the local baseline.
   - Replace the obsolete direct-`httpx` test with exact tests requiring
     `llm>=0.33` and rejecting direct `httpx`; run them before the manifest
     edit and record the expected failures.
   - Retain the completed isolated 0.33 backend run as compatibility
     feasibility evidence. Rule 5 is not applicable to the adapter if no
     adapter behavior changes; inventing a failing test for an already
     compatible seam would be false evidence. The manifest assertions provide
     the real red-green evidence for the dependency change.
   - Stop if a clean released 0.33 environment imports `httpx` or any Weft
     source path owns it independently.

2. Atomically promote traceability and update the dependency set.
   - Add the proposed Related Plans backlink.
   - Set the root floor to `llm>=0.33`; remove `httpx>=0.28`.
   - Refresh the lock from the changed manifest. Accept LLM 0.33,
     `openai>3`, and required `httpx2`/transport movement; reject unrelated
     package upgrades.
   - Update the changelog to replace the obsolete 0.32 workaround note with
     the 0.33 migration facts.
   - Record the promotion baseline as the spec diff from the authoring SHA
     while work remains uncommitted.

3. Prove the existing adapter or make only necessary compatibility fixes.
   - Run the real plugin-backed backend tests first, then the task execution
     and persistent session tests.
   - If they pass, leave `weft/core/agents/backends/llm.py` and the fixture
     untouched.
   - If one fails because a documented 0.33 public API changed, add the
     nearest valid and invalid regression cases before the smallest adapter or
     fixture fix. Preserve the Weft-owned output and session contracts.
   - Stop and re-plan if the fix would adopt new LLM features, change public
     output, or require an upstream private API.

4. Verify, review, and reconcile traceability.
   - Run targeted packaging, backend, task, and session tests, then the full
     default suite, mypy, ruff, plan/spec/import gates, lock check, and diff
     check.
   - Verify the installed versions and dependency graph: LLM is 0.33, OpenAI
     is greater than 3.0.0, `httpx2` is upstream-owned, and `httpx` is absent.
   - Run independent completed-work review against this plan, [AR-7], the
     released-package diff, and the exact repository diff. Disposition every
     finding before completion.
   - Capture the final Backstitch report with the same roots as the pre-edit
     report. Require identical issue tuples for the touched agent-runtime spec
     and this plan; unrelated repository debt may retain aggregate exit 1.
   - Record external-plugin compatibility as an explicit deployment caveat;
     do not claim uninstalled third-party plugins were tested.

## Testing Plan

- Packaging: `tests/system/test_optional_extras.py` fires the exact LLM floor
  and proves `httpx` is not a direct root dependency.
- Adapter: `tests/core/test_llm_backend.py` uses the real upstream plugin,
  model, conversation, tool, prompt, response, JSON, usage, and hook surfaces.
- Task integration: `tests/tasks/test_agent_execution.py` and the LLM/session
  cases in `tests/tasks/test_runner.py` prove one-shot and persistent work stay
  on the existing task and terminal handoff paths.
- Import boundary: `tests/architecture/test_import_boundaries.py` proves the
  LLM backend remains lazy outside the explicit runtime-registration seam.
- Full suite: catches dependency-level import or transitive version effects
  outside the narrow agent tests.
- Do not make a live provider API call in the deterministic gate. Provider
  credentials, billing, network behavior, and uninstalled external plugins are
  outside the repository's reproducible compatibility proof.

## Verification and Gates

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python -m pytest tests/system/test_optional_extras.py -q
./.venv/bin/python -m pytest tests/core/test_llm_backend.py -q
./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_runner.py -k "llm or agent_session" -q
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
bin/check-dom15-fixtures
bin/check-doc-paths
uv lock --check
uv tree --frozen --invert --package llm
uv tree --frozen --invert --package httpx2
git diff --check
./.venv/bin/python -c "from importlib.metadata import version; from importlib.util import find_spec; from packaging.version import Version; assert version('llm') == '0.33'; assert Version(version('openai')) > Version('3'); version('httpx2'); assert find_spec('httpx') is None"
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-llm-033-backstitch-after.json || test $? -eq 1
jq --exit-status --slurp \
  'def scoped: [.issues[] | select(.path == "docs/specifications/13-Agent_Runtime.md" or .path == "docs/plans/2026-08-24-llm-0-33-compatibility-plan.md") | [.code, .severity, .path, .section_id, .symbol, .message]] | sort; (.[0] | scoped) == (.[1] | scoped)' \
  /tmp/weft-llm-033-backstitch-before.json \
  /tmp/weft-llm-033-backstitch-after.json
```

Per-task gates run in dependency order. The full suite and static gates run
after the lock and any compatibility patch settle.

## Rollout and Rollback

- Roll out the Weft package metadata, lock, and any proven compatibility patch
  together. Observe one deterministic or deployment-selected one-shot LLM task
  and, where used, one persistent task-lifetime conversation.
- Repository rollback restores the prior lock, 0.32 lower bound, direct
  `httpx` workaround, packaging tests, changelog, and spec backlink as one
  change. The prior lock is what actually selects 0.32; restoring only
  `llm>=0.32` does not, because that range also admits 0.33.
- An unlocked deployment that must force the old runtime needs its previously
  validated constraints/lock artifact or a temporary exact 0.32 / `<0.33`
  constraint while the old Weft package is restored. Do not publish that upper
  bound as a new long-term compatibility policy; it is rollback containment.
  No queue, TaskSpec, transcript, or persistence migration constrains rollback.
- Deployment-specific LLM plugins are the main residual risk. Before rollout,
  operators using them should verify their installed set against OpenAI 3.x and
  `httpx2`; a plugin that pins OpenAI 2.x may make the environment
  unresolvable, while a plugin that patches `httpx` may stop intercepting live
  traffic.

## Independent Review Loop

An independent reviewer must inspect this plan, its exact Related Plans delta,
the 0.33 upstream release/diff, [AR-7], the adapter, fixture, and packaging
test before implementation. The same-family reviewer limitation is acceptable
when no different model family is available, but it must be recorded. A second
review inspects the completed diff for public-contract drift, dependency-graph
mistakes, obsolete direct dependencies, missing red evidence, and unsupported
claims about external plugins.

## Review Log

| Stage | Finding | Disposition |
|-------|---------|-------------|
| Author fresh-eyes | The first draft omitted environment synchronization, and its multiline `python -c` handshake would not execute as written. No scope, invariant, or architecture ambiguity remained after checking the task order against the hardening checklist. | Accepted. Added `uv sync --all-extras`, replaced the handshake with an executable single-line package/import assertion, and retained the narrow no-adapter-change default. |
| Independent plan review, round 1 | BLOCKED: the old lower bound would not force 0.32 in an unlocked rollback; the Class 5 Backstitch gate was missing; the already-green adapter probe was mislabeled as a Rule 5 substitute. | Accepted all three. Separated lock-based and unlocked rollback, added before/after keyed Backstitch comparison, and reclassified the isolated run as feasibility evidence while keeping manifest assertions as the red-green proof. |
| Independent plan review, round 2 | PASS: verified the lock-aware rollback, executable keyed Backstitch comparison and baseline, and correct separation of feasibility evidence from manifest red-green proof. No new defect was introduced. | Accepted. Implementation may proceed within the reviewed scope. |
| Independent work review, round 1 | No blocker. F1: plan corpus count was 180 but the filesystem and index both had 181. F2: the transport-dependency test recognized only `>=` entries, not every PEP 508 spelling. F3: Fresh-Eyes text still said plan review remained pending. | Accepted all three. Corrected the count, normalized the leading PEP 508 distribution name before the absence assertion, and made the review history current. |
| Independent work review, round 2 | PASS: the plan count, index, and filesystem all report 181; eight alternate PEP 508 dependency forms normalize correctly; the Fresh-Eyes and review history are current. Packaging, plan-metadata, ruff, and diff checks passed. No defect was introduced. | Accepted. The completed-work review loop is closed. |

## Verification Record

- Pre-plan isolated probe: released `llm==0.33` plus current Weft source on
  Python 3.14 passed all 12 tests in `tests/core/test_llm_backend.py`.
- Pre-edit Backstitch report:
  `/tmp/weft-llm-033-backstitch-before.json`; expected repository-debt exit 1,
  27 errors, 988 warnings, and 572 infos. The new plan has zero issues. The
  touched agent-runtime spec has 16 pre-existing `SPEC_SECTION_UNMAPPED` infos
  and no error or warning. Completion requires the exact scoped issue tuple set
  to remain unchanged.
- Current 0.32 baseline: the direct backend plus existing packaging tests
  passed (21 tests); installed versions were LLM 0.32, OpenAI 3.0.0, `httpx`
  0.28.1, and `httpx2` 2.10.0.
- Red packaging gate: after updating only the tests, the exact LLM floor and
  transitive-`httpx` assertions failed against `llm>=0.32` plus direct
  `httpx>=0.28`; the transitive-`httpx2` assertion passed.
- Installed handshake after `uv sync --all-extras`: LLM 0.33, OpenAI 3.3.1,
  and `httpx2` 2.10.0; `httpx` is absent.
- Targeted post-change tests: packaging plus backend 23 passed; agent execution
  51 passed; selected LLM/session runner tests 56 passed; import boundaries 54
  passed.
- Final post-review default suite: 4,217 passed, 2 expected
  PostgreSQL-gated skips in 216.35 seconds.
- Full mypy: 187 source files passed. Ruff reported all checks passed.
- Plan metadata/spec hygiene: 8 passed. DOM-15 fixtures and document-path
  checks passed. Lock check, dependency-tree checks, package handshake, and
  `git diff --check` passed.
- Final Backstitch report:
  `/tmp/weft-llm-033-backstitch-after.json`; the aggregate baseline is
  unchanged at 27 errors, 988 warnings, and 572 infos. The scoped before/after
  tuple comparison returned `true`: zero plan issues and the same 16
  pre-existing agent-runtime spec infos.
- Independent completed-work review passed in round 2 after correcting the
  plan corpus count, hardening the dependency-name guard for alternate PEP 508
  forms, and reconciling the review history. The reviewer found no introduced
  defects.
- No adapter, fixture, TaskSpec, task-runner, session-protocol, or public-output
  change was required. The Deviation Log remains empty.

## Out of Scope

- Adopting native upstream structured messages, streaming events, reasoning,
  attachments, embeddings, server-side tools, pausable chains, or new logging.
- Changing the default model or supporting removed upstream model aliases.
- Adding or upgrading third-party LLM provider plugins.
- Making live paid provider calls part of the default test suite.
- Refactoring the agent adapter, task runner, or session protocol without a
  released-package failure that requires it.

## Fresh-Eyes Review

The separate author pass found two verification defects: the plan did not
explicitly synchronize the changed lock into the repository environment, and
the multiline package handshake was not valid as a pasted shell command. Both
are corrected above. The plan otherwise names the exact current API seam,
keeps new upstream features out of scope, provides red packaging evidence plus
a released-package compatibility substitute, and stops if private APIs or
public-contract changes appear. Independent plan review subsequently completed
in two rounds; the Review Log records the blocking findings and accepted fixes.
