# Ruff Stable-Default Lint Expansion Plan

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-3], [TS-3.1]
Superseded by: none

Class: 5+P. The change materially revises the repository-wide verification
contract and how future Python work is implemented and reviewed. The cleanup
will touch runtime, test, integration, extension, and tool surfaces.

Plan type: implementation with spec revision.

Hardening: required. Although product behavior is intended to stay unchanged,
expanded lint findings occur in the durable execution spine, exception
containment, resource cleanup, subprocess handling, public typing surfaces,
and extension adapters. A lint-driven edit can cross those boundaries while
still looking mechanical.

## 1. Goal

Make Weft use the same effective Ruff rule set as SimpleBroker: Ruff 0.16.1's
stable defaults extended with the existing `E`, `W`, `F`, `I`, `B`, `C901`,
`C4`, and `UP` families. Resolve the resulting findings without changing
runtime behavior, weakening error containment, or fragmenting cohesive code.
The expanded policy must be an exact, firing repository gate rather than a
configuration accident.

## 2. Requested Outcomes

- [ ] Replace `lint.select` with `lint.extend-select` while retaining exactly
  `E`, `W`, `F`, `I`, `B`, `C901`, `C4`, and `UP`.
- [ ] Match SimpleBroker's reviewed effective inventory exactly: 453 enabled
  Ruff 0.16.1 rules, 282 more than Weft's current 171 and none removed.
- [ ] Preserve the McCabe threshold, C901 registry, global ignores, preview
  posture, exact extensionless-Python discovery, CI ordering, and explicit
  formatter ownership.
- [ ] Resolve every expanded-policy diagnostic through a reviewed,
  behavior-preserving edit where practical.
- [ ] Make zero new suppressions the target. Any irreducible finding must use
  [TS-3.1], name the protected invariant and real proof, receive independent
  review and explicit owner approval, and activate atomically with its source
  directive and generated index.
- [ ] Prove with the real repo-managed Ruff binary that one stable-default
  rule outside the prior set and one retained legacy-family rule both fire.
- [ ] Update active contributor guidance to use `ruff check .` without
  rewriting historical plans or lessons.
- [ ] Pass the full test, type, lint, suppression, format, documentation, and
  traceability gates before completion.

## 3. Source Documents

- `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1] is the
  governing static-analysis and suppression contract.
- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md` is
  completed and owns the current C901 activation, discovery policy, exact
  enabled-rule fixture, and suppression tool.
- `../simplebroker/docs/plans/2026-07-29-ruff-lint-expansion-plan.md` is the
  completed reference implementation. Reuse its policy shape and lessons, but
  not its old diagnostic counts, file list, suppressions, or Python target.
- `../simplebroker/pyproject.toml` and
  `../simplebroker/tests/fixtures/ruff-enabled-rules.txt` provide the current
  reference configuration and reviewed 453-code inventory.
- `AGENTS.md`, `docs/agent-context/engineering-principles.md`,
  `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` govern
  implementation and review.
- Ruff references used by the SimpleBroker plan:
  <https://github.com/astral-sh/ruff/releases/tag/0.16.0>,
  <https://astral.sh/blog/ruff-v0.16.0>, and
  <https://docs.astral.sh/ruff/linter/#rule-selection>.

## 4. Spec Baseline

- `d0254ce45a757a7351fa0e7ca76f389790a1800b` is the authoring baseline for
  [TS-3], [TS-3.1], Ruff 0.16.1, the current 171-code fixture, the
  118-group/147-directive C901 registry, and the `C901=147`, `E402=22`,
  `F401=5` raw-`noqa` inventory.
- Record the atomic promotion baseline after Task 8. Until then, this baseline
  remains the governing contract.

## 5. Current Structure And Key Files

### Read first

- `pyproject.toml`: explicitly selects eight families, shadowing Ruff 0.16.1's
  expanded stable defaults.
- `.github/workflows/test.yml`: already runs `ruff check .`, suppression check,
  explicit formatter roots, and full mypy roots in the required order.
- `tests/specs/test_ruff_policy.py`: real-Ruff proof for effective rules,
  tracked-file discovery, C901, CI shape, cleanliness, and suppression checks.
- `tests/fixtures/ruff-enabled-rules.txt`: exact accepted effective codes;
  currently 171.
- `bin/ruff_suppression_index.py`: the only reconciliation path for normal
  Ruff, raw `--ignore-noqa`, source directives, [TS-3.1] rows, and the generated
  index. Do not create a second suppression path.
- `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1].
- `README.md`, `AGENTS.md`, and
  `docs/agent-context/runbooks/testing-patterns.md`: active contributor
  commands that still show narrower lint paths.

### Expected policy/documentation changes

- `pyproject.toml`
- `tests/fixtures/ruff-enabled-rules.txt`
- `tests/specs/test_ruff_policy.py`
- `docs/specifications/08-Testing_Strategy.md`
- `README.md`
- `AGENTS.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- this plan and `docs/plans/README.md`
- `.github/workflows/test.yml` only if a firing test shows drift; its current
  command order should remain unchanged
- `bin/ruff_suppression_index.py` and its tests only if a real non-C901
  candidate proves the current general-rule support insufficient
- `docs/lessons.md` only if implementation exposes a durable new failure mode

### Cleanup scope

Every path in the locked expanded-policy JSON is authoritative. Highest-count
current paths include `weft/core/tasks/sessions.py`,
`weft/core/runners/host.py`, `weft/core/taskspec/model.py`,
`weft/_constants.py`, `weft/core/manager.py`,
`weft/core/manager_runtime.py`, `weft/core/monitor/task_monitor.py`,
`weft/core/runners/subprocess_runner.py`, the Docker and Microsandbox adapter
modules, `tests/test_harness_registration.py`, `tests/conftest.py`,
`tests/helpers/weft_harness.py`, `tests/tasks/test_runner.py`, and
`tests/tasks/test_multiqueue_watcher.py`.

Do not turn this list into an allowlist. Regenerate exact diagnostics at
implementation start and after every accepted cleanup slice.

```text
pyproject.toml selection + discovery
          |
          v
ruff check .  --->  normal clean gate
          |
          +-------> raw --ignore-noqa inventory
                         |
                         v
              ruff_suppression_index.py
                         |
        [TS-3.1] rows <-> source directives <-> generated index
```

The expansion changes only the first box's effective selection. It reuses the
remaining discovery, suppression, CI, and exact-fixture paths.

### Comprehension gate

Before editing, the implementer must answer:

1. Why does `select` -> `extend-select` add 282 rules without removing any
   current rule, and which exact fixture proves the union?
2. Why must broad process, plugin, retry, and cleanup catches retain failure
   containment even when `BLE001` or `S110` fires?
3. Which path makes a local suppression durable, and why are global/per-file
   ignores, blanket directives, and a second allowlist prohibited?
4. Why must Weft retain six exact extensionless Python paths instead of
   SimpleBroker's broad `bin/*`, which would parse Weft's Bash tools?

## 6. Measured Baseline

Corrected on 2026-08-06 at the authoring baseline with repo Ruff 0.16.1 and a
temporary `pyproject.toml` identical to the real project configuration except
for the single intended `select` -> `extend-select` mutation. This preserves
`isort.known-first-party`, McCabe, target, ignores, discovery, and every other
non-selection setting while simulating activation:

- Expanded inventory: 453 codes; SimpleBroker inventory: 453 codes; set
  difference empty in both directions.
- Current Weft inventory: 171; added: 282; removed: zero.
- Normal diagnostics: 599 across 146 files and 38 rule codes.
- Ruff-offered fixes: 228. Fix availability is not permission to apply it.
- Raw diagnostics: 773 across 156 files and 41 codes. The difference is the
  current `C901=147`, `E402=22`, `F401=5` local inventory.

| Rule | Count | Primary review risk |
|---|---:|---|
| `BLE001` | 186 | Exception containment and process/plugin boundaries |
| `TRY004` | 75 | Public exception type and caller compatibility |
| `S110` | 46 | Intentional silent cleanup/retry behavior |
| `RUF100` | 38 | Stale existing `noqa` codes; registry integrity |
| `SIM102` | 25 | Branch precedence and logical locality |
| `RUF022` | 25 | Export membership/order contracts |
| `FLY002` | 24 | String construction and output fidelity |
| `SIM117` | 21 | Context-manager enter/exit and cleanup ordering |
| `RUF059` | 19 | Test readability and unused values |
| `PIE807` | 17 | Redundant lambdas; callable behavior |
| `RUF012` | 15 | Mutable class state and test-fixture intent |
| `PYI064` | 15 | Protocol/signature typing contracts |
| `PYI063` | 11 | Positional-only signature compatibility |
| `ISC004` | 10 | String concatenation and exact output |
| `PLW1510` | 10 | Explicit subprocess failure policy |
| `PLR0402` | 9 | Import shape and monkeypatch behavior |
| `PLR1711` | 7 | Explicit return semantics |
| `RET501` | 7 | Explicit return semantics |
| `SIM103` | 5 | Boolean branch simplification |
| `FURB188` | 4 | Path method choice and error behavior |
| `SIM114` | 4 | Branch combination and message fidelity |
| `PIE810` | 3 | Starts/ends-with tuple semantics |
| `RUF046` | 3 | Numeric cast/precedence behavior |
| `S102` | 3 | Intentional `exec` in architecture tests |
| `PIE790` | 2 | Redundant pass cleanup |
| `S112` | 2 | Intentional continue-on-error behavior |
| `SIM905` | 2 | Split construction behavior |
| `FURB136` | 1 | Redundant string operation |
| `FURB167` | 1 | Regex flag placement |
| `PLC0208` | 1 | Set iteration in a test |
| `PYI025` | 1 | Collection annotation contract |
| `PYI034` | 1 | Context-manager public typing contract |
| `RUF009` | 1 | Dataclass default construction |
| `RUF019` | 1 | Unnecessary key check |
| `RUF034` | 1 | Suspicious branch equivalence |
| `SIM115` | 1 | File/resource lifetime |
| `SIM118` | 1 | Mapping membership expression |
| `TRY203` | 1 | Immediate re-raise and traceback semantics |

The implementation must rerun the exact probe; it must not copy these counts
if intervening accepted work changed the baseline.

### Exact reproduction probe

Run from the repository root after loading `.envrc`. This changes only the
selection key in a temporary project config. Do not use `--isolated` for the
diagnostic baseline: it also discards `isort.known-first-party` and creates 75
false `I001` findings that the real activation does not produce.

```bash
./.venv/bin/python - <<'PY'
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

root = Path.cwd()
source = (root / "pyproject.toml").read_text(encoding="utf-8")
marker = "[tool.ruff.lint]\nselect = ["
if source.count(marker) != 1:
    raise SystemExit("expected exactly one Ruff lint.select table entry")
expanded = source.replace(
    marker,
    "[tool.ruff.lint]\nextend-select = [",
    1,
)

with tempfile.TemporaryDirectory() as directory:
    config = Path(directory) / "pyproject.toml"
    config.write_text(expanded, encoding="utf-8")
    command = [
        str(root / ".venv/bin/ruff"),
        "check",
        "--config",
        str(config),
        "--output-format",
        "json",
        ".",
    ]
    for label, extra in (("normal", []), ("raw", ["--ignore-noqa"])):
        result = subprocess.run(
            [*command, *extra], cwd=root, text=True, capture_output=True
        )
        if result.returncode not in {0, 1}:
            raise SystemExit(
                f"{label}: Ruff exit {result.returncode}: {result.stderr}"
            )
        diagnostics = json.loads(result.stdout)
        rules = Counter(item["code"] for item in diagnostics)
        files = {
            Path(item["filename"]).resolve().relative_to(root).as_posix()
            for item in diagnostics
        }
        offered_fixes = sum(item["fix"] is not None for item in diagnostics)
        print(
            label,
            len(diagnostics),
            len(files),
            offered_fixes,
            sorted(rules.items()),
        )

    settings = subprocess.run(
        [
            str(root / ".venv/bin/ruff"),
            "check",
            "--config",
            str(config),
            "--show-settings",
            "weft/__init__.py",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    match = re.search(
        r"linter\.rules\.enabled = \[\n(?P<rules>.*?)\n\]",
        settings.stdout,
        re.DOTALL,
    )
    if match is None:
        raise SystemExit("Ruff enabled-rule settings were not found")
    enabled = set(re.findall(r"\(([A-Z]+\d+)\)", match.group("rules")))

simplebroker = set(
    (root.parent / "simplebroker/tests/fixtures/ruff-enabled-rules.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)
current = set(
    (root / "tests/fixtures/ruff-enabled-rules.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)
print("effective", len(enabled))
print("current", len(current))
print("added", len(enabled - current))
print("removed", sorted(current - enabled))
print("only in Weft", sorted(enabled - simplebroker))
print("only in SimpleBroker", sorted(simplebroker - enabled))
if current - enabled:
    raise SystemExit("expanded policy removed existing Weft rules")
PY
```

The tracked-file discovery policy remains independently proven by
`tests/specs/test_ruff_policy.py`. Counts alone are insufficient.

## 7. Invariants And Constraints

### Product and architecture

- No public CLI, client, TaskSpec, queue, result, status, or persisted payload
  contract changes.
- TID format/immutability, forward-only state transitions, reserved-queue
  policy, resolved `spec`/`io` immutability, spawn context, and runtime-only
  `weft.state.*` behavior remain unchanged.
- The `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine
  remains the only execution path.
- A broad catch at a real process, plugin, interpreter, retry, cleanup, or
  best-effort boundary must not be narrowed merely to satisfy a rule.
- Error types/messages are observable where callers depend on them. `TRY004`
  is a prompt to inspect the boundary, not authority to replace an exception.
- Context-manager rewrites preserve acquisition, partial-enter cleanup, exit
  order, suppression, and traceback behavior.
- Subprocess calls preserve fatal/nonfatal policy. `check=False` may make an
  existing policy explicit; `check=True` requires proof nonzero is already
  fatal.
- Public typing and exported membership remain consumer-compatible.

### Policy

- Ruff stays at the current locked version; a version refresh is separate.
- Target `py312`, line length 88, McCabe 10, and ignores `E501`/`B008` remain.
- Preview stays disabled; per-file ignores stay empty.
- Six explicit extensionless Python tools remain exact; Bash stays excluded.
- CI remains normal Ruff -> suppression -> explicit formatter -> full mypy.
- C901 group IDs/cardinalities/raw findings/index remain coherent.
- No global rule ignore, threshold raise, blanket directive, baseline
  allowlist, or second suppression system.

### Scope and design

- No new dependency.
- Never run repository-wide `ruff check --fix .` or `--unsafe-fixes`. Apply one
  reviewed rule batch at a time and format only changed Python paths.
- Do not refactor cohesive code merely to satisfy `SIM`, `TRY`, or another
  style rule. A narrow approved suppression is better than behavior drift or
  reduced logical locality.
- Do not add generic exception, validation, context-manager, subprocess, or
  cleanup frameworks.
- Historical plans and dated lessons remain historical evidence; do not edit
  them to show current commands.

## 8. Proposed Spec Delta

Promotion strategy: **B, atomic**. Preparatory cleanup may land under the
current policy. Promote this [TS-3] text only with `lint.extend-select`, the
453-code fixture, firing tests, contributor docs, and approved [TS-3.1]
additions. This avoids an active spec claiming a gate that is not yet enabled.

### [TS-3] replace the opening paragraph

Replace the current paragraph beginning `Weft's Python lint gate uses` with:

> Weft's Python lint gate uses the stable default rule set of the Ruff version
> locked in `uv.lock`, extended with the repository's reviewed `E`, `W`, `F`,
> `I`, `B`, `C901`, `C4`, and `UP` rule families. The configuration extends
> Ruff's defaults rather than replacing them. The gate covers every tracked
> first-party `.py`/`.pyi` file and Python-shebang repository tool. Ruff owns
> Python file discovery; configuration includes tracked extensionless Python
> tools explicitly and must not parse Bash tools as Python.

### [TS-3] replace the owner/verification paragraph

Replace the paragraph beginning `Owner: pyproject.toml owns rule selection`
with:

> Owner: `pyproject.toml` owns rule selection and discovery; the root CI lint
> job enforces it. Boundary: `weft/`, `tests/`, `integrations/`, `extensions/`,
> and Python tools under `bin/`. Verification:
> `tests/specs/test_ruff_policy.py` invokes the real repo-managed Ruff binary,
> compares effective discovery and the complete enabled-rule set with reviewed
> inventories, proves every behavior-affecting policy setting fires, and proves
> that a stable-default rule outside the retained families and a retained-family
> rule both fire. Required action: a Ruff version or rule-selection change
> intentionally reviews and updates the enabled-rule inventory before changing
> the lock or configuration.

### [TS-3] add to the existing requirements

> - configured rule families extend Ruff's stable defaults; preview rules
>   remain opt-in and are not part of the default gate;
> - intentionally broad exception, retry, plugin, process, and best-effort
>   cleanup boundaries retain runtime behavior through explicit structure where
>   practical; a suppression is the reviewed last resort, not the default
>   alternative to a behavior-changing rewrite;
> - changing a public exception type, signature, resource lifetime, subprocess
>   policy, or output shape to satisfy lint requires direct compatibility proof
>   from owning tests or downstream type checks.

Keep existing requirements for `ruff check .`, ignores, formatter scope,
atomic activation, C901, raw audits, and suppression reconciliation. [TS-3.1]
already supports arbitrary registered rule sets and changes only if Task 7
produces owner-approved exceptions.

## 9. Dependency-Ordered Tasks

### Task 1: Independent plan and spec-delta review

- Read this plan, [TS-3]/[TS-3.1], the completed Weft C901 plan, SimpleBroker's
  completed lint-expansion plan, current policy code/tests, representative
  high-risk findings, and named exception/cohesion/policy-fixture lessons.
- Look for missing invariants, weak accounting, behavior changes disguised as
  style, broad exemptions, and tests that could pass without real Ruff.
- Done when a clean reviewer returns `PASS`, or all blockers are fixed and a
  scoped re-review passes.
- Stop before implementation if the reviewer cannot implement confidently or
  believes the cleanup would degrade failure containment.

### Task 2: Reproduce and freeze implementation evidence

- Rerun Section 6's temporary expanded-config probe. Record command, Ruff version,
  453-code set, current/added/removed rule sets, normal/raw JSON counts, offered
  fix counts, and git SHA in Section 19.
- Compare exact sets with both Weft's current fixture and SimpleBroker. Any
  removed current rule or SimpleBroker difference is a stop condition.
- Populate Section 16 for every affected function/class under `BLE001`,
  `TRY004`, `S110`, `S112`, `S102`, `SIM117`, `SIM115`, `PLW1510`, `PYI025`,
  `PYI034`, `PYI063`, `PYI064`, `RUF009`, `RUF012`, `RUF034`, and `TRY203`.
  Also include `PLR0402` sites governed by the facade-backedge architecture
  test and both `SIM905` multiline field-ledger sites; neither is mechanical.
  Group only findings sharing one owner, invariant, disposition, and proof.
- Each row records path/symbol, rules/count, current behavior, disposition,
  exact proof, and review state. Do not edit production source in this task.
- Stop on count drift, missing discovery, or undocumented public contracts.

### Task 3: Add failing policy proofs without activation

- Update `tests/specs/test_ruff_policy.py` in the worktree, but never commit a
  permanently red policy test separately from activation.
- Require `lint.select` absent; `lint.extend-select` exactly the eight current
  families; and target, ignores, McCabe, preview, per-file ignores, and six
  extensionless paths unchanged.
- Add real-stdin probes proving `BLE001` from stable defaults and a retained
  family rule absent from the curated defaults (use `B904` if still valid).
- Keep exact enabled-rule comparison; replace the fixture only after the
  453-code set is independently checked against SimpleBroker.
- Extract pure `_assert_ruff_policy(ruff, lint)` and `_assert_lint_job(job)`
  helpers so repository-owned structural policy can be mutation-tested without
  rewriting the real project file. The complete mutation matrix is:
  `select` versus `extend-select`; omitted/extra family; changed effective
  inventory code; target version; either global ignore; McCabe threshold;
  preview; per-file ignores; each exact extensionless path; Bash inclusion;
  normal-Ruff/suppression/format/mypy CI order; formatter ownership; and CI
  preview activation.
- Use the real Ruff binary where settings affect Ruff behavior:
  - parse `--show-settings` to prove target `py312`, McCabe 10, preview false,
    and the exact effective code set;
  - run stdin probes showing `E501` and `B008` are ignored, then use a temporary
    mutated config to show removal of each ignore makes its diagnostic fire;
  - use a temporary mutated config to show a per-file `F401` ignore hides a
    real probe and is rejected by `_assert_ruff_policy`;
  - show `select` loses the stable-default sentinel while `extend-select`
    retains both sentinels.
- Use `_assert_lint_job` mutations for CI ordering and formatter ownership,
  because those are repository workflow contracts rather than Ruff semantics.
- Record expected RED against current `lint.select`. Do not mock Ruff.
- Preserve C901, discovery, CI, normal-clean, and suppression tests.

### Task 4: Low-risk mechanical batches

- Work from Task 2 JSON and group by rule. Start with `RUF100`,
  `RUF022`, `PIE807`, `PIE790`, `PIE810`, `SIM118`, `FURB136`,
  and `FURB167`. Do not include `SIM905`: Ruff's rewrite expands the compact
  multiline ledgers into unreadable 100-plus-item line literals. Apply
  `PLR0402` only where the architecture test proves no facade-backedge contract;
  the seven production facade imports are Task 7 manual-risk findings.
- Use Ruff fixes for one named rule batch only. Never enable unsafe fixes.
- For `RUF100`, reconcile every changed `noqa` with [TS-3.1], raw inventory,
  and the suppression check. Never remove an approved group pointer blindly.
- For `RUF022`, preserve exact public membership and any proven order contract.
- Format changed paths only; run closest tests, current/expanded Ruff, relevant
  mypy, and suppression check.
- An independent reviewer inspects each coherent rule batch before acceptance.
  A batch is one rule and one mechanical rewrite shape; split when owner,
  semantics, or proof differs.

### Task 5: Typing, class-state, and data-model findings

- Address `PYI025`, `PYI034`, `PYI063`, `PYI064`, `RUF009`, and `RUF012` as
  contract work, not formatting.
- Read protocols/models and all implementations/callers before changing
  positional-only markers, context-manager returns, class variables, or
  defaults.
- Use `ClassVar`, `field(default_factory=...)`, or signature changes only when
  runtime and mypy consumers prove existing behavior.
- For test classes, distinguish intentional shared state from accidental
  instance defaults; no blanket `ClassVar` conversion.
- Add focused runtime/signature tests and run full configured mypy plus closest
  integration/extension tests.
- Every non-mechanical refactor enters Task 7's locality review queue.
- Stop and propose a suppression if Ruff's preferred shape breaks a public or
  downstream contract.

### Task 6: Control flow, resource, subprocess, and output

- Address `SIM102`, `SIM103`, `SIM114`, `SIM117`, `SIM115`, `PLW1510`,
  `FLY002`, `ISC004`, `RUF019`, `RUF034`, `RUF046`, `RUF059`, `RET501`,
  `PLR1711`, `FURB188`, `SIM905`, and remaining local simplifications.
- Preserve branch precedence and locality. Do not flatten nesting that conveys
  distinct proof stages or cleanup authority.
- For context/file lifetime, prove enter/exit order, partial-enter failure,
  suppression, lifetime, and traceback with real resource tests.
- For subprocesses, use `check=False` for intentionally inspected status;
  `check=True` only when nonzero is already fatal and characterized.
- Preserve exact CLI/log/file output for string rewrites.
- Treat `RUF034` as a possible real defect: write a failing test if branches
  should differ; remove the condition only if identical behavior is proven.
- Every behavior-relevant refactor enters Task 7's locality review queue.

### Task 7: Exception, security, and architecture boundaries and the rework queue

- Address `BLE001`, `TRY004`, `S110`, `S112`, `S102`, `TRY203`, and
  architecture-governed `PLR0402` last.
- Narrow catches only to exceptions raised by the enclosed boundary. Move
  serialization, validation, programming, and invariant checks out of
  best-effort broker/cleanup catches only when existing behavior permits it.
  Follow the `docs/lessons.md` exception-boundary lesson.
- Preserve broad catches at process, plugin, interpreter, cleanup, callback,
  and retry seams where extension failures must be contained.
- Use clearer structure or `contextlib.suppress` only when logging, traceback,
  hooks, cleanup, and retry behavior remain exact.
- Do not add logging to an intentionally silent path without checking output,
  observability, and retry-volume contracts.
- Change exception types only after direct caller/public compatibility proof.
- Keep intentional `exec` confined to architecture-test fixtures. If needed,
  make it a specific suppression candidate; do not weaken the test.
- Preserve the commands/core facade rule proven by
  `tests/architecture/test_import_boundaries.py::test_restored_facade_backedges_are_detected`.
  Do not accept Ruff's child-module import rewrite where that test requires a
  facade import; prefer a reviewed narrow suppression over an alias rename that
  hides the architecture from readers.
- Preserve traceback identity and hooks around immediate re-raise paths.

#### Fresh Python-expert locality review

After every behavior-relevant Section 16 owner/invariant group refactored in
Tasks 5-7, dispatch a fresh clean subagent acting as a Python expert. That
ledger group is the review unit; do not combine unrelated symbols into a large
review or create one review per trivial diagnostic inside the same cohesive
change. Give the reviewer the exact baseline/candidate diff, owning tests, rule
finding, and invariant. It returns `NET POSITIVE` or `NET NEGATIVE`, focusing
on logical locality and comprehensibility as well as correctness.

- `NET POSITIVE`: accept only after focused tests, mypy scope, expanded Ruff,
  and suppression check pass.
- `NET NEGATIVE`: do not revert or discard. Preserve the candidate in a
  dedicated worktree and append it to Section 17's FIFO queue with concrete
  improvement criteria. Rework the head, then use a different fresh reviewer.
- No queued/reworking/awaiting-review candidate may remain at activation. If
  the original cohesive code is best, make the finding a suppression candidate
  rather than forcing a worse refactor.
- Owner-directed checkpoint commits are required before candidate worktrees
  and after accepted behavior-relevant refactors. Review does not authorize a
  commit.

#### Suppression gate

- Zero new suppressions is the target.
- Before any directive, append a proposed [TS-3.1] row to Section 18 with
  stable ID, rules/cardinality, exact Section 16 ledger IDs, exact
  `path::qualified_symbol` sites, proposed source snippets, invariant, real
  proof, rejected alternatives, and review result.
- Map candidate raw diagnostics one-to-one to ledger rows, proposed groups,
  sites/snippets, proof, reviewer verdict, and owner approval. Before
  activation, validate locations and counts with the temporary expanded-config
  probe from Section 6; do not run the repository suppression tool and pretend
  the old 171-rule config can reconcile rules it does not enable.
- Obtain clean independent review and explicit owner approval for every row.
  Approval is attached to those exact sites/snippets, not only a rule/count or
  group rationale. Plan approval is not suppression approval.
- Task 7 records approved candidate material only. It does not edit [TS-3.1],
  source directives, global raw inventory, exact fixtures, or generated index.
  Those changes belong to Task 8's one candidate activation state.
- No rejected, pending, or unreviewed candidate may remain at activation.

### Task 8: Atomic policy and spec activation

- Apply Section 8's exact [TS-3] delta. Keep [TS-3.1] unchanged unless Task 7
  produced approved rows. Add the plan backlink and update nearby mapping if
  ownership changed.
- Rename `lint.select` to `lint.extend-select`; preserve the eight families,
  target, ignores, McCabe, discovery, CI order, and formatter scope.
- In the same candidate activation state, apply every approved [TS-3.1] human
  row, exact approved source directive, global raw inventory change, exact
  group/directive fixture change, and generated index change. Only now run
  `bin/ruff_suppression_index.py --write` and `--check`, because normal/raw Ruff
  must see the newly enabled rules for reconciliation. If this state does not
  reconcile, do not split or partially land it.
- Replace the enabled-rule fixture with the reviewed 453-code set, exactly
  matching SimpleBroker at Ruff 0.16.1.
- Complete Task 3 tests and observe the prior RED turn GREEN.
- Update `README.md`, `AGENTS.md`, and `testing-patterns.md` to use
  `ruff check .` for the repository gate. Keep clearly targeted examples.
- Do not change CI unless the policy test proves its current shape wrong.
- Run normal Ruff, `RUF100`, suppression, policy tests, format, and mypy before
  acceptance. Record the promotion baseline in Sections 4 and 19.

### Task 9: Full verification and completed-work review

- Run Section 11 from current accepted state.
- Normal Ruff must be clean. Raw findings must equal [TS-3.1]/global inventory.
- Review the full diff for containment, error contracts, resource ordering,
  subprocess policy, typing, exports, output, discovery, and formatter drift.
- Give a clean reviewer the promoted spec, plan, config, fixture/tests,
  suppression state/tool, complete source diff, ledgers, and evidence.
- Ask whether any fix changed behavior or reduced locality/comprehensibility,
  and whether any suppression exceeds its approved invariant.
- Resolve blockers and run scoped re-review.
- Mark plan/index completed only after implementation/verification closure is
  committed at owner direction. Status closure is a separate ledger commit.

## 10. Testing Strategy

### Failing-test-first

- Policy proof fails under `lint.select`, then passes after atomic activation.
- Semantic fixes start with the smallest test proving existing behavior or the
  defect. If Ruff finds a real bug, observe the regression test RED first.
- For provably mechanical edits, record pre/post diagnostic plus unchanged
  closest behavior test as the sanctioned equivalent proof.
- Never weaken a test, output assertion, or type contract for a lint rewrite.

### Keep these real

- Real Ruff subprocesses for config, discovery, firing, rule-set, and clean
  gates.
- `WeftTestHarness`, real queues/process paths, and real extension adapters for
  lifecycle, resource, retry, subprocess, or queue changes.
- Mock only already-external/nondeterministic seams. Do not create a parallel
  mock-heavy remediation suite.

### Per-slice proof

1. Closest owning tests.
2. Current Ruff on changed paths.
3. Isolated expanded-policy probe and regenerated counts.
4. Relevant mypy package for typing/signature changes.
5. Suppression check after any `noqa` or C901-adjacent edit.
6. Required batch or locality review before acceptance.

## 11. Verification And Gates

Focused policy gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest -n 0 \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py -q
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
```

Final gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -n 0 \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
uv lock --check
git diff --check
```

Run `./.venv/bin/python bin/pytest-pg --all` if accepted cleanup touches shared
queue, manager, watcher, monitor, result, or runtime behavior. Run the closest
extension/integration suite for every changed extension/integration symbol.

`bin/check-doc-paths` is advisory and may retain the exact eight reviewed
baseline claims or reach zero; this work adds none. Backstitch needs the
explicit roots above and must add no error/warning keyed to [TS-3], [TS-3.1],
this plan, Ruff policy surfaces, suppression tool, or changed code. Compare
issues by code/path/symbol/section, not aggregate count. If the sibling tool is
absent, record a blocker rather than claim success.

## 12. Rollout And Rollback

The merge gate is rollout. Preparatory behavior-preserving cleanup may land as
reviewed checkpoints while old policy remains active. Spec, `extend-select`,
fixture, tests, docs, and approved suppressions activate atomically only when
the expanded probe is clean.

Rollback the atomic activation as a unit for configuration/discovery/CI
failure. Accepted behavior-preserving cleanup may remain. If cleanup proves
behavior-changing, repair or revert that specific checkpoint; do not disable
the expanded policy to hide it.

No data migration, persisted-format change, deployment ordering, or one-way
door exists. CI's clean Ruff -> suppression -> format -> mypy -> test sequence
is the observable rollout signal.

## 13. Stop And Re-plan Gates

Stop for owner direction if:

- the locked effective set no longer matches SimpleBroker;
- a fix wants a public API/error/queue/TaskSpec/result/runtime change;
- an invariant needs a global/per-file ignore, threshold change, blanket
  directive, or unregistered suppression;
- a `NET NEGATIVE` refactor cannot be reworked without locality/behavior harm;
- proof requires a second execution path or mock-heavy harness;
- atomic activation cannot stay coherent;
- full verification exposes a regression not owned by a cleanup slice.

## 14. Out Of Scope

- Preview rules or `select = ["ALL"]`.
- Ruff/Python target refresh.
- New lint/type/format tools, dependencies, or CI topology.
- Formatter expansion to `.`, Markdown, or historical docs.
- Rewriting completed plans or dated lessons for current commands.
- Refactoring cohesive runtime/test/extension owners for size or style alone.
- Behavior, exception, logging, retry, cleanup, subprocess, storage, queue,
  CLI, or typing changes without direct compatibility proof and scope review.
- Copying SimpleBroker's suppressions, counts, Python target, or broad `bin/*`.

## 15. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [TS-3.1] Task 4 `RUF100` preflight | Apply only the 38 candidate-policy stale-directive fixes. | A first command used `--select RUF100 --fix`, which made all other suppressed rules appear disabled and proposed 193 removals. The result was rejected before review and every `noqa` hunk was mechanically restored; suppression reconciliation and the expected 38-count inventory pass again. | Ruff's CLI `--select` replaces the effective selection for unused-directive analysis. The full candidate rule set must remain active while the fix scope is restricted separately. | None. Durable command-safety lesson added; no broad result was accepted. |

## 16. Manual-Risk Finding Ledger

Populate Task 2 before source edits. Group only one owner/invariant/disposition.

### Task 2 inventory

Generated read-only from repository SHA `d0254ce45a757a7351fa0e7ca76f389790a1800b` on 2026-08-06. The authoritative corrected activation simulation (temporary `pyproject.toml` identical to the repository config except `lint.select` -> `lint.extend-select`) is 599 normal diagnostics across 146 files and 38 codes; 773 raw diagnostics across 156 files and 41 codes; 453 effective rules, exact with SimpleBroker. The isolated owner-attribution pass below has unchanged manual-risk cardinality.

- Required manual-risk findings: **390** across **258 owner/category rows**.
- Required rule counts: `BLE001=186, PLW1510=10, PYI025=1, PYI034=1, PYI063=11, PYI064=15, RUF009=1, RUF012=15, RUF034=1, S102=3, S110=46, S112=2, SIM115=1, SIM117=21, TRY004=75, TRY203=1`.
- Additional requested facade/simplification findings: **11** (`PLR0402=9, SIM905=2`).
- Total ledger rows: **268**. Blocked rows: **0**. Rows marked `pending` or `candidate` still require the named compatibility, locality, or suppression review before an edit is accepted.
- Symbol attribution matches `bin/ruff_suppression_index.py`: nested functions are attributed to their enclosing outermost function; methods remain class-qualified; lines outside a function are `<module>`.

| ID | Path::outermost owner | Rules/counts and sites | Current invariant | Proposed disposition | Closest exact proof | Review state |
|---|---|---|---|---|---|---|
| MR-001 | `bin/check-dom15-fixtures::<module>` | `BLE001=1`; `BLE001@214` | The top-level checker boundary converts any internal failure into a clean diagnostic and exit 2; no traceback reaches the caller. | Preserve the broad no-traceback boundary and register an exact BLE001 suppression candidate. | `./bin/check-dom15-fixtures` | candidate: independent suppression review |
| MR-002 | `bin/coalesce-check::git` | `PLW1510=1`; `PLW1510@62` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./bin/coalesce-check` | ready: mechanical candidate |
| MR-003 | `bin/launch_manager.py::_wait_for_registry` | `BLE001=1`; `BLE001@81` | A registry read failure is treated as no entries for that poll; the bounded startup wait continues instead of crashing the launcher. | Preserve retry-on-read-failure semantics. Narrow only with complete broker exception proof; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_manager.py` | candidate: independent suppression/locality review |
| MR-004 | `bin/pytest-pg::_kill_pytest_process_tree` | `BLE001=2`; `BLE001@452; BLE001@455` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_pytest_pg_script.py` | candidate: independent suppression/locality review |
| MR-005 | `bin/pytest-pg::_send_pytest_interrupt` | `BLE001=2`; `BLE001@416; BLE001@419` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_pytest_pg_script.py` | candidate: independent suppression/locality review |
| MR-006 | `bin/pytest-pg::_terminate_pytest_process_tree` | `BLE001=2`; `BLE001@433; BLE001@436` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_pytest_pg_script.py` | candidate: independent suppression/locality review |
| MR-007 | `bin/pytest-pg::main` | `BLE001=1`; `BLE001@572` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_pytest_pg_script.py` | candidate: independent suppression/locality review |
| MR-008 | `extensions/weft_docker/tests/test_docker_plugin.py::<module>` | `PLR0402=1`; `PLR0402@16` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: facade/monkeypatch identity review |
| MR-009 | `extensions/weft_docker/tests/test_docker_plugin.py::test_command_runner_cleans_up_container_when_runtime_start_fails` | `RUF012=1`; `RUF012@1017` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py -k test_command_runner_cleans_up_container_when_runtime_start_fails` | pending: shared-vs-instance state review |
| MR-010 | `extensions/weft_docker/tests/test_docker_plugin.py::test_command_runner_waits_for_container_to_leave_created_before_runtime_handle` | `RUF012=1`; `RUF012@811` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py -k test_command_runner_waits_for_container_to_leave_created_before_runtime_handle` | pending: shared-vs-instance state review |
| MR-011 | `extensions/weft_docker/tests/test_docker_plugin.py::test_describe_runtime_falls_back_to_container_id_when_name_lookup_misses` | `RUF012=1`; `RUF012@686` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py -k test_describe_runtime_falls_back_to_container_id_when_name_lookup_misses` | pending: shared-vs-instance state review |
| MR-012 | `extensions/weft_docker/tests/test_docker_plugin.py::test_describe_runtime_falls_back_to_container_list_when_name_get_misses` | `RUF012=1`; `RUF012@743` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py -k test_describe_runtime_falls_back_to_container_list_when_name_get_misses` | pending: shared-vs-instance state review |
| MR-013 | `extensions/weft_docker/tests/test_docker_plugin.py::test_wait_for_container_runtime_start_fails_when_created_state_sticks` | `RUF012=1`; `RUF012@968` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py -k test_wait_for_container_runtime_start_fails_when_created_state_sticks` | pending: shared-vs-instance state review |
| MR-014 | `extensions/weft_docker/weft_docker/_sdk.py::wait_for_container_runtime_start` | `BLE001=1`; `BLE001@63` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-015 | `extensions/weft_docker/weft_docker/agent_runner.py::DockerProviderCLIRunner.run_with_hooks` | `BLE001=6, S110=5`; `BLE001@241; S110@241; BLE001@246; S110@246; BLE001@286; S110@286; BLE001@291; S110@291; BLE001@333; BLE001@361; S110@361` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_runner.py` | candidate: independent suppression/locality review |
| MR-016 | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_mounts` | `TRY004=2`; `TRY004@571; TRY004@575` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_runner.py` | pending: exception-contract compatibility review |
| MR-017 | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_optional_text` | `TRY004=1`; `TRY004@761` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_runner.py` | pending: exception-contract compatibility review |
| MR-018 | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_required_text` | `TRY004=1`; `TRY004@770` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_runner.py` | pending: exception-contract compatibility review |
| MR-019 | `extensions/weft_docker/weft_docker/agent_runner.py::_normalize_work_item_mounts` | `TRY004=4`; `TRY004@599; TRY004@604; TRY004@625; TRY004@628` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_runner.py` | pending: exception-contract compatibility review |
| MR-020 | `extensions/weft_docker/weft_docker/images.py::_image_exists` | `BLE001=1`; `BLE001@123` | Any Docker SDK lookup failure is currently reported as image absence, preserving the build-or-fetch fallback. | Preserve the SDK-to-bool adapter contract. Narrow only if all supported SDK failures are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_agent_images.py` | candidate: independent suppression/locality review |
| MR-021 | `extensions/weft_docker/weft_docker/plugin.py::_cleanup_process` | `BLE001=2`; `BLE001@1273; BLE001@1277` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-022 | `extensions/weft_docker/weft_docker/plugin.py::_docker_container_liveness` | `BLE001=1`; `BLE001@794` | A Docker reload failure maps to `unknown` liveness rather than a false live/stale claim or an escaped SDK failure. | Preserve the SDK-to-liveness adapter contract. Narrow only if all supported SDK failures are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-023 | `extensions/weft_docker/weft_docker/plugin.py::_docker_kill` | `BLE001=1`; `BLE001@1253` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-024 | `extensions/weft_docker/weft_docker/plugin.py::_docker_runtime_liveness` | `BLE001=1`; `BLE001@754` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-025 | `extensions/weft_docker/weft_docker/plugin.py::_docker_stop` | `BLE001=1`; `BLE001@1242` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-026 | `extensions/weft_docker/weft_docker/plugin.py::_lookup_container` | `BLE001=1`; `BLE001@1204` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-027 | `extensions/weft_docker/weft_docker/plugin.py::_mapping_of_strings` | `TRY004=1`; `TRY004@1491` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-028 | `extensions/weft_docker/weft_docker/plugin.py::_normalize_mounts` | `TRY004=2`; `TRY004@1432; TRY004@1447` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-029 | `extensions/weft_docker/weft_docker/plugin.py::_normalize_required_text` | `TRY004=1`; `TRY004@1473` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-030 | `extensions/weft_docker/weft_docker/plugin.py::_optional_string` | `TRY004=1`; `TRY004@1500` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-031 | `extensions/weft_docker/weft_docker/plugin.py::_remove_container` | `BLE001=1`; `BLE001@1264` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | candidate: independent suppression/locality review |
| MR-032 | `extensions/weft_docker/weft_docker/plugin.py::_require_mapping` | `TRY004=1`; `TRY004@1482` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-033 | `extensions/weft_docker/weft_docker/plugin.py::_string_list` | `TRY004=1`; `TRY004@1509` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_docker_plugin.py` | pending: exception-contract compatibility review |
| MR-034 | `extensions/weft_docker/weft_docker/profiles.py::_load_profile_file` | `TRY004=1`; `TRY004@209` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-035 | `extensions/weft_docker/weft_docker/profiles.py::_mapping_of_strings` | `TRY004=1`; `TRY004@392` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-036 | `extensions/weft_docker/weft_docker/profiles.py::_require_mapping` | `TRY004=1`; `TRY004@419` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-037 | `extensions/weft_docker/weft_docker/profiles.py::_required_text` | `TRY004=1`; `TRY004@410` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-038 | `extensions/weft_docker/weft_docker/profiles.py::_resolve_profile_mounts` | `TRY004=2`; `TRY004@321; TRY004@330` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-039 | `extensions/weft_docker/weft_docker/profiles.py::_string_list` | `TRY004=1`; `TRY004@401` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_docker/tests/test_container_profiles.py` | pending: exception-contract compatibility review |
| MR-040 | `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py::_require_mapping` | `TRY004=1`; `TRY004@346` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_macos_sandbox/tests/test_macos_sandbox_plugin.py` | pending: exception-contract compatibility review |
| MR-041 | `extensions/weft_microsandbox/tests/test_controls.py::<module>` | `RUF012=2`; `RUF012@15; RUF012@16` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_controls.py` | pending: shared-vs-instance state review |
| MR-042 | `extensions/weft_microsandbox/tests/test_runtime_adapter.py::test_copy_into_guest_recursively_copies_directory_contents` | `RUF012=2`; `RUF012@149; RUF012@150` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py -k test_copy_into_guest_recursively_copies_directory_contents` | pending: shared-vs-instance state review |
| MR-043 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_mapping_of_strings` | `TRY004=1`; `TRY004@301` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-044 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_normalize_mode` | `TRY004=1`; `TRY004@230` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-045 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_normalize_mounts` | `TRY004=3`; `TRY004@271; TRY004@275; TRY004@286` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-046 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_normalize_network` | `TRY004=1`; `TRY004@241` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-047 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_normalize_workspace_mode` | `TRY004=1`; `TRY004@252` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-048 | `extensions/weft_microsandbox/weft_microsandbox/_options.py::_require_mapping` | `TRY004=1`; `TRY004@307` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py` | pending: exception-contract compatibility review |
| MR-049 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::MicrosandboxRuntime._describe_async` | `BLE001=2, S110=1`; `BLE001@246; BLE001@261; S110@261` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-050 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::MicrosandboxRuntime._kill_async` | `BLE001=1`; `BLE001@234` | Any SDK lookup or kill failure maps to `False`; the runtime control adapter does not leak provider exceptions. | Preserve the SDK-to-bool control contract. Narrow only with complete provider exception proof; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-051 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::MicrosandboxRuntime._stop_async` | `BLE001=1`; `BLE001@224` | Any SDK lookup or stop failure maps to `False`; the runtime control adapter does not leak provider exceptions. | Preserve the SDK-to-bool control contract. Narrow only with complete provider exception proof; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-052 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_cleanup_sandbox` | `BLE001=2, S110=2`; `BLE001@447; S110@447; BLE001@451; S110@451` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-053 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_copy_back_files` | `BLE001=1, S110=1`; `BLE001@334; S110@334` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-054 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_exec_with_cancel` | `BLE001=2, S110=1`; `BLE001@417; S110@417; BLE001@424` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-055 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_is_timeout_error` | `BLE001=1`; `BLE001@458` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-056 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_mkdir_guest` | `BLE001=1, S110=1`; `BLE001@366; S110@366` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-057 | `extensions/weft_microsandbox/weft_microsandbox/_runtime.py::_sandbox_name` | `BLE001=1`; `BLE001@438` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_runtime_adapter.py extensions/weft_microsandbox/tests/test_command_runner.py` | candidate: independent suppression/locality review |
| MR-058 | `extensions/weft_microsandbox/weft_microsandbox/plugin.py::MicrosandboxRunner._execute` | `BLE001=2, S110=1`; `BLE001@268; S110@268; BLE001@352` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_command_runner.py extensions/weft_microsandbox/tests/test_controls.py` | candidate: independent suppression/locality review |
| MR-059 | `extensions/weft_microsandbox/weft_microsandbox/plugin.py::_emit_stream` | `BLE001=1, S110=1`; `BLE001@524; S110@524` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_command_runner.py extensions/weft_microsandbox/tests/test_controls.py` | candidate: independent suppression/locality review |
| MR-060 | `extensions/weft_microsandbox/weft_microsandbox/plugin.py::_safe_callback` | `BLE001=1, S110=1`; `BLE001@536; S110@536` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q extensions/weft_microsandbox/tests/test_command_runner.py extensions/weft_microsandbox/tests/test_controls.py` | candidate: independent suppression/locality review |
| MR-061 | `integrations/weft_django/tests/test_weft_django.py::test_deferred_native_submission_validates_unknown_overrides_before_commit` | `SIM117=1`; `SIM117@354` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py -k test_deferred_native_submission_validates_unknown_overrides_before_commit` | ready: behavior-preserving candidate |
| MR-062 | `integrations/weft_django/tests/test_weft_django.py::test_deferred_spec_reference_validates_missing_reference_before_commit` | `SIM117=1`; `SIM117@364` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py -k test_deferred_spec_reference_validates_missing_reference_before_commit` | ready: behavior-preserving candidate |
| MR-063 | `integrations/weft_django/tests/test_weft_django.py::test_enqueue_on_commit_rollbacks_do_not_bind` | `SIM117=1`; `SIM117@326` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py -k test_enqueue_on_commit_rollbacks_do_not_bind` | ready: behavior-preserving candidate |
| MR-064 | `integrations/weft_django/tests/test_weft_django.py::test_transport_validation` | `SIM117=1`; `SIM117@599` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py -k test_transport_validation` | ready: behavior-preserving candidate |
| MR-065 | `integrations/weft_django/tests/test_weft_django.py::test_url_import_requires_authz_setting` | `SIM117=1`; `SIM117@592` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py -k test_url_import_requires_authz_setting` | ready: behavior-preserving candidate |
| MR-066 | `integrations/weft_django/weft_django/channels.py::TaskEventsConsumer._stream_events` | `TRY203=1`; `TRY203@123` | CancelledError is re-raised unchanged while finally still cancels and closes the event stream. | Remove only the redundant catch/re-raise; prove cancellation and iterator close behavior. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py` | ready: behavior-preserving candidate |
| MR-067 | `integrations/weft_django/weft_django/client.py::_apply_taskspec_payload_overrides` | `TRY004=2`; `TRY004@232; TRY004@245` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q integrations/weft_django/tests/test_weft_django.py` | pending: exception-contract compatibility review |
| MR-068 | `tests/architecture/test_import_boundaries.py::test_agent_backend_package_exports_keep_identity` | `S102=1`; `S102@938` | exec is intentional test-only architecture machinery used to exercise import forms and identity in an isolated namespace. | Keep the fixture and register an exact S102 suppression candidate; do not weaken the architecture proof. | `./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py -k test_agent_backend_package_exports_keep_identity` | candidate: independent suppression review |
| MR-069 | `tests/architecture/test_import_boundaries.py::test_commands_manager_export_supports_attribute_from_and_star_imports` | `S102=2`; `S102@975; S102@979` | exec is intentional test-only architecture machinery used to exercise import forms and identity in an isolated namespace. | Keep the fixture and register an exact S102 suppression candidate; do not weaken the architecture proof. | `./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py -k test_commands_manager_export_supports_attribute_from_and_star_imports` | candidate: independent suppression review |
| MR-070 | `tests/cli/test_cli.py::TestCLIConstants.test_constants_override` | `SIM117=1`; `SIM117@118` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/cli/test_cli.py -k test_constants_override` | ready: behavior-preserving candidate |
| MR-071 | `tests/cli/test_cli.py::TestModuleExecution.test_module_help` | `PLW1510=1`; `PLW1510@77` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/cli/test_cli.py -k test_module_help` | ready: mechanical candidate |
| MR-072 | `tests/cli/test_cli.py::TestModuleExecution.test_module_no_args` | `PLW1510=1`; `PLW1510@87` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/cli/test_cli.py -k test_module_no_args` | ready: mechanical candidate |
| MR-073 | `tests/cli/test_cli.py::TestModuleExecution.test_module_version` | `PLW1510=1`; `PLW1510@67` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/cli/test_cli.py -k test_module_version` | ready: mechanical candidate |
| MR-074 | `tests/cli/test_cli_result.py::_wait_for_outbox` | `BLE001=1, S110=1`; `BLE001@41; S110@41` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_result.py` | candidate: independent suppression/locality review |
| MR-075 | `tests/cli/test_env_file_bootstrap.py::_probe_default_export_path` | `PLW1510=1`; `PLW1510@71` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/cli/test_env_file_bootstrap.py` | ready: mechanical candidate |
| MR-076 | `tests/cli/test_env_file_bootstrap.py::_run_module` | `PLW1510=1`; `PLW1510@38` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/cli/test_env_file_bootstrap.py` | ready: mechanical candidate |
| MR-077 | `tests/commands/test_queue.py::test_watch_queue_closes_generator_when_limit_stops_iteration` | `RUF012=1`; `RUF012@381` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/commands/test_queue.py -k test_watch_queue_closes_generator_when_limit_stops_iteration` | pending: shared-vs-instance state review |
| MR-078 | `tests/commands/test_queue.py::test_watch_queue_uses_queue_monitor` | `RUF012=1`; `RUF012@340` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/commands/test_queue.py -k test_watch_queue_uses_queue_monitor` | pending: shared-vs-instance state review |
| MR-079 | `tests/commands/test_task_evidence.py::_probe_with_task` | `BLE001=1`; `BLE001@227` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_evidence.py` | candidate: independent suppression/locality review |
| MR-080 | `tests/commands/test_task_evidence.py::test_known_tid_ping_pong_updates_task_status` | `BLE001=1`; `BLE001@695` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_evidence.py -k test_known_tid_ping_pong_updates_task_status` | candidate: independent suppression/locality review |
| MR-081 | `tests/conftest.py::_register_cli_outputs` | `BLE001=1, S112=1`; `BLE001@471; S112@471` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/conftest.py` | candidate: independent suppression/locality review |
| MR-082 | `tests/conftest.py::broker_env` | `BLE001=1, S110=1`; `BLE001@339; S110@339` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/conftest.py` | candidate: independent suppression/locality review |
| MR-083 | `tests/conftest.py::queue_factory` | `BLE001=1, S110=1`; `BLE001@310; S110@310` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/conftest.py` | candidate: independent suppression/locality review |
| MR-084 | `tests/conftest.py::run_cli` | `BLE001=1`; `BLE001@437` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/conftest.py` | candidate: independent suppression/locality review |
| MR-085 | `tests/conftest.py::run_cli` | `PLW1510=1`; `PLW1510@416` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/conftest.py` | ready: mechanical candidate |
| MR-086 | `tests/conftest.py::task_factory` | `BLE001=1, S110=1`; `BLE001@362; S110@362` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/conftest.py` | candidate: independent suppression/locality review |
| MR-087 | `tests/core/test_manager.py::test_manager_cleanup_waits_for_active_child_launch_worker` | `BLE001=1`; `BLE001@4079` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py -k test_manager_cleanup_waits_for_active_child_launch_worker` | candidate: independent suppression/locality review |
| MR-088 | `tests/core/test_manager.py::test_manager_late_child_launch_self_reaps_after_cleanup_deadline` | `BLE001=1`; `BLE001@4150` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py -k test_manager_late_child_launch_self_reaps_after_cleanup_deadline` | candidate: independent suppression/locality review |
| MR-089 | `tests/core/test_manager.py::test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue` | `RUF012=1`; `RUF012@4197` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py -k test_manager_terminal_envelope_does_not_cache_child_ctrl_out_queue` | pending: shared-vs-instance state review |
| MR-090 | `tests/core/test_manager.py::test_manager_terminal_envelope_skips_when_task_terminal_proof_exists` | `RUF012=1`; `RUF012@4270` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py -k test_manager_terminal_envelope_skips_when_task_terminal_proof_exists` | pending: shared-vs-instance state review |
| MR-091 | `tests/core/test_monitor_store.py::_monitor_message_ids` | `SIM117=1`; `SIM117@141` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py` | ready: behavior-preserving candidate |
| MR-092 | `tests/core/test_monitor_store.py::_monitor_table_count` | `SIM117=1`; `SIM117@129` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py` | ready: behavior-preserving candidate |
| MR-093 | `tests/core/test_monitor_store.py::test_monitor_store_lists_raw_deleted_child_refs_for_repair` | `SIM117=1`; `SIM117@1245` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py -k test_monitor_store_lists_raw_deleted_child_refs_for_repair` | ready: behavior-preserving candidate |
| MR-094 | `tests/core/test_monitor_store.py::test_monitor_store_prunes_legacy_message_tombstones` | `SIM117=1`; `SIM117@1312` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py -k test_monitor_store_prunes_legacy_message_tombstones` | ready: behavior-preserving candidate |
| MR-095 | `tests/core/test_monitor_store.py::test_monitor_store_rejects_newer_schema_version` | `SIM117=1`; `SIM117@1486` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py -k test_monitor_store_rejects_newer_schema_version` | ready: behavior-preserving candidate |
| MR-096 | `tests/core/test_monitor_store.py::test_store_sidecar_session_rolls_back_on_exception` | `SIM117=1`; `SIM117@162` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py -k test_store_sidecar_session_rolls_back_on_exception` | ready: behavior-preserving candidate |
| MR-097 | `tests/core/test_ops_shared.py::test_realtime_events_emits_state_when_terminal_derived_from_snapshot` | `RUF012=1`; `RUF012@400` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/core/test_ops_shared.py -k test_realtime_events_emits_state_when_terminal_derived_from_snapshot` | pending: shared-vs-instance state review |
| MR-098 | `tests/core/test_ops_shared.py::test_realtime_events_uses_terminal_state_seen_during_materialization` | `RUF012=1`; `RUF012@316` | Mutable attributes on the local fake are intentionally shared at class scope within the owning test unless the test resets/overrides them. | Declare ClassVar when sharing is intentional; otherwise initialize per instance. The owning test must prove the choice. | `./.venv/bin/python -m pytest -q tests/core/test_ops_shared.py -k test_realtime_events_uses_terminal_state_seen_during_materialization` | pending: shared-vs-instance state review |
| MR-099 | `tests/core/test_runner_plugins.py::<module>` | `RUF009=1`; `RUF009@16` | Each fake plugin currently receives an equal immutable RunnerCapabilities default. | Use field(default_factory=RunnerCapabilities); prove fake-plugin behavior. | `./.venv/bin/python -m pytest -q tests/core/test_runner_plugins.py` | ready: mechanical dataclass candidate |
| MR-100 | `tests/core/test_runtime_handle_liveness.py::<module>` | `PLR0402=1`; `PLR0402@7` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_runtime_handle_liveness.py` | pending: facade/monkeypatch identity review |
| MR-101 | `tests/fixtures/mcp_stdio_fixture.py::_read_response` | `TRY004=1`; `TRY004@306` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/fixtures/mcp_stdio_fixture.py` | pending: exception-contract compatibility review |
| MR-102 | `tests/fixtures/mcp_stdio_fixture.py::_server_command` | `TRY004=2`; `TRY004@267; TRY004@271` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/fixtures/mcp_stdio_fixture.py` | pending: exception-contract compatibility review |
| MR-103 | `tests/fixtures/mcp_stdio_fixture.py::call_fixture_tool` | `TRY004=1`; `TRY004@113` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/fixtures/mcp_stdio_fixture.py` | pending: exception-contract compatibility review |
| MR-104 | `tests/fixtures/provider_cli_fixture.py::_execute_fixture_request` | `BLE001=1`; `BLE001@598` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/fixtures/provider_cli_fixture.py` | candidate: independent suppression/locality review |
| MR-105 | `tests/fixtures/provider_cli_fixture.py::_load_claude_mcp_servers` | `TRY004=2`; `TRY004@651; TRY004@654` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/fixtures/provider_cli_fixture.py` | pending: exception-contract compatibility review |
| MR-106 | `tests/helpers/multiqueue_sigint_probe.py::main` | `BLE001=1`; `BLE001@103` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/multiqueue_sigint_probe.py` | candidate: independent suppression/locality review |
| MR-107 | `tests/helpers/test_backend.py::cleanup_postgres_schema_for_root` | `BLE001=1`; `BLE001@252` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/test_backend.py` | candidate: independent suppression/locality review |
| MR-108 | `tests/helpers/test_backend.py::cleanup_prepared_roots` | `BLE001=1, S112=1`; `BLE001@217; S112@217` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/test_backend.py` | candidate: independent suppression/locality review |
| MR-109 | `tests/helpers/weft_harness.py::WeftTestHarness.__del__` | `BLE001=1, S110=1`; `BLE001@123; S110@123` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-110 | `tests/helpers/weft_harness.py::WeftTestHarness.__enter__` | `PYI034=1`; `PYI034@103` | __enter__ returns the same harness instance; callers rely on the context manager type. | Annotate with Self only after runtime and full mypy proof. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | ready: typing-contract candidate |
| MR-111 | `tests/helpers/weft_harness.py::WeftTestHarness._close_live_database_queues` | `BLE001=1, S110=1`; `BLE001@1231; S110@1231` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-112 | `tests/helpers/weft_harness.py::WeftTestHarness._format_debug_payload` | `BLE001=1`; `BLE001@242` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-113 | `tests/helpers/weft_harness.py::WeftTestHarness._load_tid_mapping_payloads` | `BLE001=1`; `BLE001@696` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-114 | `tests/helpers/weft_harness.py::WeftTestHarness._peek_queue_lines` | `BLE001=1`; `BLE001@260` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-115 | `tests/helpers/weft_harness.py::WeftTestHarness._terminate_pid` | `BLE001=1`; `BLE001@1135` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/helpers/weft_harness.py` | candidate: independent suppression/locality review |
| MR-116 | `tests/long_session_surface_benchmark.py::ApiSurface._invoke_run` | `SIM117=1`; `SIM117@526` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/long_session_surface_benchmark.py` | ready: behavior-preserving candidate |
| MR-117 | `tests/long_session_surface_benchmark.py::main` | `BLE001=1`; `BLE001@1192` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/long_session_surface_benchmark.py` | candidate: independent suppression/locality review |
| MR-118 | `tests/multiqueue_polling_benchmark.py::main` | `BLE001=1`; `BLE001@709` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/multiqueue_polling_benchmark.py` | candidate: independent suppression/locality review |
| MR-119 | `tests/system/test_constants.py::TestLoadConfig.test_log_tasks_retention_period_rejects_zero` | `SIM117=1`; `SIM117@635` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_log_tasks_retention_period_rejects_zero` | ready: behavior-preserving candidate |
| MR-120 | `tests/system/test_constants.py::TestLoadConfig.test_removed_task_monitor_env_rejects` | `SIM117=1`; `SIM117@664` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_removed_task_monitor_env_rejects` | ready: behavior-preserving candidate |
| MR-121 | `tests/system/test_constants.py::TestLoadConfig.test_removed_task_monitor_task_log_cutoff_rejects` | `SIM117=1`; `SIM117@646` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_removed_task_monitor_task_log_cutoff_rejects` | ready: behavior-preserving candidate |
| MR-122 | `tests/system/test_constants.py::TestLoadConfig.test_reserved_cleanup_min_age_rejects_negative` | `SIM117=1`; `SIM117@579` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_reserved_cleanup_min_age_rejects_negative` | ready: behavior-preserving candidate |
| MR-123 | `tests/system/test_constants.py::TestLoadConfig.test_task_monitor_batch_size_rejects_zero` | `SIM117=1`; `SIM117@604` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_task_monitor_batch_size_rejects_zero` | ready: behavior-preserving candidate |
| MR-124 | `tests/system/test_constants.py::TestLoadConfig.test_task_monitor_interval_rejects_below_heartbeat_minimum` | `SIM117=1`; `SIM117@591` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_task_monitor_interval_rejects_below_heartbeat_minimum` | ready: behavior-preserving candidate |
| MR-125 | `tests/system/test_constants.py::TestLoadConfig.test_task_monitor_store_write_batch_size_rejects_zero` | `SIM117=1`; `SIM117@624` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_task_monitor_store_write_batch_size_rejects_zero` | ready: behavior-preserving candidate |
| MR-126 | `tests/system/test_constants.py::TestLoadConfig.test_task_monitor_task_log_scan_limit_rejects_zero` | `SIM117=1`; `SIM117@613` | Nested context managers currently acquire in lexical order and release in reverse order while preserving exception propagation. | Combine with one with statement only if enter/exit/suppression order is identical; run the exact owner test and fresh locality review. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py -k test_task_monitor_task_log_scan_limit_rejects_zero` | ready: behavior-preserving candidate |
| MR-127 | `tests/tasks/process_target.py::run_task` | `SIM115=1`; `SIM115@59` | Each temporary file intentionally remains open through the simulated workload so resource monitoring observes `open_files`; the same function closes every handle and unlinks every path before return. | Preserve workload-length ownership and register an exact SIM115 suppression candidate; a `with` rewrite would shorten or awkwardly fragment the intended lifetime. | `./.venv/bin/python -m pytest -q tests/specs/resource_management/test_resource_metrics.py tests/tasks/test_task_execution.py -k cleanup_on_exit_process_target` | candidate: independent suppression/locality review |
| MR-128 | `tests/tasks/test_command_runner_parity.py::_ensure_docker_image_available` | `PLW1510=2`; `PLW1510@182; PLW1510@196` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/tasks/test_command_runner_parity.py` | ready: mechanical candidate |
| MR-129 | `tests/tasks/test_command_runner_parity.py::_skip_unavailable_runner` | `BLE001=1`; `BLE001@165` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_command_runner_parity.py` | candidate: independent suppression/locality review |
| MR-130 | `tests/tasks/test_multiqueue_watcher.py::test_background_add_queue_rebinds_exact_set_on_drive_owner` | `BLE001=1`; `BLE001@129` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_add_queue_rebinds_exact_set_on_drive_owner` | candidate: independent suppression/locality review |
| MR-131 | `tests/tasks/test_multiqueue_watcher.py::test_background_add_with_preexisting_message_forces_immediate_discovery` | `BLE001=1`; `BLE001@578` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_add_with_preexisting_message_forces_immediate_discovery` | candidate: independent suppression/locality review |
| MR-132 | `tests/tasks/test_multiqueue_watcher.py::test_background_membership_errors_return_to_requesting_thread` | `BLE001=1`; `BLE001@1816` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_membership_errors_return_to_requesting_thread` | candidate: independent suppression/locality review |
| MR-133 | `tests/tasks/test_multiqueue_watcher.py::test_background_mutation_after_drive_reservation_waits_for_owner_claim` | `BLE001=1`; `BLE001@327` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_mutation_after_drive_reservation_waits_for_owner_claim` | candidate: independent suppression/locality review |
| MR-134 | `tests/tasks/test_multiqueue_watcher.py::test_background_mutation_committed_before_stop_returns_success` | `BLE001=1`; `BLE001@1060` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_mutation_committed_before_stop_returns_success` | candidate: independent suppression/locality review |
| MR-135 | `tests/tasks/test_multiqueue_watcher.py::test_background_mutation_from_handler_is_rejected_before_effects` | `BLE001=1`; `BLE001@1763` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_mutation_from_handler_is_rejected_before_effects` | candidate: independent suppression/locality review |
| MR-136 | `tests/tasks/test_multiqueue_watcher.py::test_background_mutation_racing_stop_never_binds_after_owner_close` | `BLE001=1`; `BLE001@929` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_mutation_racing_stop_never_binds_after_owner_close` | candidate: independent suppression/locality review |
| MR-137 | `tests/tasks/test_multiqueue_watcher.py::test_background_post_replace_publication_failure_restores_old_waiter` | `BLE001=1`; `BLE001@863` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_post_replace_publication_failure_restores_old_waiter` | candidate: independent suppression/locality review |
| MR-138 | `tests/tasks/test_multiqueue_watcher.py::test_background_queue_open_failure_preserves_old_generation` | `BLE001=1`; `BLE001@704` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_queue_open_failure_preserves_old_generation` | candidate: independent suppression/locality review |
| MR-139 | `tests/tasks/test_multiqueue_watcher.py::test_background_rebind_before_first_strategy_start_closes_unbound_cached_waiter` | `BLE001=1`; `BLE001@391` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_rebind_before_first_strategy_start_closes_unbound_cached_waiter` | candidate: independent suppression/locality review |
| MR-140 | `tests/tasks/test_multiqueue_watcher.py::test_background_remove_queue_rebinds_exact_remaining_set` | `BLE001=1`; `BLE001@193` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_remove_queue_rebinds_exact_remaining_set` | candidate: independent suppression/locality review |
| MR-141 | `tests/tasks/test_multiqueue_watcher.py::test_background_strategy_replacement_failure_fails_request_and_retries_drive` | `BLE001=1`; `BLE001@785` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_background_strategy_replacement_failure_fails_request_and_retries_drive` | candidate: independent suppression/locality review |
| MR-142 | `tests/tasks/test_multiqueue_watcher.py::test_owner_fatal_exit_signals_every_queued_mutator` | `BLE001=1`; `BLE001@1469` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_owner_fatal_exit_signals_every_queued_mutator` | candidate: independent suppression/locality review |
| MR-143 | `tests/tasks/test_multiqueue_watcher.py::test_same_waiter_replacement_publication_failure_preserves_installed_owner` | `BLE001=1`; `BLE001@1541` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py -k test_same_waiter_replacement_publication_failure_preserves_installed_owner` | candidate: independent suppression/locality review |
| MR-144 | `tests/tasks/test_task_execution.py::test_base_task_foreign_wait_rejects_before_waiter_effects` | `BLE001=1`; `BLE001@480` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py -k test_base_task_foreign_wait_rejects_before_waiter_effects` | candidate: independent suppression/locality review |
| MR-145 | `tests/tasks/test_task_execution.py::test_base_task_process_once_rejects_a_second_drive_thread_before_policy` | `BLE001=1`; `BLE001@203` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py -k test_base_task_process_once_rejects_a_second_drive_thread_before_policy` | candidate: independent suppression/locality review |
| MR-146 | `tests/tasks/test_task_execution.py::test_base_task_rejects_reentrant_same_owner_turn` | `BLE001=1`; `BLE001@425` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py -k test_base_task_rejects_reentrant_same_owner_turn` | candidate: independent suppression/locality review |
| MR-147 | `tests/tasks/test_task_execution.py::test_base_task_stop_waits_for_starting_interlock` | `BLE001=1`; `BLE001@1078` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py -k test_base_task_stop_waits_for_starting_interlock` | candidate: independent suppression/locality review |
| MR-148 | `weft/_constants.py::<module>` | `SIM905=2`; `SIM905@1165; SIM905@1237` | The current split expression produces the same token sequence expected by its caller. | Apply the SIM905 simplification only after exact token/output proof. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py tests/system/test_constants_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: local simplification candidate |
| MR-149 | `weft/_constants.py::<module>` | `PYI064=15`; `PYI064@833; PYI064@1304; PYI064@1307; PYI064@1310; PYI064@1313; PYI064@1316; PYI064@1690; PYI064@1693; PYI064@1696; PYI064@1699; PYI064@1702; PYI064@1705; PYI064@1938; PYI064@1941; PYI064@1944` | Each Final constant has a single literal value and consumers rely on its inferred literal type. | Replace Final[Literal[value]] with bare Final; prove exact constants and full mypy. | `./.venv/bin/python -m pytest -q tests/system/test_constants.py tests/system/test_constants_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: mechanical typing candidate |
| MR-150 | `weft/builtins/__init__.py::_normalize_supported_platforms` | `TRY004=1`; `TRY004@173` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_builtin_contract.py tests/core/test_builtin_platform_support.py` | pending: exception-contract compatibility review |
| MR-151 | `weft/builtins/__init__.py::builtin_task_catalog` | `TRY004=2`; `TRY004@115; TRY004@119` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_builtin_contract.py tests/core/test_builtin_platform_support.py` | pending: exception-contract compatibility review |
| MR-152 | `weft/builtins/agent_images.py::_normalize_provider_list` | `TRY004=1`; `TRY004@164` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_builtin_agent_images.py` | pending: exception-contract compatibility review |
| MR-153 | `weft/builtins/agent_images.py::_parse_prepare_request` | `TRY004=3`; `TRY004@134; TRY004@137; TRY004@154` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_builtin_agent_images.py` | pending: exception-contract compatibility review |
| MR-154 | `weft/builtins/agent_images.py::prepare_agent_images_task` | `BLE001=1`; `BLE001@80` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_builtin_agent_images.py` | candidate: independent suppression/locality review |
| MR-155 | `weft/cli/validate_taskspec.py::_resolve_taskspec_source` | `BLE001=1`; `BLE001@116` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_validate.py` | candidate: independent suppression/locality review |
| MR-156 | `weft/commands/_dump_support.py::cmd_dump` | `BLE001=2`; `BLE001@85; BLE001@118` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_dump_load.py` | candidate: independent suppression/locality review |
| MR-157 | `weft/commands/_load_support.py::_execute_import` | `BLE001=1`; `BLE001@399` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_dump_load.py` | candidate: independent suppression/locality review |
| MR-158 | `weft/commands/_load_support.py::cmd_load` | `BLE001=3`; `BLE001@519; BLE001@535; BLE001@548` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_dump_load.py` | candidate: independent suppression/locality review |
| MR-159 | `weft/commands/_task_snapshot_reducer.py::<module>` | `PYI025=1`; `PYI025@14` | The annotation accepts the abstract set interface, including immutable and custom set-like values; runtime behavior is unaffected. | Alias `collections.abc.Set` as `AbstractSet` and retain the abstract contract; run mypy plus reducer tests. | `./.venv/bin/python -m pytest -q tests/commands/test_task_snapshot_reducer.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: mechanical typing candidate |
| MR-160 | `weft/commands/events.py::<module>` | `PLR0402=1`; `PLR0402@16` | The module facade keeps task-evidence lookups qualified and patchable as one cohesive dependency; callers/tests also import the events facade by module identity. | Keep the qualified facade unless a from-import candidate survives module-identity and monkeypatch tests; prefer an exact PLR0402 suppression if locality worsens. | `./.venv/bin/python -m pytest -q tests/commands/test_result.py tests/core/test_ops_shared.py` | pending: facade/monkeypatch identity review |
| MR-161 | `weft/commands/init.py::cmd_init` | `BLE001=1`; `BLE001@91` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_init.py` | candidate: independent suppression/locality review |
| MR-162 | `weft/commands/result.py::<module>` | `PLR0402=1`; `PLR0402@15` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_result.py tests/cli/test_cli_result.py` | pending: facade/monkeypatch identity review |
| MR-163 | `weft/commands/result.py::_collect_all_results` | `BLE001=1`; `BLE001@438` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_result.py tests/cli/test_cli_result.py` | candidate: independent suppression/locality review |
| MR-164 | `weft/commands/result.py::cmd_result` | `BLE001=1`; `BLE001@950` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_result.py tests/cli/test_cli_result.py` | candidate: independent suppression/locality review |
| MR-165 | `weft/commands/run.py::_execute_inline` | `BLE001=1`; `BLE001@1176` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_run.py tests/commands/test_interactive_client.py` | candidate: independent suppression/locality review |
| MR-166 | `weft/commands/run.py::_execute_spec_via_manager` | `BLE001=1`; `BLE001@1313` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_run.py tests/commands/test_interactive_client.py` | candidate: independent suppression/locality review |
| MR-167 | `weft/commands/run.py::_run_interactive_session` | `BLE001=2, S110=1`; `BLE001@636; BLE001@685; S110@685` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_run.py tests/commands/test_interactive_client.py` | candidate: independent suppression/locality review |
| MR-168 | `weft/commands/specs.py::_validate_task_spec_payload` | `BLE001=6`; `BLE001@523; BLE001@534; BLE001@548; BLE001@564; BLE001@579; BLE001@595` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_specs.py` | candidate: independent suppression/locality review |
| MR-169 | `weft/commands/submission.py::<module>` | `PLR0402=1`; `PLR0402@19` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_submission.py` | pending: facade/monkeypatch identity review |
| MR-170 | `weft/commands/submission.py::apply_submit_overrides` | `TRY004=1`; `TRY004@94` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_submission.py` | pending: exception-contract compatibility review |
| MR-171 | `weft/commands/submission.py::ensure_manager_after_submission` | `BLE001=1`; `BLE001@221` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_submission.py` | candidate: independent suppression/locality review |
| MR-172 | `weft/commands/system.py::<module>` | `PLR0402=1`; `PLR0402@22` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_status.py tests/cli/test_cli_system.py` | pending: facade/monkeypatch identity review |
| MR-173 | `weft/commands/system.py::_collect_task_snapshot_records` | `BLE001=1`; `BLE001@852` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_status.py tests/cli/test_cli_system.py` | candidate: independent suppression/locality review |
| MR-174 | `weft/commands/system.py::_watch_task_events` | `BLE001=1`; `BLE001@1586` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_status.py tests/cli/test_cli_system.py` | candidate: independent suppression/locality review |
| MR-175 | `weft/commands/system.py::cmd_status` | `BLE001=1`; `BLE001@1614` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_status.py tests/cli/test_cli_system.py` | candidate: independent suppression/locality review |
| MR-176 | `weft/commands/task_monitor.py::<module>` | `PLR0402=1`; `PLR0402@27` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_monitor.py` | pending: facade/monkeypatch identity review |
| MR-177 | `weft/commands/task_monitor.py::_load_checkpoint` | `TRY004=1`; `TRY004@225` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_monitor.py` | pending: exception-contract compatibility review |
| MR-178 | `weft/commands/tasks.py::<module>` | `PLR0402=1`; `PLR0402@23` | The module-qualified import currently preserves a facade/module object used for attribute lookup or monkeypatching. | Convert to from-import only after proving import identity and monkeypatch targets; otherwise register a PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_commands.py` | pending: facade/monkeypatch identity review |
| MR-179 | `weft/commands/tasks.py::_monitor_store_task_snapshot` | `BLE001=1`; `BLE001@546` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_commands.py` | candidate: independent suppression/locality review |
| MR-180 | `weft/core/agents/provider_cli/container_runtime.py::get_provider_container_runtime_descriptor` | `TRY004=1`; `TRY004@404` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_provider_cli_container_runtime.py` | pending: exception-contract compatibility review |
| MR-181 | `weft/core/agents/provider_cli/settings.py::_load_json_mapping` | `TRY004=1`; `TRY004@253` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_provider_cli_settings.py` | pending: exception-contract compatibility review |
| MR-182 | `weft/core/agents/provider_cli/settings.py::_provider_settings_mapping` | `TRY004=2`; `TRY004@218; TRY004@226` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_provider_cli_settings.py` | pending: exception-contract compatibility review |
| MR-183 | `weft/core/agents/provider_cli/settings.py::ensure_provider_cli_project_executable` | `TRY004=1`; `TRY004@103` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_provider_cli_settings.py` | pending: exception-contract compatibility review |
| MR-184 | `weft/core/agents/provider_cli/settings.py::load_provider_cli_project_settings` | `TRY004=1`; `TRY004@56` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_provider_cli_settings.py` | pending: exception-contract compatibility review |
| MR-185 | `weft/core/agents/runtime.py::start_agent_runtime_session` | `TRY004=1`; `TRY004@292` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_agent_runtime.py` | pending: exception-contract compatibility review |
| MR-186 | `weft/core/agents/validation.py::_require_mapping` | `TRY004=1`; `TRY004@105` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_agent_validation.py` | pending: exception-contract compatibility review |
| MR-187 | `weft/core/callable.py::make_callable` | `PLW1510=1`; `PLW1510@105` | The subprocess return code is currently non-fatal here and is either inspected by the caller or intentionally ignored by a probe/test. | Add explicit check=False. Use check=True only if a nonzero result is already proven fatal. | `./.venv/bin/python -m pytest -q tests/core/test_callable.py` | ready: mechanical candidate |
| MR-188 | `weft/core/environment_profiles.py::_mapping_of_strings` | `TRY004=1`; `TRY004@173` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_environment_profiles.py` | pending: exception-contract compatibility review |
| MR-189 | `weft/core/environment_profiles.py::_require_mapping` | `TRY004=1`; `TRY004@180` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_environment_profiles.py` | pending: exception-contract compatibility review |
| MR-190 | `weft/core/environment_profiles.py::_require_text` | `TRY004=1`; `TRY004@186` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_environment_profiles.py` | pending: exception-contract compatibility review |
| MR-191 | `weft/core/manager.py::Manager._atexit_unregister` | `BLE001=1, S110=1`; `BLE001@6914; S110@6914` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/specs/manager_architecture/test_spawn_retry.py` | candidate: independent suppression/locality review |
| MR-192 | `weft/core/manager.py::Manager._launch_child_task` | `BLE001=1`; `BLE001@1078` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/specs/manager_architecture/test_spawn_retry.py` | candidate: independent suppression/locality review |
| MR-193 | `weft/core/manager.py::Manager._retry_stale_child_launches` | `BLE001=1`; `BLE001@949` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/specs/manager_architecture/test_spawn_retry.py` | candidate: independent suppression/locality review |
| MR-194 | `weft/core/manager.py::Manager._run_child_launch_worker` | `BLE001=1`; `BLE001@1139` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/specs/manager_architecture/test_spawn_retry.py` | candidate: independent suppression/locality review |
| MR-195 | `weft/core/manager.py::Manager._unregister_atexit_callback` | `BLE001=1, S110=1`; `BLE001@6933; S110@6933` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/specs/manager_architecture/test_spawn_retry.py` | candidate: independent suppression/locality review |
| MR-196 | `weft/core/manager_runtime.py::_stop_manager` | `BLE001=1`; `BLE001@1884` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py` | candidate: independent suppression/locality review |
| MR-197 | `weft/core/manager_runtime.py::_terminate_manager_process` | `BLE001=2`; `BLE001@1704; BLE001@1711` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py` | candidate: independent suppression/locality review |
| MR-198 | `weft/core/monitor/external_log.py::_PathWriter._close_handler_locked` | `BLE001=2`; `BLE001@115; BLE001@119` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_external_log.py` | candidate: independent suppression/locality review |
| MR-199 | `weft/core/monitor/runtime.py::<module>` | `PLR0402=1`; `PLR0402@27` | The task-evidence facade supplies a cohesive namespace used for many functions and runtime annotation types; qualification makes ownership explicit and avoids a wide import surface. | Keep the qualified facade unless a from-import candidate is locality-positive and passes monitor/runtime tests; otherwise use an exact PLR0402 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/tasks/test_task_monitor.py` | pending: facade/locality review |
| MR-200 | `weft/core/monitor/store.py::MonitorStore.status` | `BLE001=1`; `BLE001@2387` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py` | candidate: independent suppression/locality review |
| MR-201 | `weft/core/monitor/task_monitor.py::TaskMonitor._close_worker_local_resources` | `BLE001=3`; `BLE001@1009; BLE001@1017; BLE001@1039` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-202 | `weft/core/monitor/task_monitor.py::TaskMonitor._emit_task_monitor_log` | `BLE001=1`; `BLE001@1304` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-203 | `weft/core/monitor/task_monitor.py::TaskMonitor._ensure_monitor_store` | `BLE001=1`; `BLE001@2292` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-204 | `weft/core/monitor/task_monitor.py::TaskMonitor._run_builtin_cycle_worker` | `BLE001=1`; `BLE001@5214` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-205 | `weft/core/monitor/task_monitor.py::TaskMonitor._run_custom_monitor_processor` | `BLE001=1`; `BLE001@5483` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-206 | `weft/core/monitor/task_monitor.py::TaskMonitor._run_terminal_control_cleanup_worker` | `BLE001=1`; `BLE001@3650` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | candidate: independent suppression/locality review |
| MR-207 | `weft/core/monitor/task_monitor.py::TaskMonitor._worker_local_monitor_clone` | `TRY004=2`; `TRY004@840; TRY004@849` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_task_monitoring.py tests/core/test_task_monitor_cleanup.py` | pending: exception-contract compatibility review |
| MR-208 | `weft/core/pipelines.py::_validate_stage_template` | `TRY004=1`; `TRY004@288` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_pipelines.py` | pending: exception-contract compatibility review |
| MR-209 | `weft/core/resource_monitor.py::<module>` | `BLE001=1`; `BLE001@24` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/specs/resource_management/test_monitor_compat.py tests/specs/resource_management/test_resource_metrics.py` | candidate: independent suppression/locality review |
| MR-210 | `weft/core/resource_monitor.py::PsutilResourceMonitor.__init__` | `RUF034=1`; `RUF034@178` | Both conditional branches currently assign None, so psutil availability does not change initial _process state. | Replace the redundant conditional with None and retain delayed start_monitoring initialization; run resource-monitor tests. | `./.venv/bin/python -m pytest -q tests/specs/resource_management/test_monitor_compat.py tests/specs/resource_management/test_resource_metrics.py` | ready: behavior-preserving defect fix |
| MR-211 | `weft/core/resource_monitor.py::PsutilResourceMonitor.check_limits` | `BLE001=1`; `BLE001@356` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/specs/resource_management/test_monitor_compat.py tests/specs/resource_management/test_resource_metrics.py` | candidate: independent suppression/locality review |
| MR-212 | `weft/core/runner_validation.py::_require_mapping` | `TRY004=1`; `TRY004@132` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_runner_plugins.py` | pending: exception-contract compatibility review |
| MR-213 | `weft/core/runner_validation.py::_require_text` | `TRY004=1`; `TRY004@138` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_runner_plugins.py` | pending: exception-contract compatibility review |
| MR-214 | `weft/core/runners/host.py::HostTaskRunner._close_mp_queue` | `BLE001=2, S110=2`; `BLE001@938; S110@938; BLE001@942; S110@942` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-215 | `weft/core/runners/host.py::HostTaskRunner._close_process_handle` | `BLE001=1, S110=1`; `BLE001@951; S110@951` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-216 | `weft/core/runners/host.py::HostTaskRunner._run_one_shot_terminal_handoff` | `BLE001=3`; `BLE001@507; BLE001@598; BLE001@763` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-217 | `weft/core/runners/host.py::HostTaskRunner._stop_process` | `BLE001=1, S110=1`; `BLE001@921; S110@921` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-218 | `weft/core/runners/host.py::HostTaskRunner.run_with_hooks` | `BLE001=2, S110=2`; `BLE001@452; S110@452; BLE001@457; S110@457` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-219 | `weft/core/runners/host.py::HostTaskRunner.start_agent_session` | `BLE001=1`; `BLE001@1095` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-220 | `weft/core/runners/host.py::HostTaskRunner.start_session` | `BLE001=1`; `BLE001@1040` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-221 | `weft/core/runners/host.py::_agent_session_worker_entry` | `BLE001=3, S110=1`; `BLE001@308; BLE001@333; BLE001@355; S110@355` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-222 | `weft/core/runners/host.py::_worker_entry` | `BLE001=1`; `BLE001@186` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/core/test_terminal_handoff.py` | candidate: independent suppression/locality review |
| MR-223 | `weft/core/runners/subprocess_runner.py::run_monitored_subprocess` | `BLE001=5, S110=2`; `BLE001@91; S110@91; BLE001@96; S110@96; BLE001@110; BLE001@238; BLE001@298` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_subprocess_runner.py` | candidate: independent suppression/locality review |
| MR-224 | `weft/core/serve_log.py::emit_serve_log_record` | `BLE001=2`; `BLE001@149; BLE001@155` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_serve.py tests/cli/test_cli_serve.py` | candidate: independent suppression/locality review |
| MR-225 | `weft/core/spawn_requests.py::_write_spawn_request_with_timestamp` | `TRY004=1`; `TRY004@122` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_spawn_requests.py` | pending: exception-contract compatibility review |
| MR-226 | `weft/core/spec_store.py::_read_json` | `TRY004=1`; `TRY004@72` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_spec_store.py` | pending: exception-contract compatibility review |
| MR-227 | `weft/core/state_machines.py::StateMachine._source_states` | `TRY004=1`; `TRY004@262` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_state_machines.py` | pending: exception-contract compatibility review |
| MR-228 | `weft/core/task_evidence.py::describe_runtime` | `BLE001=1`; `BLE001@772` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/commands/test_task_evidence.py` | candidate: independent suppression/locality review |
| MR-229 | `weft/core/tasks/base.py::BaseTask._control_runtime_summary` | `BLE001=1`; `BLE001@1770` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py tests/tasks/test_control_channel.py` | candidate: independent suppression/locality review |
| MR-230 | `weft/core/tasks/base.py::BaseTask._pong_extension_fields` | `BLE001=1`; `BLE001@1712` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py tests/tasks/test_control_channel.py` | candidate: independent suppression/locality review |
| MR-231 | `weft/core/tasks/base.py::BaseTask._submit_worker_lane` | `BLE001=1`; `BLE001@673` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_task_execution.py tests/tasks/test_control_channel.py` | candidate: independent suppression/locality review |
| MR-232 | `weft/core/tasks/consumer.py::Consumer._run_reactor_work_item` | `BLE001=1`; `BLE001@343` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_consumer_terminal_events.py` | candidate: independent suppression/locality review |
| MR-233 | `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._apply_pending_topology_mutations` | `BLE001=2`; `BLE001@662; BLE001@664` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_multiqueue_watcher.py` | candidate: independent suppression/locality review |
| MR-234 | `weft/core/tasks/pipeline.py::PipelineEdgeTask._handle_work_message` | `BLE001=1`; `BLE001@164` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_pipeline_runtime.py` | candidate: independent suppression/locality review |
| MR-235 | `weft/core/tasks/pipeline.py::PipelineTask._bootstrap_children` | `BLE001=1`; `BLE001@440` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_pipeline_runtime.py` | candidate: independent suppression/locality review |
| MR-236 | `weft/core/tasks/service.py::ServiceTask._run_service_worker_thread` | `BLE001=1`; `BLE001@562` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_service_task.py` | candidate: independent suppression/locality review |
| MR-237 | `weft/core/tasks/sessions.py::AgentSession._close_ipc_resources` | `BLE001=1, S110=1`; `BLE001@760; S110@760` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-238 | `weft/core/tasks/sessions.py::AgentSession._join_and_drain_ready_response` | `BLE001=1, S110=1`; `BLE001@367; S110@367` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-239 | `weft/core/tasks/sessions.py::AgentSession.close` | `BLE001=1, S110=1`; `BLE001@810; S110@810` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-240 | `weft/core/tasks/sessions.py::AgentSession.last_metrics` | `BLE001=1, S110=1`; `BLE001@791; S110@791` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-241 | `weft/core/tasks/sessions.py::AgentSession.poll_limits` | `BLE001=1`; `BLE001@770` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-242 | `weft/core/tasks/sessions.py::AgentSession.stop_monitor` | `BLE001=2, S110=2`; `BLE001@778; S110@778; BLE001@782; S110@782` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-243 | `weft/core/tasks/sessions.py::AgentSession.terminate` | `BLE001=4, S110=4`; `BLE001@700; S110@700; BLE001@721; S110@721; BLE001@728; S110@728; BLE001@738; S110@738` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-244 | `weft/core/tasks/sessions.py::AgentSession.wait_ready` | `BLE001=1, S110=1`; `BLE001@327; S110@327` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-245 | `weft/core/tasks/sessions.py::CommandSession.close` | `BLE001=1, S110=1`; `BLE001@185; S110@185` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-246 | `weft/core/tasks/sessions.py::CommandSession.poll_limits` | `BLE001=1`; `BLE001@195` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-247 | `weft/core/tasks/sessions.py::CommandSession.stop_monitor` | `BLE001=2, S110=2`; `BLE001@203; S110@203; BLE001@207; S110@207` | Failures from the enclosed callback, cleanup, observation, or test-helper seam are intentionally contained without replacing the primary outcome; current continuation is silent. | Preserve broad containment. Do not use contextlib.suppress merely to evade lint. Narrow only with an exhaustive raised-exception proof; otherwise register a BLE001/S110/S112 suppression candidate. | `./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/tasks/test_agent_execution.py` | candidate: independent suppression/locality review |
| MR-248 | `weft/core/taskspec/model.py::AgentSection.model_post_init` | `PYI063=1`; `PYI063@923` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-249 | `weft/core/taskspec/model.py::AgentTemplateSection.model_post_init` | `PYI063=1`; `PYI063@815` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-250 | `weft/core/taskspec/model.py::AgentToolSection.model_post_init` | `PYI063=1`; `PYI063@769` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-251 | `weft/core/taskspec/model.py::LimitsSection.model_post_init` | `PYI063=1`; `PYI063@338` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-252 | `weft/core/taskspec/model.py::ParameterizationSection.model_post_init` | `PYI063=1`; `PYI063@698` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-253 | `weft/core/taskspec/model.py::RunInputArgumentSection.model_post_init` | `PYI063=1`; `PYI063@458` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-254 | `weft/core/taskspec/model.py::RunInputSection.model_post_init` | `PYI063=1`; `PYI063@635` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-255 | `weft/core/taskspec/model.py::RunInputStdinSection.model_post_init` | `PYI063=1`; `PYI063@549` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-256 | `weft/core/taskspec/model.py::RunnerSection.model_post_init` | `PYI063=1`; `PYI063@406` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-257 | `weft/core/taskspec/model.py::SpecSection.model_post_init` | `PYI063=1`; `PYI063@1097` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-258 | `weft/core/taskspec/model.py::TaskSpec.model_post_init` | `PYI063=1`; `PYI063@1431` | Pydantic calls model_post_init with one positional context argument; the double-underscore name already prevents keyword use by callers. | Use explicit positional-only slash consistently across all overrides; prove Pydantic construction and mypy. | `./.venv/bin/python -m pytest -q tests/taskspec/test_taskspec.py tests/taskspec/test_taskspec_properties.py && ./.venv/bin/mypy weft --config-file pyproject.toml` | ready: typing-contract candidate |
| MR-259 | `weft/core/taskspec/parameterization.py::invoke_parameterization_adapter` | `TRY004=1`; `TRY004@84` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_spec_parameterization.py` | pending: exception-contract compatibility review |
| MR-260 | `weft/core/taskspec/parameterization.py::materialize_taskspec_template` | `TRY004=1`; `TRY004@120` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_spec_parameterization.py` | pending: exception-contract compatibility review |
| MR-261 | `weft/ext.py::RunnerHandle.__post_init__` | `TRY004=2`; `TRY004@59; TRY004@66` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_runner_plugins.py tests/core/test_runtime_handle_liveness.py` | pending: exception-contract compatibility review |
| MR-262 | `weft/ext.py::RunnerHandle.from_dict` | `TRY004=4`; `TRY004@151; TRY004@153; TRY004@155; TRY004@157` | Invalid shape/type is currently rejected as ValueError with the existing message; callers may observe both. | Keep ValueError/message unless direct caller and test evidence authorizes TypeError; absent that evidence, register a TRY004 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_runner_plugins.py tests/core/test_runtime_handle_liveness.py` | pending: exception-contract compatibility review |
| MR-263 | `weft/helpers/__init__.py::safe_cancel` | `BLE001=1`; `BLE001@87` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_helpers.py` | candidate: independent suppression/locality review |
| MR-264 | `weft/helpers/__init__.py::stdin_is_tty` | `BLE001=1`; `BLE001@77` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_helpers.py` | candidate: independent suppression/locality review |
| MR-265 | `weft/helpers/__init__.py::write_file_atomically` | `BLE001=1`; `BLE001@817` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/system/test_helpers.py` | candidate: independent suppression/locality review |
| MR-266 | `weft/manager_detached_launcher.py::_terminate_runtime` | `BLE001=2`; `BLE001@84; BLE001@91` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_manager.py` | candidate: independent suppression/locality review |
| MR-267 | `weft/manager_detached_launcher.py::main` | `BLE001=2`; `BLE001@110; BLE001@122` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/cli/test_cli_manager.py` | candidate: independent suppression/locality review |
| MR-268 | `weft/manager_process.py::main` | `BLE001=2`; `BLE001@59; BLE001@65` | The owner catches arbitrary boundary failure and follows its existing fallback/result/state path instead of allowing the failure to escape. | Audit the enclosed calls. Narrow only if the complete raised set and existing fallback are proven; otherwise register a BLE001 suppression candidate. | `./.venv/bin/python -m pytest -q tests/core/test_manager.py` | candidate: independent suppression/locality review |

## Blocked groups

None. `pending` and `candidate` rows are not accepted edits: they still require the row's named compatibility, locality, independent suppression, and owner-approval gates.

## Cardinality check

```text
required=390
BLE001=186
PLW1510=10
PYI025=1
PYI034=1
PYI063=11
PYI064=15
RUF009=1
RUF012=15
RUF034=1
S102=3
S110=46
S112=2
SIM115=1
SIM117=21
TRY004=75
TRY203=1
PLR0402=9
SIM905=2
rows=268
blocked=0
```

## 17. Refactor Rework Queue

FIFO. Preserve `NET NEGATIVE` candidates in dedicated worktrees. Activation
requires every row `accepted`.

| Queue ID | Baseline/worktree | Rules/symbol | Attempt | Verdict | Improvement criteria | State | Accepted checkpoint |
|---|---|---|---:|---|---|---|---|
| RW-RUF100-001 | Current uncommitted Task 4 worktree; initial candidate recorded by clean review | `RUF100` prose-preserving sites | 1 | `NET NEGATIVE` | Put the third SLF001 rationale beside the exact `_agent` access; remove obsolete D401 lint-history prose that interrupts the import block. | accepted | Attempt 2 `NET POSITIVE` after 168 focused tests and clean Ruff/suppression/diff gates; owner checkpoint pending. |
| RW-RUF012-002 | Baseline `29e9ae54`; `/tmp/weft-rw-ruf012-queue-monitor` | MR-078, `test_watch_queue_uses_queue_monitor::_FakeContext.config` | 1 | `NET NEGATIVE` | Model the real per-instance context contract; initialize a fresh config in `__init__` instead of declaring shared mutable `ClassVar` state. | accepted | Attempt 2 `NET POSITIVE`; named test and clean Ruff/suppression/diff gates pass. |

States: `candidate`, `queued`, `reworking`, `awaiting-review`,
`awaiting-owner-checkpoint`, `accepted`.

## 18. Proposed Suppression Disposition

Empty at plan review. Task 7 appends candidates before directives. Each needs
independent review and separate explicit owner approval.

| Group | Ledger IDs and exact candidate sites/snippets | Rules/cardinality | Protected invariant | Real proof | Rejected alternatives | Review | Owner approval |
|---|---|---|---|---|---|---|---|

## 19. Execution Evidence

| Slice | Baseline | Command/proof | Result | Reviewer | Disposition |
|---|---|---|---|---|---|
| Task 2 initial probe, invalidated | `d0254ce45a757a7351fa0e7ca76f389790a1800b`; Ruff 0.16.1 | Initial Section 6 `--isolated` normal/raw probe | Reported 674 normal/848 raw, but discarded `isort.known-first-party` and created 75 false `I001` findings. The 453-code set comparison remained valid. | Parent fresh-eyes implementation check | Superseded immediately by the corrected probe below; no source cleanup began from these counts. |
| Task 2 corrected baseline freeze | Same baseline and Ruff | Temporary real `pyproject.toml` with only `select` -> `extend-select`; normal/raw Ruff plus `--show-settings` exact-set comparison | Normal: 599 diagnostics, 146 files, 38 codes, 228 offered fixes. Raw: 773 diagnostics, 156 files, 41 codes. Effective: 453; current: 171; added: 282; removed: 0; exact set difference from SimpleBroker: 0/0. | Parent integration; manual-risk grouping pending | Authoritative activation baseline reproduced on 2026-08-06. |
| Task 2 manual-risk ledger | `d0254ce45a757a7351fa0e7ca76f389790a1800b`; corrected baseline above | AST attribution matching `bin/ruff_suppression_index.py`, direct source inspection, and closest-test mapping for every named finding; includes requested `PLR0402` facade and `SIM905` sites | 390 required findings plus 11 requested additions accounted for across 268 owner/category rows. The generated first pass flagged 9 blocked groups; direct follow-up established their current invariants and proof paths, leaving 0 hard-blocked rows. Candidate and pending rows remain edit-gated by their named compatibility, locality, suppression-review, and owner-approval requirements. | Clean Task 2 risk-ledger analysis | Ledger populated before production edits; cardinalities are machine-checkable in Section 16. |
| Task 3 policy RED | Current `lint.select`; uncommitted policy-test slice | `pytest -n 0 tests/specs/test_ruff_policy.py -q`; temporary candidate-policy Ruff on the changed test file | Exactly two intended RED failures and 39 passes: structural `extend-select` requirement and missing real `BLE001` sentinel. Candidate-policy Ruff clean. | Clean independent Python/pytest reviewer | PASS after adding passing controls before every policy, inventory, extensionless, per-file, and CI mutation. |
| Task 4 `FURB136` batch | One expression in `weft/shell/__init__.py::prepare_command` | Focused shell tests; candidate-policy rule check; suppression check; clean Python-expert locality review | 5 passed, 1 Windows skip; lint and suppression clean; algebraically equivalent for all list lengths. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `FURB167` batch | `bin/coalesce-check` regex flag alias | Complete checker output comparison; candidate-policy rule check; suppression check; clean Python-expert locality review | Output identical; `re.MULTILINE` is the same flag as `re.M` and states anchor behavior explicitly. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `PIE790` CLI owner | Docstring-bodied `weft.cli.app::main` | Full `tests/cli/test_cli.py`; candidate-policy rule check; suppression check; clean Python-expert locality review | 11 passed; signature, docstring, bytecode behavior, and Typer callback semantics preserved. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `PIE790` monitor owner | Docstring-bodied `weft.core.resource_monitor::ResourceMonitor` | Focused resource/runner tests; candidate-policy rule check; focused mypy; suppression check; clean Python-expert locality review | 11 passed; distinct class identity, inheritance, loader behavior, and public export preserved. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `PIE807` harness owner | Sixteen empty zero-argument test factories in `tests/test_harness_registration.py` | Full owning test file; candidate-policy rule check; suppression/diff checks; clean Python-expert locality review | 29 passed; every call remains zero-argument and returns a fresh empty container; no identity/name/signature dependency. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `PIE807` manager owner | One empty zero-argument manager-record factory in the leadership-drain test | Exact owning test; candidate-policy rule check; suppression check; clean Python-expert locality review | Named test passed; production calls the patched attribute with no arguments and receives a fresh dictionary. | Fresh clean Python expert | `NET POSITIVE`; accepted without rework. |
| Task 4 `RUF100` pure removals | 33 stale directives without explanatory prose | Full 453-rule candidate inventory; focused owning tests; current Ruff; suppression and diff checks | Candidate `RUF100` fell from 38 to 0; approved pointer count remains 155; no [TS-3.1] pointer removed. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 4 `RUF100` prose sites | Five stale directives whose rationale required separate disposition | 168 focused tests; current Ruff; suppression/diff checks; two clean Python-expert reviews | Attempt 1 `NET NEGATIVE`: one rationale was nonlocal and obsolete D401 history interrupted imports. Rework localized the SLF001 comment, removed obsolete history, and retained four useful comments. | Two different fresh clean Python experts | Attempt 2 `NET POSITIVE`; RW-RUF100-001 accepted, owner checkpoint pending. |
| Task 4 `RUF022` leaf inventories | Sixteen short/leaf `__all__` lists | 172 focused/architecture tests; current Ruff; suppression/diff checks; candidate inventory | Exact unique membership and identity preserved; candidate `RUF022` fell from 25 to the nine locality-sensitive grouped inventories. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 4 test-only `PLR0402` | Two `weft.runtime_liveness` test imports | 67 focused and architecture tests; current Ruff; suppression/diff checks; module-identity inspection | Exact `sys.modules` identity and monkeypatch visibility preserved; production import graph unchanged; candidate `PLR0402` fell 9→7. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `PYI063` | MR-248..MR-258, all Pydantic `model_post_init` overrides | RED runtime-signature test; 64 TaskSpec tests; focused mypy; targeted/current Ruff; suppression/diff checks | All eleven overrides now match Pydantic's `(self, context, /)` runtime contract; positional invocation and super calls preserved. | Fresh clean Python expert | `NET POSITIVE`; accepted, awaiting owner-directed checkpoint before the next behavior-relevant slice. |
| Task 5 `RUF009` | MR-099, fake runner-plugin capabilities default | Full owning test file; targeted/current Ruff; suppression/diff checks | 5 passed; distinct but equal frozen capability values replace incidental shared identity. | Fresh clean Python expert | `NET POSITIVE`; accepted, awaiting owner checkpoint. |
| Task 5 `PYI025` | MR-159, reducer abstract-set annotation | 24 reducer tests; focused mypy; targeted/current Ruff; suppression/diff checks | `AbstractSet` names the existing abstract set-like contract; resolved annotation and runtime behavior are unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted, awaiting owner checkpoint. |
| Task 5 `PYI034` | MR-110, `WeftTestHarness.__enter__` | 29 harness tests; full configured mypy; direct helper mypy with only unrelated assignment error disabled; targeted/current Ruff; suppression/diff checks | `Self` matches the existing return-self behavior and preserves subclass typing. | Fresh clean Python expert | `NET POSITIVE`; accepted, awaiting owner checkpoint. |
| Task 5 `PYI064` | MR-149, fifteen literal constants | 81 constants tests; full configured mypy; direct `assert_type` probes for all direct/alias forms; targeted/current Ruff; suppression/diff checks | Bare `Final` retains every exact inferred literal type, including aliased `DEFAULT_STATUS`, while removing redundant duplicated annotations. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Microsandbox controls | MR-040, `ControlRuntime` call-history lists | RED instance-isolation test; full owning test file; targeted/current Ruff; suppression/diff checks | 3 passed; stop/kill history is now owned by each injected fake runtime and cannot leak across instances/tests. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Microsandbox filesystem fake | MR-042, local `Fs` call-record lists | RED instance-isolation assertions; full owning test file; targeted/current Ruff; suppression/diff checks | 9 passed; mkdir/copy records are owned by each fake filesystem while the single sandbox path remains unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` queue-monitor context | MR-078, first local `_FakeContext.config` | Exact owner test; current/targeted Ruff; suppression/diff checks; two clean reviews | Attempt 1 `ClassVar` was `NET NEGATIVE` because sharing was incidental. Rework initializes per-instance config to match `WeftContext`. | Two different fresh clean Python experts | Attempt 2 `NET POSITIVE`; RW-RUF012-002 accepted. |
| Task 5 `RUF012` queue-close context | MR-077, second local `_FakeContext.config` | RED instance-isolation assertion; full owning test file; current/targeted Ruff; suppression/diff checks | 19 passed; config now matches real per-context ownership while queue and generator-close behavior remain unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` manager terminal-queue registry | MR-089, first local `FakeTerminalQueue.instances` | Exact owner test; current/targeted Ruff; suppression/diff checks | Shared class registry intentionally captures both proof-read and write queue instances within one test-local class. | Fresh clean Python expert | `NET POSITIVE`; `ClassVar` accepted. |
| Task 5 `RUF012` manager terminal-proof registry | MR-090, second local `FakeTerminalQueue.instances` | Exact owner test; current/targeted Ruff; suppression/diff checks | Shared class registry intentionally captures aggregate queue construction history and is recreated per test invocation. | Fresh clean Python expert | `NET POSITIVE`; `ClassVar` accepted. |
| Task 5 `RUF012` realtime materialization context | MR-098, first shared-ops `_FakeContext.config` | RED instance-isolation assertion; exact owner test; current/targeted Ruff; suppression/diff checks | Context config now matches real per-instance ownership while terminal-materialization event behavior is unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` realtime snapshot context | MR-097, second shared-ops `_FakeContext.config` | RED instance-isolation assertion; full owning test file; current/targeted Ruff; suppression/diff checks | 10 passed; context config now matches real per-instance ownership while snapshot-derived terminal event order remains unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Docker ID-fallback container | MR-011, first local `FakeContainer.attrs` | Exact owner test; current/targeted Ruff; suppression/diff checks | Mutable Docker-model attributes are now instance-owned; fixed immutable container ID and fallback lookup behavior remain unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Docker list-fallback container | MR-012, second local `FakeContainer.attrs` | Exact owner test; current/targeted Ruff; suppression/diff checks | Mutable Docker-model attributes are now instance-owned; fixed immutable ID/name and list fallback behavior remain unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Docker startup-wait container | MR-010, mutable `FakeContainer.attrs` | RED instance-isolation assertion; exact owner test; current/targeted Ruff; suppression/diff checks | Exercised container alone transitions `created`→`running`; the extra isolation probe does not consume transition state or affect callback timing. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Docker stuck-container timeout | MR-013, mutable `FakeContainer.attrs` | RED instance-isolation assertion; exact owner test; current/targeted Ruff; suppression/diff checks | Proven instance exercises the unchanged reload and fake-clock timeout path; container state no longer leaks across instances. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 5 `RUF012` Docker startup-failure cleanup | MR-009, final mutable `FakeContainer.attrs` | RED instance-isolation assertion; full owning test file; current/targeted Ruff; suppression/diff checks | 26 passed; runner and cleanup still use the proven instance, with process kill/wait and container removal behavior unchanged. | Fresh clean Python expert | `NET POSITIVE`; accepted; repository candidate `RUF012=0`. |
| Task 6 `RUF019` Django TaskSpec name override | `integrations/weft_django/weft_django/client.py::_apply_taskspec_payload_overrides` | Full Django integration test file; targeted/current Ruff; suppression/diff checks | 30 passed; the truthy override value is fetched once, bound at the check, and used immediately. Missing and falsey names retain the existing behavior. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 6 `RUF034` resource-monitor initialization | MR-210, `PsutilResourceMonitor.__init__` | Both resource-monitor test modules; targeted/current Ruff; suppression/diff checks | 7 passed; `_process` is unconditionally unset at construction, while availability validation and process creation remain delayed in `start_monitoring`. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 6 `RUF046` redundant integer casts | Consumer and interactive CPU metrics; relative-duration fallback | Four owning test modules; focused mypy; targeted/current Ruff; suppression/diff checks | `round(value)` without `ndigits` already returns `int`; values, rounding behavior, exceptional inputs, and downstream types remain unchanged at all three sites. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 6 `RET501`/`PLR1711` terminal `None` returns | Seven protocol, callback, and test-double methods | Four owning test files; focused production mypy; targeted/current Ruff; suppression/diff checks | 136 passed; falling off each `-> None` or callback body returns the same singleton after the same observable work. Empty no-op methods retain their required body statement. | Fresh clean Python expert | `NET POSITIVE`; accepted. |
| Task 6 `SIM103` PG launcher predicate | `tests/conftest.py::_pg_cli_requires_active_env` | Direct five-case command matrix; targeted/current Ruff; pytest collection; suppression/diff checks | The empty guard and one-time string conversion remain; membership names exactly the prior `manager`/`run` command set beside its rationale. | Fresh clean Python expert | `NET POSITIVE`; accepted. |

## 20. Independent Review Loop

Plan prompt:

> Read this plan, exact [TS-3] delta, and atomic strategy at its baseline.
> Read current Ruff policy/tests, completed C901 and SimpleBroker reference
> plans, representative high-risk findings, and named lessons. Look for errors,
> bad ideas, ambiguities, or ceremony without risk reduction. Could a
> zero-context engineer account for all findings, preserve exception/resource/
> subprocess/typing contracts, govern suppressions, and activate 453 codes
> atomically? Answer PASS or BLOCKED based on confident implementation and no
> degradation of robustness.

Completion prompt:

> Review promoted [TS-3], plan, config, fixture/tests, suppression state/tool,
> complete cleanup diff, ledgers, and current evidence. Focus on regressions,
> containment, resource order, subprocess semantics, typing, output, logical
> locality, comprehensibility, missing tests, and policy drift. Answer PASS or
> BLOCKED and tie blockers to implementability or degradation.

Reproduce each finding. Update accepted points, record declined rationale, and
request scoped re-review. Material invariant/ownership/authority/blast-radius
changes re-enter full review.

## 21. Revision Log

| Date | Reviewed baseline | Revision | Reason | Re-review |
|---|---|---|---|---|
| 2026-08-05 | Initial draft against `d0254ce45a757a7351fa0e7ca76f389790a1800b` | Added the exact tracked-file isolated reproduction probe and required set comparison, rather than leaving Task 2 to reconstruct the authoring command from prose. | Fresh-eyes review found that flags and counts were reproducible in principle but not executable by a zero-context implementer without rebuilding file discovery. | Author reran an equivalent probe: 453 effective codes; no set difference from SimpleBroker; 674 normal and 848 raw diagnostics. |
| 2026-08-05 | Independent review of the first complete draft | Moved all approved suppression edits and reconciliation into Task 8's expanded-config candidate state; retained the full [TS-3] firing-gate sentence and added the complete mutation matrix; bound suppression approval to exact ledger IDs/sites/snippets; defined review units as one owner/invariant group or one mechanical rule/rewrite shape. | Round 1 found three implementation blockers and one review-scope ambiguity. | Scoped round 2 PASS on all four accepted findings with no new defect. |
| 2026-08-06 | Implementation preflight against the reviewed draft | Replaced the `--isolated` diagnostic probe with a temporary full project config that changes only `select` to `extend-select`; corrected normal/raw counts from 674/848 to 599/773 and removed 75 false `I001` findings from the worklist. | `--isolated` also discarded non-selection Ruff settings, specifically `isort.known-first-party`, so the original diagnostic baseline did not model real activation. | Exact effective set remains 453 with zero SimpleBroker difference; corrected counts, fix totals, current/added/removed inventory, and all remaining per-rule counts reproduce. Scoped plan re-review PASS. |

## 22. Review Log

| Review | Date | Verdict | Disposition |
|---|---|---|---|
| Author fresh-eyes review | 2026-08-05 | PASS at plan-authoring time, later baseline flaw found | Added the then-exact isolated probe and confirmed its 39 rows summed to 674; confirmed Task 8 preserves Weft's six-file Python discovery instead of copying SimpleBroker's broad `bin/*`; metadata/spec-hygiene and DOM-15 gates passed. The 2026-08-06 implementation preflight later invalidated only the diagnostic probe/count claim, not the reviewed activation or safety design. |
| Clean independent plan review, round 1 | 2026-08-05 | BLOCKED | Accepted F1: the old 171-rule config cannot reconcile new-rule suppressions before activation. Accepted F2: the first [TS-3] delta weakened the existing every-setting-fires contract. Accepted F3: owner approval lacked exact candidate sites. Accepted F4: review units were underspecified. |
| Scoped independent plan re-review | 2026-08-05 | PASS | Confirmed coherent Task 8 suppression activation, complete setting mutation/firing proof, one-to-one site approval, and bounded owner/invariant or mechanical-batch review units. No new defect. |
| Corrected-baseline scoped review | 2026-08-06 | BLOCKED | Corrected 599/773 counts and the removal of false `I001` findings reproduced, but the probe did not prove the adjacent current/added/removed inventory or offered-fix claims. Updated it to compare the 171-code Weft fixture, fail on removed rules, and print offered-fix counts; scoped re-review pending. |
| Corrected-baseline scoped re-review | 2026-08-06 | PASS | Reproduced effective/current/added/removed inventory, normal/raw diagnostic/file/rule/fix counts, and exact SimpleBroker parity. No remaining defect. |
| Task 3 clean policy-test review | 2026-08-06 | PASS | Initial review blocked mutation tests that lacked passing controls. After correction, each mutation proves its unmutated candidate first; candidate-policy Ruff passes and only the two intended pre-activation RED tests fail. |

## 23. Fresh-Eyes Checklist

- [ ] 453-code target exactly matches SimpleBroker and removes no Weft rule.
- [ ] Discovery includes tracked Python/shebang tools and excludes Bash.
- [ ] Every manual-risk finding has owner, invariant, disposition, proof, and
  review state before edits.
- [ ] Mechanical batches are narrow and independently reviewed.
- [ ] Each behavior refactor gets fresh Python-expert locality review.
- [ ] Negative candidates are preserved/reworked; none unresolved at activation.
- [ ] Behavior, containment, resource/subprocess order, typing, and output stay
  fixed unless directly proven and separately scoped.
- [ ] No global/per-file ignore, blanket directive, threshold raise, or second
  suppression system.
- [ ] New suppressions, if any, are separately reviewed and owner-approved.
- [ ] Spec, config, fixture, tests, docs, and suppression state activate atomically.
- [ ] Formatter ownership and CI ordering stay unchanged.
- [ ] Full verification and independent completion review pass.
