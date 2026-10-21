# Unbounded Dependency Compatibility

Status: completed
Source specs: docs/specifications/13C-Using_Weft_With_Django.md [DJ-19]
Superseded by: none

Class: 5. This changes a published compatibility contract in [DJ-19] and, after
real-SDK verification exposed an adapter incompatibility, touches TaskRunner's
Microsandbox execution boundary. Hardening applies to that runtime slice.

## Goal

Remove speculative upper bounds from all published dependencies. Keep minimum
versions where they express the oldest supported API, and let resolution select
newer compatible releases. Remove tests that freeze current package or dependency
version text while retaining tests for protocols, transformations, and metadata
relationships whose version values are the behavior under test.

## Source Documents And Current Evidence

- [DJ-19](../specifications/13C-Using_Weft_With_Django.md) currently declares a
  Django upper bound that conflicts with the requested no-ceiling policy.
- The published manifests own dependency metadata:
  `integrations/weft_django/pyproject.toml`,
  `extensions/weft_docker/pyproject.toml`,
  `extensions/weft_macos_sandbox/pyproject.toml`, and
  `extensions/weft_microsandbox/pyproject.toml`.
- `tests/system/test_optional_extras.py` freezes the exact Channels requirement.
  Its useful contract is that Channels remains an explicit opt-in and that the
  realtime alias matches it, not a particular dependency version string.
- Universal `uv` resolution is the falsifiable proof for dependency metadata.
  The reported resolver error concludes that no allowed microsandbox release is
  compatible with its PyPy/Linux/aarch64 split because the ceiling excludes all
  releases from 0.6 onward. The lockfile alone does not establish which upstream
  artifact distinction caused that conclusion.

## Proposed Spec Delta

Promotion strategy D: update [DJ-19] in place. Replace its versioning rules with
this exact text:

> - `weft-django` release numbering tracks the supported Weft core major; this
>   is a release-numbering rule, not a dependency upper bound
> - published dependencies declare minimum versions for the APIs in use and do
>   not impose upper bounds without a reproduced incompatibility and a tracked
>   removal path
> - unsupported combinations must fail clearly rather than silently degrading
> - do not ship reflection-based compatibility fallbacks

Remove the obsolete rollout instructions for the already-shipped client-session
transition. Keep the sibling-repository failure rule and the no-reflection rule.

## Implementation

1. Remove every dependency upper bound from all first-party package manifests.
   Do not lower minimums or add exclusions. The microsandbox manifest was edited
   before this task expanded from one broken bound to a repository-wide contract;
   keep that edit and record it as the only sequencing deviation.
2. Replace the exact Channels requirement assertion in
   `tests/system/test_optional_extras.py` with a normalized dependency-name
   assertion using `_normalized_dependency_name`; delete the now-unused
   `_dependency_entry`. Keep all other tests unchanged.
3. Update [DJ-19], the Django README, and the changelog to state the no-ceiling
   policy and current compatibility shape without freezing current releases.
4. Adapt `_exec_with_cancel` to accept the SDK's general awaitable return by
   scheduling it with `asyncio.ensure_future` rather than coroutine-only
   `asyncio.create_task`. Add a focused unit test whose fake `sandbox.exec`
   returns a pending `asyncio.Future` resolved on the next loop turn; with no
   cancellation request, it must fail with the old implementation and return
   the identical sentinel result with the fix. Do not change cancellation,
   kill, timeout, cleanup, or result mapping.
5. Raise the Microsandbox minimum to 0.7.0, the first probed release exposing
   `is_runtime_installed`, and migrate preflight to that API without an old-name
   fallback. Fail clearly if the callable is absent. Require the installed-SDK
   binding test to exercise it; add true-result, false-result, and missing-checker
   coverage. Compare the generated network object with the installed SDK's
   `Network.none()` result rather than its obsolete internal string field. Do
   not silently skip preflight.
6. Regenerate `uv.lock`, then verify universal resolution, focused package tests,
   the real Microsandbox path, Ruff, formatting, mypy, and metadata/spec gates.

No abstraction is added. A negative test enumerating forbidden version strings
would recreate the brittle policy gate the user rejected, so verification uses
manifest inspection plus the real resolver.

## Invariants And Risks

- Optional integrations remain optional and platform markers remain unchanged.
- Minimum versions remain intact except Microsandbox, whose minimum rises to
  0.7.0 because that is the first probed release with the required
  `is_runtime_installed` API. This work does not claim compatibility below the
  declared minimums.
- A future incompatibility may justify a temporary upper bound only with a
  reproduced failure and a tracked removal path. This plan does not add a gate
  that attempts to encode that judgment mechanically.
- Removing ceilings exposes latent upstream incompatibilities sooner. The lock
  update and package suites are the present evidence; future upstream releases
  still require ordinary CI diagnosis rather than speculative bounds.
- The SDK execution operation may be any asyncio awaitable, including a Future.
  `ensure_future` returns an SDK Future unchanged or creates a Task for a
  coroutine; `_exec_with_cancel` remains its sole poller and canceller. Cleanup
  remains in `_run_async` and no second execution path is added.
- Stop and re-evaluate if compatibility requires changing command arguments,
  result shape, sandbox cleanup, or cancellation semantics. Those are not
  implied by the observed awaitable-type mismatch.
- Rollback is code-only: restore coroutine-only scheduling together with an SDK
  version that actually returns a coroutine. There is no persisted state or
  queue migration, and mixed extension versions do not share in-flight state.
- Historical plans and changelog entries keep their period-accurate version text.

## Verification

Run:

```bash
uv lock
uv lock --check
uv sync --all-extras --locked
./.venv/bin/python -m pytest tests/system/test_optional_extras.py tests/specs/test_plan_metadata.py integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Inspect all requirement declarations with `rg` to confirm no dependency upper
bounds remain. Run the enabled real microsandbox integration tests with the
locked SDK; if their external runtime is unavailable, record the skip rather
than treating fake-only tests as full compatibility evidence. Review the plan
before implementation and the final diff before completion using a different
agent family.

## Deviation Log

| Date | Deviation | Reason | Disposition |
| --- | --- | --- | --- |
| 2026-10-21 | The microsandbox manifest and its exact-bound test changed before the Class 5 plan. | The initial request named one resolver failure; the user then expanded scope to all ceilings and version-pinning tests. | Retain the narrow edit and review it as part of this full unit. |
| 2026-10-21 | Real SDK verification exposed a Future-returning `sandbox.exec` that `asyncio.create_task` rejects. | Removing the unsupported ceiling selected Microsandbox 0.7.8 and exercised the real boundary. | Amend and re-review the plan before the narrow adapter fix; do not restore the ceiling. |
| 2026-10-21 | Completed-work review found that SDK 0.7 renamed `is_installed` to `is_runtime_installed`, causing preflight to skip its availability check. | The real smoke test had an installed runtime, so execution passed while the negative preflight path had lost its check. | Per owner direction, require Microsandbox 0.7.0 and migrate directly with no backward-compatibility fallback. |
| 2026-10-21 | SDK 0.7 represents `Network.none().policy` as a policy object rather than the old string field. | The real deny-network smoke test proves behavior; the unit assertion was coupled to an obsolete representation. | Compare the adapter output with the installed SDK's own `Network.none()` value. |

## Review Log

2026-10-21 cross-family plan review (Claude, Tier 1): PASS, no blocker. Accepted
F1-F10: sync before tests; run or disclose the real microsandbox integration;
quote the resolver error without inferring wheel causality; avoid a global lock
upgrade; say declared rather than tested minimums; provide exact [DJ-19] text;
clarify release numbering is not a cap; remove `_dependency_entry`; name the
structural Channels assertion; and narrow the test-edit description.

2026-10-21 cross-family runtime-amendment review (Claude, Tier 1): no blocker.
Accepted F1-F2 by correcting Future ownership language and specifying a pending
Future test that exercises the polling loop. F3 was optional duplicate coverage
of standard Future cancellation and was declined. F4 records that the SDK stub
declares a coroutine while the compiled runtime returns a Future; no type
narrowing should rely on that inaccurate stub.

2026-10-21 cross-family completed-work review (Claude, Tier 1): blocker F1
accepted. Owner direction rejected backward compatibility; probes showed 0.6.0,
0.6.1, and 0.6.18 expose only `is_installed`, while 0.7.0 exposes only
`is_runtime_installed`, so the minimum becomes 0.7.0 and preflight migrates
directly. F2 accepted through the deviation row above. F3 accepted by documenting
the Future compatibility fix in the changelog.

2026-10-21 round-2 review: FAIL on documentation completeness only. Accepted
all three findings by recording the network-object assertion change, correcting
the minimum-version invariant, and stating the Microsandbox 0.7.0 floor break
plainly in the changelog. Code and tests for F1 were confirmed correct. Round-3
verification followed.

2026-10-21 round-3 review: PASS. The network-object test change, minimum-floor
exception, and explicit compatibility break are fully recorded with no new
defect.

