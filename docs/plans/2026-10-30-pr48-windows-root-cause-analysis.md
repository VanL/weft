# PR #48 Windows Root-Cause Analysis

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-4]; docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-3.2]; docs/specifications/06-Resource_Management.md [RM-5.2]
Superseded by: none

Class: 4. Native-process investigation crosses test-environment and process-lifetime boundaries. This is an investigation under existing contracts, with no proposed product or normative-spec change.

## Goal and stop on corrective churn

Locate the causal flaw or false premise behind the original Windows failures before making another corrective change. A passing run is not a diagnosis. The owner requested a structured investigation on 2026-10-30 after repeated CI failures and test/diagnostic revisions. Runtime changes, test-premise changes, and further acceptance reruns stop during this investigation. Concurrent launch workers remain deferred.

This plan governs the investigation slice of [the active reactor repair plan](./2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md). That plan remains open. Its completed repairs and measurements remain evidence; they do not clear the failures below. Do not close either plan merely because a subsequent run passes.

## Model, sources, and expected behavior

Read `docs/program-theory.md` [WT-0], [WT-2], [WT-7], [ALT-WT-017], [ALT-WT-021]; `docs/agent-context/engineering-principles.md` sections 4.1 and 4.3; the source specs above; and the review/hardening runbooks. The session's external [diagnosing-bugs skill](/Users/van/.agents/skills/diagnosing-bugs/SKILL.md) supplies the feedback-loop discipline restated below; it is not a repository file. Use `skills/call-agent/SKILL.md` for cross-family review.

The model predicts one task-owned arbiter: eligible work, local completion, queue hints, and explicit deadlines must lead to progress. Durable queue facts determine custody and lineage. OS process handles establish physical lifetime, not logical ownership. SimpleBroker owns connection/retry/queue primitives. Weft must not invent a second scheduler, polling fallback, guessed admission charge, or broader timeout to accommodate an unexplained failure.

For each failure, identify the violated prediction and the actual owner. A queued token is not automatically eligible work. A process being alive does not prove a responsive Manager. A later result does not prove delivery before the failed deadline. A single blocked stack does not establish lock duration or lock ownership. Labels such as contention, pre-existing, infrastructure, or flaky do not supply a cause.

No spec delta is proposed. If a repair requires changing a winning contract or refused alternative, stop and record RECONSIDERATION REQUESTED before implementation.

## Frozen subjects and evidence inventory

Baseline product: `540707a370d32cf9475832099fabd52fa1dc7f0a` (pre-reactor).
Candidate product and common tests: `f4fb77892efe76f9225ff4ad83040801b783cf04`.
These names describe whole product versions, not an attribution to the reactor alone. Their runtime delta includes Host and Docker cleanup as well as subprocess supervision. Manager source is unchanged between them.

Original, uninstrumented candidate failures:

| Signature | Run / job | Observed boundary and missing evidence |
| --- | --- | --- |
| Windows 3.12.10 sequential pipeline | 41120227615 / 124285514469 | CLI timeout at its existing 120 s limit. Manager, Pipeline, input edge, and first stage made progress. One later Manager snapshot was in SimpleBroker `begin_immediate` during checkpoint publication. No interval trace proves a persistent lock or its holder. |
| Windows 3.14.10 parallel Manager reuse | 41120214739 / 124285487290 | Submission returned TIDs but reported degraded Manager readiness; initialized/created events existed without a stable canonical registry/proof. Launcher return 11 denotes abort/parent EOF, not proof of child crash. Child stack and native exit cause are absent. |
| Windows 3.14.10 child launch | 41120214739 / 124285487372 | `test_manager_spawns_child` did not observe its matching event within the existing 8 s wait. A child existed later during teardown. Launch-worker state at the deadline is missing. Keep this separate from CLI constructor failures. |
| Windows 3.13.17 CLI progress | 41120214739 / 124285488052 | Function kwargs, `json:dumps`, and environment-command calls exceeded existing 120 s subprocess communication limits; the agent result waiter expired at its existing 60 s completion wait after successful submission. Retained states differ: some stop before registry publication, one has a registry and queued request, and one has later work completion. Do not pool these into one causal claim. |

The failing `test_cli_run.py`, `test_manager.py`, conftest, and harness source do not differ between these product versions. The installed-console test and `run_diagnostics.py` did change. Freeze the candidate's complete test tree in both comparison arms, rather than silently comparing different oracles.

The previous successful baseline run is not a controlled old/new-product comparison. Run 40902305487 used Python 3.12.10/3.13.16/3.14.9, whereas the failed CLI jobs used 3.12.10/3.13.17/3.14.10. Both used pytest 9.2.0 and SimpleBroker 8.7.2. Their install records also differ in Pydantic (2.14.1 versus 2.14.2), pydantic-core (2.50.1 versus 2.50.2), Hypothesis and two typing-stub packages. These are observed confounds, not established causes. The owner considers Python patch-release causation unlikely; keep the interpreter fixed as a cheap control, rather than making a Python-version bisect a leading investigation.

Exact raw-log/install-record paths and hashes are indexed by `/tmp/weft-implementation-20261027/rca-original-evidence-manifest.json` (SHA256 `ce2b6e8d44cb8917717ca990b2a0e92c4932f34ec43d7e9082c5d9847af906da`) and `rca-baseline-evidence-manifest.json` in the same directory (SHA256 `1a654b0422cd612188a98fa8428bf520b66abcc9eb5c2c2c754464e814bb86f7`). Preserve these inputs with the temporary comparison machinery/artifacts; do not re-resolve package versions when reconstructing them. The original CLI 3.13.17 is mechanically verified in job 124285488052; the separate core measurement job reporting 3.13.16 is not contradictory because setup-python resolved versions per job.

The pipeline trace times out while joining Windows' stdout reader thread. Python's [3.12.10 implementation](https://github.com/python/cpython/blob/v3.12.10/Lib/subprocess.py) waits for pipe-reader completion separately from process exit. The old evidence therefore does not distinguish a still-running CLI from an exited CLI whose output pipe remains open. That distinction is an explicit missing observation under H4, not proof that a descendant retained the pipe. The exact original nodes and source comparisons are retained in the investigator's evidence table; identical individual tests do not establish identical broader-suite scheduling.

Narrow this ambiguity using the existing evidence: [3.13.17 `subprocess.run`](https://github.com/python/cpython/blob/v3.13.17/Lib/subprocess.py), like 3.12.10, kills its direct child after the Windows communication timeout and calls `communicate()` again without a timeout before re-raising. The three 3.13 timeout cases' call durations were 120.05, 120.07 and 124.17 s. A descendant keeping the pipe open indefinitely after that kill is inconsistent with those prompt exception returns. A bounded hold is still possible. The pipeline call lasted 163.09 s; its call includes root preparation and post-timeout harness/broker diagnostics, so the extra time is not an EOF-delay measurement. No recorded duration proves the CLI's native state immediately before kill. H4 must distinguish a persistent post-kill pipe holder, weakened for the 3.13 cases, from other ownership/resource failures.

Source tracing further limits H4: `json:dumps` and `json_kwargs` execute through Host's unchanged multiprocessing function worker and one-shot terminal handoff, not through the new command adapter. Changed imports and earlier/concurrent command work still permit indirect effects; neither resource accumulation nor such an effect has been demonstrated.

Exact original node IDs, for signature matching rather than reduced-load acceptance:

```text
tests/cli/test_cli_pipeline.py::test_pipeline_run_sequential
tests/cli/test_cli_run.py::test_cli_run_spec_builtin_keyword_arguments_payload_shapes_work_item
tests/cli/test_cli_run.py::test_cli_run_fast_standard_library_function
tests/cli/test_cli_run.py::test_cli_run_command_with_env
tests/cli/test_cli_run.py::test_cli_run_agent_spec_no_wait_and_result
tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse
tests/core/test_manager.py::test_manager_spawns_child
```

Separate observer experiment: run 41128466407 at diagnostic commit `75c8cff8f017a21e187c98827fd19aef832432f8` passed the Windows 3.12 watchdog-OFF arm (293 passed, 10 skipped) and failed one Manager-reuse case in the ON arm (292 passed, 10 skipped). The failed winner completed construction, then its first periodic dump ended mid-frame. Both Application-event exports sampled two events with no Python matches. This supports observer risk, not a native-crash diagnosis; separate VMs and one pair do not isolate causation. The added timer was absent from the original failures and cannot explain them.

CPython's [3.12.10 watchdog](https://github.com/python/cpython/blob/v3.12.10/Modules/faulthandler.c) calls the stack walker without the GIL; [issue 116008](https://github.com/python/cpython/issues/116008) and [watchdog reproducer 131580](https://github.com/python/cpython/issues/131580) establish the risk. Do not transfer that periodic timer, startup wrapper, or phase-marker code to the main branch.

Raw evidence lives under `/tmp/weft-implementation-20261027/`; the parent repair plan records artifact/run identities. Preserve original failed/cancelled runs and skipped dependent jobs as such.

## Six hypotheses and discriminating evidence

These are working hypotheses, not findings or a claim of one shared cause. Investigate the hypothesis that matches a reproduced signature; do not force every signature into it.

| ID | Hypothesis | Support required | Evidence against it for that signature |
| --- | --- | --- | --- |
| H1 | Broker lock ownership or retry behavior blocks progress. | Repeated observations of the blocked call, plus a transaction/lock holder and its lifetime that explain why progress cannot occur. | Broker operations complete during the failed interval and the actual block is elsewhere. A lone SQLite frame is insufficient support. |
| H2 | A reactor wake or eligible-work scheduling transition is missed. | Prove eligibility from durable facts and current capacity, then show the owner sleeping without a needed event/deadline. A correctly delivered missing event restores progress. | Wake delivery and owner processing occur, or the token is ineligible, or the owner is blocked inside another call. |
| H3 | Election and readiness disagree about the canonical Manager. | A complete registry/proof/launcher sequence shows inconsistent ownership, both contenders yielding, or abort of a viable Manager because readiness was evaluated incorrectly. | One canonical Manager and matching proof remain valid throughout, and the actual stall occurs after readiness. |
| H4 | Process/pipe ownership in the new implementation leaks or blocks. | Identify an owned process, handle, endpoint, or operation surviving its required release boundary; measure accumulation if claiming a suite-wide effect. Version association alone is not a mechanism. | The direct path has not run and there is no prior resource accumulation. The same old-version signature refutes a claim that new code is necessary, but not every possible contribution. |
| H5 | A specific Windows initialization/resource operation consumes the startup interval. | Phase and OS evidence locates an import, file access, process-creation operation, or scheduler delay and accounts for the failed deadline. | Startup is prompt in the failing case and the stall is later. Normal aggregate counters alone do not exclude a short stall. |
| H6 | Test/context/observation behavior reports failure despite timely correct behavior. | Independent process, queue, target identity, and timestamps prove the required behavior before the original deadline while the oracle misses it. Include installed test/dependency behavior in this inquiry. | Independent evidence confirms the required behavior was absent through the deadline. Eventual success is not evidence for a false oracle. |

## Investigation sequence and gates

### 1. Establish the feedback loop and controls before execution

The only original red-capable loop presently demonstrated is the real full Windows CI slice. It is slow and intermittent. The tight, repeatable feedback-loop gate is NOT satisfied. The initial comparison below is a bounded attempt to build that loop, not permission to fix a hypothesized cause. No lower-load acceptance is authorized.

Owner: top-level investigator. Before any dispatch, independently verify the inventory, dependency reconstruction, source/test manifests, exact argv, and review this plan plus the actual temporary workflow/runner. Record fresh-eyes and cross-family findings with dispositions.

Use existing `.github/workflows/test.yml` as the command/worker/action source, `bin/pytest-worker-count`, the candidate test tree and its existing diagnostics, and the archive/provenance approach in `tests/subprocess_reactor_benchmark.py`. Do not create a reusable diagnostic framework. Any new temporary runner is investigation-only, under the isolated diagnostic branch; mark its path and delta in the execution record before review. The main product and tests stay frozen.

**Dependency control:** CI uses `uv pip install --system -e ".[dev,docker,django,macos-sandbox,microsandbox]"`, not `uv sync --frozen`. Failed logs show pytest 9.2.0, whereas `uv.lock` pins 9.1.1. Identical lockfiles do not prove identical installed environments. Reconstruct the per-Python installed versions from the actual failed-run install records (including pytest 9.2.0, SimpleBroker 8.7.2, xdist 3.8.0, timeout 2.4.0, psutil 7.2.2). Preserve the raw records and their digest. Unrecorded pre-existing global packages remain an explicit historical-coverage limit.

Use the setup-python SYSTEM interpreter, as official CI does. Do not create per-arm virtual environments: Windows' [3.12 venv redirector](https://github.com/python/cpython/blob/v3.12.10/PC/launcher.c) and [3.13 redirector](https://github.com/python/cpython/blob/v3.13.17/PC/venvlauncher.c) create another process and forward inherited standard handles. That would change the very topology H3-H5 investigate. Prepare one common third-party wheel set per Python version and install its pinned versions once into that system environment. Prefer wheels; any unavoidable build must have its toolchain, inputs and output hashes recorded before review. Swap only the matching first-party editable packages between arms, using `--system --no-deps`, after the cleanup guard. First-party extensions belong to the product arm. Record installed distributions and third-party artifact hashes before each arm and require equality, excluding the explicitly versioned first-party source roots. If reconstruction cannot be performed, STOP and report the specific gap; do not silently substitute the lockfile or current resolver output.

Force-reinstall explicit editable paths for the four Windows first-party distributions: the root `weft`, `extensions/weft_docker`, `integrations/weft_django`, and `extensions/weft_macos_sandbox`. Do not rely on same-version editable-cache detection. Windows' existing platform marker excludes Microsandbox; do not introduce it. Preserve isolated build installation as in official CI, with one fixed build-constraint/artifact manifest shared by both arms (including the selected hatchling version and its transitive build requirements). `--no-deps` alone does not freeze build dependencies. Historical isolated-build versions were not recorded, so state that limit; do not silently change to non-isolated builds or add build tools to the subject runtime environment. Verify `direct_url.json` and installed entry-point targets after each swap.

Check that the installed common set satisfies each arm's installed first-party requirements with `uv pip check` against the system interpreter, without resolving or changing packages. Stop on a mismatch.

Archive whole product revisions into distinct investigator-owned scratch roots on the original checkout's D: volume at comparable, equal path depth and path length. Verify the workspace volume; stop if the current image differs, rather than silently changing the location. Record paths and runner image; do not silently relocate subjects to C: runner.temp. Overlay the same complete f4 `tests/` tree into both. The oracle also includes pytest configuration and external test inputs: mechanically inventory files read by `tests/cli` and its helpers, require the `[tool.pytest.ini_options]` section and those input assets to match, or explicitly take non-product fixtures from f4. Current `pyproject.toml` and `examples/` are identical between revisions; the changed `bin/pytest-pg` is not invoked by this SQLite CLI slice. Do not overwrite a product implementation and call it an oracle fixture. An unclassified difference is a STOP.

Before each arm verify test file sets/hashes, collected CLI node IDs, configuration, system Python/console paths, and installed manifests. Run a separate preflight process with that system interpreter and `run_cli`'s environment construction; import Weft and relevant extensions and verify `__file__`, normalized git blobs and plugin entry-point targets against the selected arm. Reuse the existing module-provenance calculation where applicable. This observes the preflight, not failing subject processes: record the explicit assumption that CLI/Manager/children inherit the same installation and environment. Do not claim actual subject-module observation without a separately reviewed hook.

Cache protocol: each archived product/test tree begins without bytecode or pytest caches; remove only caches created there by provenance/collection preflights before its test arm. Do not flush OS caches or silently change third-party files. Preflights warm shared dependencies even before arm one; neither arm is claimed to reproduce the historical cache state. Shared dependency/filesystem caches can become warmer in the second arm; the fixed order and this residual confound are recorded. A collection/import/provenance mismatch invalidates the comparison, rather than authorizing a compatibility patch during it.

### 2. One prespecified comparison, not retry-until-green

Run one paired full CLI comparison for each original Python patch version: 3.12.10, 3.13.17, and 3.14.10, with `windows-latest`. Each pair executes sequentially on one VM. Predeclare order: baseline then candidate for 3.12/3.14; candidate then baseline for 3.13. This limits all-baseline-first bias; it does not remove order/cache effects or provide a failure-rate estimate.

Use the original full `tests/cli` workload, SQLite, five workers verified from the actual worker configuration/start report, existing test/module/subprocess deadlines, heartbeat 120, and unchanged 50-minute job ceiling. Invoke the SYSTEM `pytest` console entry point on the original system PATH, matching official CI; do not substitute `python -m pytest` and change path/bootstrap behavior. Per-arm argv:

```text
pytest -v --tb=short -m "not slow" tests/cli --override-ini="addopts=-ra -q --strict-markers -n logical --dist load" --durations=100 --durations-min=0.5 -o faulthandler_timeout=720 --timeout=900 --timeout-method=thread --full-trace --junitxml=<arm-specific-path>
```

JUnit is added solely to retain execution identities/results. Preserve the existing pytest diagnostic settings identically in both arms; record whether the existing 720 s worker dump actually fires. Do not add a timer or observer inside a subject process. Existing f4 diagnostics and logs are the only first-batch observation machinery. Do not add profiler capture merely because it is available.

Construct each arm's environment afresh from the job's setup-python environment, not the preceding test process. Required: `WEFT_TEST_PROGRESS_HEARTBEAT_SECONDS=120`, `PYTEST_XDIST_AUTO_NUM_WORKERS` from the unchanged `bin/pytest-worker-count`, and a distinct `WEFT_TEST_DIAGNOSTICS_DIR` for that arm. Before pytest, remove stale `WEFT_TEST_PYTHON`, `PYTHONPATH`, `__PYVENV_LAUNCHER__`, `VIRTUAL_ENV`, and prior `WEFT_*`/`BROKER_*` overrides except the required keys listed above; the unchanged conftest must choose the system interpreter itself. Preserve normal system variables such as TEMP and PATH. Record the controlled variables, cleared-key presence, interpreter prefixes, console paths and actual workers, rather than dumping unrelated environment contents. Backend remains the original default SQLite; do not introduce a PostgreSQL target.

Capture pre/post-arm identities and creation times of newly created Python/Weft processes. After pytest exits and its existing cleanup finishes, allow at most 5 s of runner-only settlement using `psutil.wait_procs` on newly observed process objects. This adds no test grace and cannot change a verdict. Preserve PID/create-time identity; a process-tree relationship alone does not prove logical Weft ownership. Identify known task-owned processes from retained harness/queue evidence; label other new Python survivors unexplained, rather than declaring them product leaks. If survivors remain, do not broadly kill processes or run the second arm in that contaminated VM. Mark the COMPARISON invalid, preserve the completed arm as valid single-arm reproduction evidence, and stop that pair. First-arm test failure alone does not skip the second arm if cleanup/provenance are sound. Retain both arms' logs, JUnit, diagnostics, manifests, argv, timings, order, runner image/CPU details, and cleanup outcome. Missing artifacts make evidence incomplete, not a pass.

The 50-minute ceiling is a shared experiment bound, not a claim of 50 minutes available to each arm. Upload each completed arm's JUnit, diagnostics, manifests and available logs immediately after its guard, before setting up the next arm. Do not defer arm-one artifact upload until the end of the job. If the ceiling or cancellation cuts off EITHER arm, that arm and the comparison are censored/incomplete, not a product failure or a comparative verdict. Any original-symptom match already streamed from that arm remains partial single-arm evidence only. Final `always()` upload is best effort and is not guaranteed after runner termination. No job, subprocess, test or module deadline is increased to avoid this limitation.

Full core tests cannot simply use new f4 subprocess tests against the old product: those tests use new APIs. The separate 8 s child-launch failure remains open and is not cleared by this CLI comparison. Do not change its oracle or omit it from the final acceptance matrix.

### 3. Interpret, reproduce, and minimize one matching signature

Before dispatch, use these fixed symptom-match rules; no matching by node ID alone and no retrospective criteria:

| Original node/category | Required exception/boundary | Required retained-state category |
| --- | --- | --- |
| Sequential pipeline | `TimeoutExpired` from the 120 s CLI communication wait | Manager/Pipeline and first-stage progress, followed by absent required downstream progress; retain reader-wait boundary if available. A sampled SQLite frame is not required and is not a causal signature. |
| Function kwargs and environment-command nodes | `TimeoutExpired` at the 120 s CLI communication boundary | Initialized Manager without stable published registry in the failure snapshot. Treat other/absent state as different/incomplete evidence. |
| Fast `json:dumps` node | `TimeoutExpired` at the 120 s CLI communication boundary | Manager registry/spawning evidence with request still queued in the retained snapshot. |
| Agent no-wait/result node | `TimeoutError` at the 60 s harness completion wait, following successful submission | Successful submission with empty stderr; retain event chronology. Later completion is a separate observation, not proof of timely success. |
| Parallel Manager integrity/reuse node | Assertion at `submit_stderr`, four successful submissions/TIDs reporting degraded readiness | No stable canonical registry/proof in retained observations. A later disappearance after successful readiness is a different signature. |
| Core child launch (outside this batch) | Assertion after the existing 8 s `task_spawned` wait | Matching event absent, `children={}` and empty event tail at failure; later child existence is separate. |

After the prescribed batch finishes, stop dispatching and report each signature separately. Both-pass means not reproduced. An old-version symptom match shows the candidate is not necessary for that observed symptom, not that both failures share a cause. Candidate-only failure supports a regression hypothesis but cannot identify its cause. Different failures remain different; missing distinguishing evidence remains incomplete. None of these outcomes alone authorizes a repair or closes the original failures.

For a reproduced original signature, choose at most one next discriminator tied to H1-H6, review its exact scope, and improve the reproduction rate before minimizing. Preserve the original full-load result. Narrowing/stressing a trigger is diagnostic only, explicitly justified under [TS-0]; it never substitutes for original-load repair verification. Do not patch a test merely because it fails. Prove an oracle error with independent timely evidence if proposing H6.

If no original signature reproduces, keep the feedback-loop gate open. Report what this finite batch establishes and what is still missing. Do not automatically add versions, repetitions, or another instrumentation layer, and do not complete the repair plan based on green.

Potential later observation, NOT enabled by this first batch: pinned py-spy v0.4.2 supports `dump --pid <identity-verified-pid> --nonblocking --json` outside the subject process. [CLI](https://github.com/benfred/py-spy/blob/v0.4.2/src/config.rs), [no-lock path](https://github.com/benfred/py-spy/blob/v0.4.2/src/python_spy.rs). Before adoption, review a small native control proving continued target progress after normal observer exit and forced observer timeout. Omit locals/native/subprocess recursion; bound observer lifetime and output, preserve failures, and never change an established test verdict. Post-failure capture may be too late: startup abort can kill the child before the test receives failure. A pre-abort observation hook needs a separate explicit design/review decision. One remote snapshot cannot establish native lock ownership or duration.

### 4. Repair only a confirmed causal flaw

A repair requires the actual failure path, a falsifiable reproduction, identified owner/primitive, and a stated mechanism. Amend the active repair plan with the smallest change and independent review before runtime edits. Show RED at the exact failure assertion, GREEN after the fix, then rerun the original scenario under its original workload and deadlines. Follow the repo's fresh-eyes/cross-family completed-work gates and normal full SQLite/PostgreSQL/static/native CI requirements. Keep concurrent launch workers deferred. Existing admission/measurement results do not measure or resolve these liveness failures.

## Rollback, verification, and completion

No publication, merge, dependency-policy change, or product rollback is part of this investigation. Scratch roots, environments and observers are investigator-owned; only their own resources may be cleaned up. Retain diagnostic commits/artifacts, then archive managed diagnostic worktrees recoverably. The frozen admission-baseline checkout remains untouched.

Before plan review: verify named source paths/symbols/SHAs/versions and, after `. ./.envrc`, run `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py` at normal local concurrency, `./bin/check-doc-paths`, and `git diff --check`. Local test execution has one owner; no source/doc edits during tests. The source freeze is per experiment, not permission to edit while a run is active.

Completion requires a causal explanation and verified correction for the reproduced defect, explicit disposition of each original failure, and the required exact-head acceptance gates. A failure still lacking causal evidence remains open. Do not relabel skipped/cancelled jobs as passed, or use a successful diagnostic run as official acceptance.

## Review and deviation record

Draft. Separate author fresh-eyes and cross-family plan/workflow review required before execution. Record raw findings and explicit accept/modify/reject dispositions here. The scoped unit is this investigation plan and temporary comparison machinery, not permission to redesign Manager or revise test premises.

Author fresh-eyes initial pass: corrected the proposed invocation from `python -m pytest` to the original console-entry-point form, so the experiment does not add a path/bootstrap variable. Added the missing distinction between CLI exit and stdout-reader completion after checking the exact CPython Windows implementation. Original failed-run logs mechanically establish Python 3.14.10, correcting an earlier read-only design's 3.14.9 claim. This pass preceded experiment dispatch and external review.

Native independent fresh-eyes review: no blocker. It verified the frozen revisions, unchanged source/config comparisons, full-load controls, finite stop rule and index row; it did not independently download raw CI evidence or remote CPython sources. Its observations were to add exact evidence paths/digests before workflow review (accepted above) and to keep the optional py-spy design deferred (already outside the first batch). The author subsequently fetched baseline CI logs and independently established the historical environment differences above.

Cross-family round 1 completed in 313.80 s and blocked F1. The author verified F1 against CPython's actual Windows launcher source before revising the operative environment protocol. Dispositions:

| Finding | Disposition and operative correction |
| --- | --- |
| F1 [P1] | Accept. Replace per-arm venvs with the original system interpreter and explicit first-party editable swaps. Define product/test bytecode-cache handling; retain OS/shared-cache order effects as a limit. |
| F2 [P2] | Accept. Define oracle as tests, pytest configuration and actually read non-product input assets, mechanically classify differences, and stop on unexplained differences. |
| F3 [P2] | Accept. List required/cleared environment keys, separate artifact directories, actual worker verification, fresh parent environment per arm, and recorded controlled values. |
| F4 [P2] | Accept. Separate preflight import/entry-point verification from the explicit assumption about subject-process inheritance. No false claim of subject-module observation. |
| F5 [P2] | Accept. Preregister node/exception/deadline/retained-state symptom rules and incomplete-evidence handling. A symptom match never establishes a common cause. |
| F6 [P2] | Accept. Define PID/create-time settlement and contamination; retain completed-arm evidence when the comparison/second arm is invalidated. Survivor does not by itself prove a product leak. |
| F7 [P2] | Modify. Keep the original 50-minute job ceiling and all test deadlines. Explicitly accept possible order-dependent experiment censoring: incomplete arm/comparison is not a product failure or a verdict. Increasing the job limit is declined under the standing no-relaxation constraint. |
| F8 [P2] | Accept the source/evidence check. Verify post-kill Windows communication, extract call durations, and weaken the persistent-post-kill-pipe-holder explanation. Do not infer pre-kill process state from aggregate duration. |
| F9 [P2] | Reject the claimed contradiction; accept mechanical verification. The original CLI job's setup and test header explicitly say 3.13.17; the separately resolved core job can report 3.13.16. Preserve both per-job facts. |
| F10 [P3] | Accept. Use the original D: volume and comparable equal-depth/equal-length source roots; record paths/image. |
| Three nits | Accept. Identify the external skill location, name exact documentation gates, prefer pinned wheels and record any build inputs/output. |

Documentation verification before round 1: six metadata/spec-hygiene cases passed at normal concurrency in 0.88 s; document paths and diff hygiene passed. Revised plan requires scoped cross-family closure verification and the actual temporary machinery still requires its own review before any dispatch.

Cross-family round 2 completed in 213.65 s: PASS on accepted fixes, with one nonblocking P2-1 about evidence preservation under the shared job ceiling. Accepted: upload completed-arm evidence before starting the next arm, and explicitly censor either arm if interrupted. The actual workflow review must verify these steps. Accepted small observations clarify required environment keys, warmed preflight caches, requirement satisfaction, the exact 3.13 source citation and workspace-volume check. Record-all-processes expansion is deferred; the first experiment retains its declared Python/Weft survivor scope and does not claim exhaustive OS-resource observation. No product behavior or test premise changed.

Revised documentation gates: six metadata/spec-hygiene cases passed at normal concurrency in 0.88 s; diff hygiene and the advisory document-path command passed. The latter does not scan plans, so named plan paths and nodes were checked separately against live files/source. Plan review is complete; the actual temporary machinery and its newly accepted per-arm upload rule require review before dispatch.

Deviation Log: none.

### Cross-family round 1 verbatim

````text
# Review: `2026-10-30-pr48-windows-root-cause-analysis.md` and its index row (plan only)

I reviewed the plan text, its README row, and the files it names. No implementation exists, so none was reviewed. This review tool set had no shell, so I could not run `git diff 540707a3..f4` myself. Two claims rest only on the prose: that Manager source is unchanged between the versions, and that the failing test, conftest and harness files are byte-identical.

**Verified as stated:**
- The argv matches `test.yml:34,71`.
- The worker count is CPU+1, capped at 12 (`bin/pytest-worker-count:17`).
- The cited symbols exist at the named paths (`run_cli` `conftest.py:412`, `_raise_parallel_manager_reuse_failure` `test_cli_run.py:338`, `_attach_failure_diagnostics` `weft_harness.py:191`, `module_provenance` `subprocess_reactor_benchmark.py:72`).
- The [CC-2.2.1], [CC-3.2] and [RM-5.2] anchors exist.
- The f4 SHA is the subject of the official failed runs (`…repair-plan.md:1689`).
- The README row and the plan count (52) are correct.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | [P1] | plan:87, :89 | **The arm environment changes how processes are launched on Windows.** "Separate arm environments" plus "avoid shared editable-install paths" effectively means one venv per arm. Official CI runs the system interpreter instead (`test.yml:59`). On Windows, a venv's `python.exe` is a small launcher that starts the base interpreter as a second process. `conftest.py:299-310` makes `sys.executable` into `WEFT_TEST_PYTHON` and puts it first on PATH. `run_cli` (`:449-456`) and Manager launches then run every CLI, Manager and child through that extra launcher. That adds a process layer, an exit-code hop and inherited pipe handles, which is exactly what H3, H4 and H5 are about. It could create or hide a baseline-vs-candidate difference. A second confound: whether compiled-bytecode caches start cold or warm differs by mechanism, and that affects H5 startup timing. | Name one mechanism that keeps the original process structure. The simplest: install the common third-party set into the setup-python system interpreter, then swap only the first-party packages (`--no-deps`) between arms, comparing manifests before each arm. Also state the cache rule (each arm starts cold, as CI does). If a venv is unavoidable, record the extra process layer as a declared confound for H3, H4 and H5. |
| F2 | [P2] | plan:40, :89 | **Which files make up the test oracle is undefined.** Only `tests/` is overlaid. The pytest config (`pyproject.toml:137-146`: markers, minversion, testpaths) and any non-test files the CLI tests read (e.g. `bin/`, `examples/`) would come from the baseline product. A marker mismatch fails loudly, but other config differences would silently change the oracle. | Define the oracle as `tests/` plus the pytest ini section plus any repo files `tests/cli` reads. Require them to be byte-identical between arms (or taken from f4). Otherwise invalidate the pair. |
| F3 | [P2] | plan:95 | **The environment for each arm is not fully listed.** The original CLI slice also set `WEFT_TEST_DIAGNOSTICS_DIR` (`test.yml:69`), which enables `run_diagnostics.py:172` and the harness diagnostics (`weft_harness.py:197`). The plan says neither that it must be set nor that it must differ per arm. `PYTEST_XDIST_AUTO_NUM_WORKERS` is likewise unnamed. `WEFT_TEST_PYTHON` uses `setdefault` (`conftest.py:301`), so a value leaking from arm 1, or set by the runner, would silently pick the wrong interpreter. | Add a per-arm list: required (heartbeat 120, worker count, a separate diagnostics dir per arm) and must-be-absent (`WEFT_TEST_PYTHON`, `PYTHONPATH`, leftover `WEFT_*`/`BROKER_*`). Record the actual environment in each arm's artifacts. |
| F4 | [P2] | plan:89 | **The provenance check cannot see the processes that actually fail.** `module_provenance` only checks `weft*` modules imported by the current process (`subprocess_reactor_benchmark.py:74-75`). The failures happen in CLI, Manager and child subprocesses. Those import `weft` via `PYTHONPATH=REPO_ROOT` (`conftest.py:426-429`), but `weft_docker` and the other extensions come from installed package metadata. The plan forbids observers inside subject processes, so as written the check is either unmeetable or incomplete. | Before each arm, run a separate probe process using the arm's interpreter and `run_cli`'s environment construction. It should check `weft` and extension `__file__`, blob hashes, and plugin entry points. Declare that subject subprocesses are assumed to inherit the same environment rather than observed. |
| F5 | [P2] | plan:44, :109 | **What counts as a "matching signature" is not set in advance.** The interpretation rules ("baseline failure shows the candidate is not necessary for that signature") depend on it. Matching on node ID alone lets the analyst pick the match after seeing results. A baseline failure at the same node may come from a different mechanism. | Before dispatch, set match criteria for each row: node ID, exception type and timeout boundary, and the retained-state category (e.g. before registry vs registry plus queued request for 3.13). Anything else counts as "different failure". |
| F6 | [P2] | plan:103 | **The survivor rule throws away the most useful runs.** Reproduced failures tend to leave processes behind (the 3.14 run left a surviving managed child at teardown, `repair-plan.md:1682`). Marking the whole pair "invalid" would discard a valid original-load reproduction, and on 3.13 (candidate first) it would cancel the baseline arm exactly when it is needed. "Unexplained owned survivor" also has no defined settle point or ownership test. | Mark the *comparison* invalid but keep the completed arm as single-arm evidence; survivors are themselves H4 evidence. Define survivors as a diff of processes before and after the arm, taken after pytest exits and a bounded `psutil.wait_procs` in the runner, with no sampling during the arm. |
| F7 | [P2] | plan:93-95 | **The 50-minute ceiling is shared between both arms.** Running two full arms in one job under "unchanged 50-minute job ceiling" makes the second arm's time budget depend on the first. Hung tests with a 900 s timeout across 5 workers can use it up, censoring arm 2 in an order-dependent way. | Keep 50 minutes per arm (step timeout 50, job timeout covering both arms plus setup). This preserves the original bound rather than relaxing it. Record a censored arm as incomplete. |
| F8 | [P2] | plan:42 | **The old evidence may already answer part of the "missing observation".** In CPython 3.12, after a `TimeoutExpired`, `subprocess.run` kills the child and then on Windows calls `communicate()` again *without a timeout*. If a long-lived descendant still held stdout, that second call would block well past 120 s, toward the 900 s pytest timeout. So the failed test's recorded duration partly separates "CLI still running" from "descendant holds the pipe". | Before the batch, check the retained durations against `Lib/subprocess.py` `run()` at v3.12.10. Narrow the H4 sub-question accordingly; this may remove work. |
| F9 | [P2] | plan:38, :93 | **The Windows 3.13 interpreter version conflicts with the parent plan.** This plan says 3.13.17 for run 41120214739. The parent plan reports Windows CPython **3.13.16** for the core job in that same run (`repair-plan.md:1646`); 3.13.17 was the Linux version. Pinning the wrong patch version breaks the "original version" match. | Check the CLI job's setup-python log mechanically, as was done for 3.14.10, and correct either the plan or the parent record. |
| F10 | [P3] | plan:89 | Scratch-root location is unspecified. Drive and path length (the original checkout is under `D:\a\weft\weft`) affect Windows I/O, antivirus scanning and path limits. | Use the same volume and a comparable path depth; record both. |
| nit | plan:17 | `diagnosing-bugs` is not in the repo; a zero-context engineer cannot find it. | Give its path or mark it as an external skill. |
| nit | plan:125 | The "plan metadata/spec-hygiene and document-path checks" are not named. | Name them, e.g. `tests/specs/test_plan_metadata.py`. |
| nit | plan:87 | "wheel/source set": building from source would add build variation. | Use wheels only, or record the build. |

## Observations (non-actionable)

- **Pre-existing CI drift.** CI resolves dependencies at install time with `--system` (pytest 9.2.0 vs 9.1.1 in `uv.lock`), while [TS-0] relies on `.envrc`/`.venv`. This predates the plan, which records it rather than making it worse. Any dependency-policy change is out of scope.
- **Declared limits.** The 3.14 core child-launch signature stays outside the first batch, as declared.
- **Constraints respected.** The plan stays within WT-7, ALT-WT-017/021 and TS-0. It authorizes no lower-load acceptance, no new product timer, polling fallback, deadline or worker change, and no concurrent launch workers. RECONSIDERATION REQUESTED handling is present. py-spy is correctly held back.
- **Possible addition.** The temporary workflow could inherit the earlier witness branch's read-only permissions and pinned actions (`repair-plan.md:1686`). This is an addition, not a blocker.

## Verdict: **BLOCKED (owner to confirm) on F1**

- **Can a zero-context engineer implement it confidently?** Not yet. The natural reading of "separate arm environments" (F1) changes the process structure the hypotheses depend on. F2–F5 leave the oracle, environment, provenance scope and match rules open to judgment calls made after the results are in.
- **Would faithful implementation avoid making the system worse?** Mostly yes. Product and tests stay frozen, and step 4 requires a demonstrated mechanism before any repair. The remaining risk is misleading evidence: F1 could produce a false regression signal or hide the failure, and F6 and F7 could discard or truncate exactly the informative arms.

Resolving F1 clears the block. I recommend settling F2–F7 and F9 before dispatch. F8 is optional, and could remove some investigation work.
````

### Cross-family round 2 verbatim

````text
**Verdict: PASS.** The accepted fixes do what they claim. I found one P2, a gap in the F7 text about keeping evidence when the job ceiling is hit; it isn't a blocker. Everything else is an observation or nit. I didn't edit anything.

## Fix-by-fix verification

| ID | Result | Basis |
|---|---|---|
| F1 | Fixed | No per-arm venvs, the system interpreter stays, and only the first-party editable packages are swapped. `conftest.py:271-274` returns the system `sys.executable` when it exists, and `run_cli` (`conftest.py:451-456`) launches `<system python> -m weft` with `PYTHONPATH=REPO_ROOT` (`:426-429`), so no launcher layer is added. The bytecode-cache rule and the shared-cache limit are both stated. |
| F2 | Fixed | The oracle now covers tests, pytest config and the input files the tests read. The only file outside `tests/` that `tests/cli` reads is `pyproject.toml` (`tests/cli/test_cli.py:25`), which is identical between revisions. `tests/tasks/*` comes with the overlay. `bin/pytest-pg` is only used by `tests/helpers/pytest_pg_runtime.py:21`, which the SQLite slice doesn't run. There is no root-level `conftest.py`. |
| F3 | Fixed (one wording nit, below) | It names the required keys, the keys to clear and a separate diagnostics directory per arm. Clearing `WEFT_TEST_PYTHON` is necessary because conftest only sets it with `setdefault` (`conftest.py:301`). |
| F4 | Fixed | The preflight check is kept separate, and the inheritance assumption is stated. `run_cli` puts the archive root on `PYTHONPATH`, so a preflight built with the same environment code checks the right `weft` source. |
| F5 | Fixed | The rules are fixed before dispatch, and the general rule handles mismatches as different or incomplete evidence. "Four submissions" matches `range(4)` at `test_cli_run.py:405`, and the `submit_stderr` phase is at `:420`. Diagnostics should behave the same in both arms: `tests/helpers/run_diagnostics.py` imports nothing from `weft`, and the harness is unchanged per the root's diff. |
| F6 | Fixed | It defines PID/create-time identity, a 5 s settlement done only by the runner, keeps the completed arm when the comparison is invalidated, and says a survivor alone doesn't prove a leak. |
| F7 | Correct; one P2 gap | Step 2's "No job, subprocess, test or module deadline is increased" matches the modified disposition. See P2-1 for what's missing. |
| F8 | Fixed | `run_cli` really uses `subprocess.run(..., timeout=...)` (`conftest.py:462-468`), so the kill-then-`communicate()` path applies. The H4 wording ("weakened"; the pipeline's 163.09 s "is not an EOF-delay measurement"; nothing about the state before the kill) claims no more than the evidence supports. |
| F9 | Correctly not reopened | Per-job facts are recorded at plan:44. |
| F10 | Fixed (observation below) | Same volume, equal path depth and length, paths and image recorded. |
| Nits | Fixed | The external skill is marked as such, the gates are named exactly, and wheels are preferred with build inputs and outputs recorded. |

## Finding

**P2-1. The F7 text promises to keep evidence but gives no way to do it, and doesn't cover arm one being cut off** (plan:119, :121)

- With one shared 50-minute ceiling, a hang can end the job while either arm is running.
- The plan says "Preserve completed-arm evidence", but nothing requires arm one's logs, JUnit, diagnostics and manifests to be uploaded before arm two starts. Artifact steps that only run when the job is cancelled aren't guaranteed to have time for a large upload.
- The text only covers the case where arm one uses up the time and arm two is censored. It doesn't say what happens when arm one itself is cut off. On 3.13 (candidate first) that is the most likely case to contain a match.
- Following F5, this should be decided before dispatch, not after seeing results.

**Suggested disposition: accept, with no deadline change.**
- Upload each arm's artifacts in a separate step as soon as that arm exits, before arm two's setup.
- State that an arm ended by job cancellation is censored/incomplete. Any original-signature match already in its streamed results counts only as partial single-arm evidence, never as a comparative result.

## Observations (not blocking)

1. **F7 disposition wording** (plan:176): "Decline increasing the job limit to conceal this limit" gives the wrong reason. The decline follows from the standing no-relaxation constraint. Suggest "…declined under the standing no-relaxation constraint." The operative step-2 text is fine.
2. **F3 nit** (plan:117): "except the named diagnostics keys" could be read as also stripping the required `WEFT_TEST_PROGRESS_HEARTBEAT_SECONDS`, which isn't a diagnostics key. Say "except the required keys listed above". The recorded controlled variables would expose the mistake anyway.
3. **F1 cache description** (plan:103): the import preflight warms third-party bytecode in the shared system site-packages before arm one as well. So neither arm starts the way CI does, rather than only arm two being warmer. This is small: in CI, pytest collection also imports third-party code before the CLI subprocesses run. Fold it into the recorded limit.
4. **F1 `--no-deps` swap:** only the root `pyproject.toml` is stated to be identical. Add a cheap per-arm check that the common third-party set satisfies each arm's declared requirements (`uv pip check` or a `Requires-Dist` comparison for the four distributions), stopping on a mismatch. Preflight imports catch loud breaks but not silent version-range mismatches.
5. **F6 survivor filter:** "Python/Weft processes" could miss children that aren't Python. The risk is low, since the CLI tests mostly launch Python targets (`tests/tasks/*`). Recording all new processes and then classifying them would be safer, but this isn't required.
6. **F8 citation:** the durations come from 3.13.17 jobs, but the link is to 3.12.10's `subprocess.py`. The behavior is the same; citing v3.13.17 as well would close the gap. Also, 124.17 s still allows a post-kill hold of up to about 4 s, and the pipeline case still allows a bounded hold of up to about 43 s. Treat the "weakened" wording as covering the three 3.13 cases only.
7. **F10:** if the current `windows-latest` image doesn't put the workspace on D:, the instruction can't be followed. Add: verify the workspace volume; if it differs, record a deviation or stop.
8. **Gates:** `bin/check-doc-paths` only scans `docs/agent-context`, `docs/specifications`, `skills`, `AGENTS.md` and `docs/coalescing.md` (`bin/check-doc-paths:40-41`). So it doesn't check this plan's paths, and passing it isn't evidence about them; the plan's manual path check covers that. The gates recorded as passing ran before this revision, so rerun them on the revised plan and `docs/plans/README.md`.
````
