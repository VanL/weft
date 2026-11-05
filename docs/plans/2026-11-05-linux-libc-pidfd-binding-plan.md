# Linux pidfd Binding Through libc When Python Omits the Wrapper

Status: promoted
Source specs: docs/specifications/06-Resource_Management.md [RM-5.2]
Superseded by: none

Class: 5 (implementation with spec revision; runtime execution boundary, hardening required).

## Goal

Allow Linux one-shot subprocess supervision to open the same kernel pidfd through
libc/ctypes when the Python build omits `os.pidfd_open`. Keep the existing selector,
exit registration ordering, reaping owner, and descriptor lifecycle. This is a
native function binding, not a callback or a new supervision mechanism. This
plan owns the library implementation and its evidence; production deployment
is separate work.

## Source Documents and Spec Baseline

Baseline: `44d7f90050c559bda10cdd0b3360e09a553f338b` for
`docs/specifications/06-Resource_Management.md` [RM-5.2],
`docs/specifications/07-System_Invariants.md` [IMPL.9], and
`docs/program-theory.md` [WT-2] item 4 and [ALT-WT-021].

Consulted during authoring: `docs/program-theory.md`,
`docs/agent-context/README.md`, `docs/agent-context/decision-hierarchy.md`,
`docs/agent-context/principles.md`, `docs/agent-context/engineering-principles.md`,
`docs/agent-context/lessons.md`, and `docs/lessons.md`.

Implementer: read `AGENTS.md`, `docs/agent-context/engineering-principles.md`,
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`,
`docs/agent-context/runbooks/testing-patterns.md`,
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, and
`skills/test-audit/SKILL.md`. The completed
`docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` explains
historical reactor decisions; current specs govern behavior.

External evidence:

- CPython 3.14.3 `Modules/posixmodule.c` conditionally compiles `os.pidfd_open`
  under Linux and `__NR_pidfd_open`:
  <https://github.com/python/cpython/blob/v3.14.3/Modules/posixmodule.c>.
- Astral's standalone release 20260718 documents new kernel headers making
  `os.pidfd_open` available:
  <https://github.com/astral-sh/python-build-standalone/releases/tag/20260718>.
- ctypes signatures and errno handling:
  <https://docs.python.org/3/library/ctypes.html>.

Promotion baseline identifier: diff base `44d7f90050c559bda10cdd0b3360e09a553f338b`
plus the combined diff in `docs/specifications/06-Resource_Management.md`,
`weft/core/runners/subprocess_runner.py`, and `weft/core/deferred.py`. Strategy B
keeps the spec/code delta in the same change; the promoted spec is governing.

## Verified Premise and Current Structure

On 2026-11-05, read-only SSH probes on `ops` found the live Manager using
`/opt/uv-python/cpython-3.14.3-linux-x86_64-gnu/bin/python3.14`, standalone
build `20260211`, via the governance release virtualenv. Installed Weft was
0.9.120. `os` and `posix` both lacked `pidfd_open`, including under `python -S`.
The installed `_open_child_exit_source(os.getpid())` reproduced `RuntimeError`
chained from `AttributeError`. Calling libc's exported `pidfd_open` through
`ctypes.CDLL(None, use_errno=True)` in that interpreter succeeded. Debian's
system Python 3.13.5 also opened a real pidfd. These probes prove the wrapper
gap and available libc route in the SSH process; they do not establish the
systemd service's full sandbox policy or retrieve the retained failing task.

The production path is `HostTaskRunner._run_command_with_hooks` in
`weft/core/runners/host.py` -> `run_monitored_subprocess` -> `_open_process_io`
-> `_PosixSelectorProcessIO` -> `_open_child_exit_source`, in
`weft/core/runners/subprocess_runner.py`. Linux currently calls
`os.pidfd_open(pid)`. `_DescriptorExitSource` owns the returned fd; the selector
uses it only as a wake source. The supervisor alone calls `process.poll()`.
`weft/core/deferred.py` already owns deferred core dependencies.

CI uses `actions/setup-python` for its Python version matrix in
`.github/workflows/test.yml`; installing uv and using `uv pip install --system`
does not reproduce the old standalone interpreter. Existing missing-primitive
coverage asserts rejection, not successful native fallback.

## Context, Files, and Ownership

Implementation files to modify:

| File | Required change and owner boundary |
| --- | --- |
| `weft/core/runners/subprocess_runner.py` | Own Linux binding selection and native-call error conversion beside `_open_child_exit_source`; reuse `_DescriptorExitSource`. |
| `weft/core/deferred.py` | Add a small typed `get_ctypes()` accessor (new symbol) for the optional native dependency. Keep deferred-import reason explicit. |
| `tests/core/test_subprocess_runner.py` | Extend existing exit-source and inherited-pipe proofs; add narrow native ABI/error proofs where existing tests cannot detect the new risk. |
| `docs/specifications/06-Resource_Management.md` | Promote [RM-5.2] delta, mapping, and plan backlink. |
| `README.md` | Synchronize Linux platform requirement. |
| `CHANGELOG.md` | Record the user-visible compatibility change under Unreleased. |
| `docs/plans/README.md` and this plan | Keep lifecycle/index status and evidence synchronized. |

Read `tests/core/test_subprocess_runner.py` helpers `_patch_os`, `_Forwarding`,
`_ChildBarrier`, `_supervise_real_process`, `_spawn_inherited_writer_root`,
`_assert_owned_pipes_released`, and `_reap_test_process` before test edits.
Do not create a native-platform package, generic FFI framework, or new dependency.

Before the first implementation edit, record answers in the execution log:

1. Why must exit readiness be independent of stdout/stderr EOF? Expected:
   descendants can retain the pipes after root exit; the pidfd must wake the
   selector even when no timeout is armed.
2. Who reaps the child, and when is numeric PID lookup forbidden? Expected:
   the supervisor's `Popen.poll()` reaps; cached `process.returncode` bypasses
   exit-source lookup, and live-child registration precedes the first poll.

Wrong answers are a stop-and-re-read gate.

## Invariants, Constraints, and Failure Policy

- Preserve [WT-2] item 4, [ALT-WT-021], and [RM-5.2]: one selector, no polling
  floor, no helper thread, no process-wide signal handler. Kernel exit remains
  a distinct readiness source. No changes to macOS/BSD or Windows behavior.
- Keep the durable TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state
  spine, including the broker-free worker boundary [IMPL.9]. TIDs, forward-only lifecycle, reserved keep/clear policy, frozen
  `spec`/`io`, spawn isolation, queue schemas, and runtime-state persistence
  exclusions are untouched. No template/resolved TaskSpec change, queue-history
  reader, new persistence, or asynchronous cleanup is introduced.
- Prefer `os.pidfd_open` if the attribute is present. Only its absence selects
  libc. Do not catch AttributeError around the entire call and mistake an
  internal failure for absence. Do not retry native Python syscall errors
  through another binding.
- Resolve ctypes only on the Linux missing-wrapper path through `get_ctypes()`.
  Use `CDLL(None, use_errno=True)` and its exported `pidfd_open`. No hardcoded
  libc filename, library-search subprocess, raw syscall number, or `os` monkey
  patch in production. Do not load libc at module import on other platforms.
- Set native signature `(c_int, c_uint) -> c_int`; pass flags `0`. Keep the
  CDLL and function objects alive through the call. A simple per-acquisition
  binding suffices; no global binding/negative-result cache or shared mutable
  initialization protocol is needed.
- On native return `-1`, immediately capture `ctypes.get_errno()` and raise
  `OSError(code, os.strerror(code))`, retaining Python's specialized errno
  subclasses. Successful fd `0` is valid. The kernel supplies close-on-exec;
  ownership transfers exactly once to `_DescriptorExitSource`.
- Missing ctypes (`ImportError`) or missing exported symbol (`AttributeError`)
  is a capability failure, chained into `RuntimeError`. Keep catches scoped
  to dependency acquisition/symbol lookup. Unexpected loader `OSError` stays
  the original error, not a speculative capability diagnosis.
- Preserve `ProcessLookupError`/ESRCH as already-gone immediate wake. Wrap only
  syscall ENOSYS/EPERM/EACCES as capability `RuntimeError`, chained from the
  actual syscall error. EMFILE and other non-capability syscall errors
  propagate unchanged. A failed ctypes call never becomes a valid descriptor.
- Acquisition/selector-registration failure follows existing session closure
  and subprocess failure cleanup. Do not let diagnostic formatting or optional
  information collection mask the primary error. Preserve existing exception
  precedence and outcome semantics.

## Difference Register

| Boundary | Baseline | Planned disposition |
| --- | --- | --- |
| Python wrapper present | Call Python API | Preserve, including original failure handling; never invoke libc as a retry. |
| Python wrapper absent | Reject | Intentionally replace with libc binding attempt. |
| Native dependencies unavailable | Python missing-attribute diagnostic | Report missing binding explicitly, preserving actual cause. |
| Kernel unsupported/security denial | Linux capability RuntimeError | Preserve type/cause and identify kernel/policy rather than missing Python wrapper. |
| fd lifecycle, register-before-poll, cached returncode | Existing ownership rules | Preserve. |

## Proposed Spec Delta

Promotion strategy: B, atomic spec/code change, for
`docs/specifications/06-Resource_Management.md` [RM-5.2]. The spec-promotion
slice and implementation form one atomic change: apply the reviewed text first
within that change, then implement; spec, implementation, tests, and README land
together. Temporary RED
implementation state is not a release candidate. Do not publish the promoted
contract independently of working implementation.

Replace exactly the paragraph beginning "On Linux, subprocess supervision
requires Linux 5.3 or newer" and ending "still precedes the supervisor's poll."
with the following text:

```text
On Linux, subprocess supervision requires Linux 5.3 or newer and permission
to open pidfds. When Python provides os.pidfd_open, the supervisor uses it.
If that attribute is absent, the supervisor attempts the exported libc
pidfd_open function through ctypes. This compatibility binding opens the same
kernel exit descriptor; it introduces no helper thread, polling fallback,
or alternate reaping owner. It does not retry a failed Python pidfd_open call
through libc, and it does not invoke raw architecture-specific syscall numbers.

If Python lacks os.pidfd_open and either ctypes or libc's exported pidfd_open
symbol is unavailable, supervision raises RuntimeError identifying the unavailable
binding, chained from the actual acquisition
error. Kernel ENOSYS or security-policy EPERM/EACCES raises RuntimeError
identifying the syscall support or permission requirement, chained from the
actual syscall error. Other loader and syscall errors propagate unchanged.
ProcessLookupError means the child is already gone and requests an immediate
wake. A cached process returncode bypasses PID lookup for an already-reaped
child. Live-child exit registration still precedes the supervisor's poll.
```

Clause audit: the kernel floor, actual syscall permission, RuntimeError and
cause chaining, absence of timer fallback, cached-returncode shortcut, and
registration ordering survive. The Python-wrapper requirement is intentionally
relaxed; explicit missing-binding and native errno behavior are added.

In [RM-5.2] implementation mapping, add `_open_child_exit_source` as the Linux
binding-selection and kernel-exit ownership boundary, with optional dependency
loading in `weft/core/deferred.py`. Add this plan to `## Related Plans`.
Update the README Linux requirement to allow Python's wrapper or libc through
ctypes, keeping the kernel and policy requirements. Explain that the libc route
requires an exported symbol (provided by glibc 2.36+), not merely any libc. The
version fact is verified against glibc's release announcement:
<https://sourceware.org/pipermail/libc-alpha/2022-August/141193.html>. Resolve
the actual symbol; do not add a libc version-string gate. Include the Python
executable/version in missing-binding diagnostics as useful implementation
context, without making those exact diagnostic fields a normative promise. No theory revision is
needed: pidfd readiness and the refused timer/thread alternatives are preserved.

## Rollout, Rollback, and Non-Goals

Implementation is a library change; no ops access or service mutation is
authorized by this plan. An explicitly authorized later deployment should
stage the candidate in the actual governance virtualenv and service security
context, run a harmless command through Weft, verify output and terminal
completion, then cut over through the deployment owner's normal drain/restart
procedure. Do not automatically retry the failed production command: the
command is spawned before exit-source acquisition, so it may have had effects.

Rollback is a code/package rollback with no queue or schema migration. It
restores rejection on old Python builds, so retain a known-compatible Python
runtime if rolling back on ops. Existing tasks still obey their launching
runtime's lifecycle. No one-way door is introduced. Updating Python/uv, editing
the governance deployment verifier, adding a distribution matrix, expanding
interactive-session pidfd use, and supporting genuinely unavailable pidfds
are separate work, not tasks hidden in this plan.

## Implementation Tasks

### 1. Review and spec-promotion slice

Complete self-review and Tier-1 cross-family review of the entire plan and exact
delta. Resolve findings in the operative text. Promote the reviewed delta and
mapping/backlink within the combined change; keep status/index
consistent with the chosen strategy. Record the combined spec/code commit as
the promotion baseline in Task 3 (or the exact diff base and file diffs if left
uncommitted). Qualify the traceability invocation before implementation as
described below. Stop if review demands a theory reversal,
another execution path, or a new platform-support policy not decided here.

### 2. RED contract coverage, then the minimal binding (same atomic change)

Use the existing test-audit authoring gate. Record the intended RED against
baseline production before implementing the compatibility helper. A narrow
Linux helper (new private symbol beside `_open_child_exit_source`) may select
the binding and normalize errno; keep selector/reaping logic where it lives.
Add `get_ctypes()` in the existing deferred accessor module. Annotate changed
functions and their [RM-5.2] ownership. Do not write tests mirroring the helper.

| Proof and smallest seam | Credible regression / existing gap | Required oracle |
| --- | --- | --- |
| Extend real inherited-writer, no-deadline test with Linux libc variant | Missing Python wrapper currently rejects; EOF-only wake would hang | Hide only module-local Python wrapper, keep real libc, child, pipes, selector, and supervisor; existing output, bounded completion while descendant remains alive, and released pipes must hold. Baseline RED must be missing-wrapper RuntimeError. |
| Extend pre-reaped/registration-order coverage as needed | Reusable numeric PID or registration gap | Cached returncode forbids either lookup; real fallback registration precedes the first reaping poll. Reuse existing tests rather than duplicate orchestration. |
| Extend narrow error table for each binding | ctypes returns -1 instead of raising; lost errno or broad catch | Python syscall cases keep existing exception identity; native dependency injection sets real ctypes errno and returns -1 for ESRCH, EPERM, and EMFILE. Reuse existing Python cases for the shared classification of ENOSYS/EPERM/EACCES and ESRCH/EMFILE; the native cases independently prove errno conversion, specialized subclassing, and pass-through. Assert expected specialized exception/cause and no resource-error reclassification. |
| Missing dependency/symbol/loader cases | Old missing-Python test now rejects too early or machine-dependent lookup masks failure | Replace its premise with both bindings unavailable; inject only acquisition dependencies, not `_open_child_exit_source`. Missing ctypes/symbol yields chained binding RuntimeError with binding identity (and executable/version as implementation context); loader OSError is preserved. Keep a narrow loader failure case because acquisition must stay outside syscall-only classification. |
| Python wrapper wins even if it fails | Catch-all fallback conceals denial or resource error | Trap libc acquisition; existing Python success and error cases must not enter it. For native return 0, assert source.fileno() == 0; replace only the module-local os.close with a recording stub before closing that source, so real stdin cannot be closed. |

For wrapper hiding, add a test-local forwarding view whose __getattr__ raises
AttributeError for pidfd_open and delegates other attributes. Existing
_Forwarding alone cannot hide an attribute; os=object() must not be used in
real-libc tests because os.close and os.strerror are still needed. Native
acquisition rejoins the unchanged fd owner; reuse existing lifecycle cleanup
proof rather than adding fd-count or EBADF assertions vulnerable to descriptor
reuse or unrelated opens.

Linux real-libc variants must run on the Linux 3.12/3.13/3.14 CI jobs, without
slow markers or capability-based skips masking a missing native symbol. Use
platform skip only outside Linux. On macOS, run narrow dependency-injection
tests through a module-local Linux view; never mutate shared stdlib `os`,
`sys`, or process identity. On a Linux test image lacking the declared libc
symbol, fail the test with an unsupported-environment diagnostic; do not skip
or claim real-libc coverage. CI image capability and exact interpreter remain evidence to record.

The real supervisor regression exercises the fallback, not a fake exit fd.
For new failure/ownership assertions, demonstrate focused sensitivity controls
after implementation where baseline behavior cannot reach the native branch
(for example, lose errno), then restore the exact source.
Do not run duplicate whole-stack CLI/broker tests solely for this fd-binding
change. Stop if tests require a production test-only API, relaxed concurrency,
fixed sleeps, or a second cleanup owner.

### 3. Validation, traceability, and completed-work review

Update README and mapping/docstrings with the implementation. Run the focused
module, lint/types, and spec/document gates below. Obtain a completed-work
cross-family review through `skills/call-agent/SKILL.md`; reconcile findings,
promotion baseline, evidence, and deviations. Mark this plan and its index row
`completed` together only after the implementation acceptance gates pass.
The plan-authoring phase left status draft; implementation promotes it.
Keep status promoted while required hosted/platform evidence remains pending.

## Verification Commands and Acceptance

Load the repo toolchain with `. ./.envrc`; never print its environment values.

```bash
./.venv/bin/python -m pytest tests/core/test_subprocess_runner.py
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
./.venv/bin/ruff format --check weft/core/runners/subprocess_runner.py weft/core/deferred.py tests/core/test_subprocess_runner.py
./.venv/bin/ruff check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
bin/check-doc-paths
git diff --check
```

Run default project concurrency, with no retries masking failures. Run real
Linux fallback cases on each Linux Python version in the existing matrix, and
existing subprocess tests on macOS/Windows to establish unchanged leaves.
Record the actual CI run and executed case results, not only workflow intent.
No claim of a full suite follows from these focused commands. If changed scope
or failures require wider checks, add them explicitly before claiming completion.

For this plan-only task, run the metadata/spec-hygiene tests, doc-path gate,
and diff check. Production runtime tests are implementation acceptance, not a
requirement to execute an unimplemented binding. The user approved baseline-versus-candidate
traceability comparison in this implementation session, superseding the initial
qualification stop gate. Use the sibling backstitch executable with `check`,
`--no-config`, `--spec-root docs/specifications`, `--plan-root docs/plans`,
`--code-root weft`, `--code-root tests`, and JSON output on both a git archive
of the baseline and the candidate. Compare multisets of issue signatures
(severity, code, path, section ID, symbol), excluding shifted source line
numbers. Require no new issue instances. Existing corpus repair remains out
of scope; do not suppress or relabel new findings to pass this comparison.
This is the accepted traceability gate for this change, not a claim that the
repository has passing corpus-wide traceability.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Review and Execution Log

- Author fresh-eyes review, 2026-11-05: corrected the inherited stale plan-count
  claim by deriving the directory count (69); clarified that strategy B keeps
  promotion and implementation in one atomic change; corrected the invariant
  reference to the broker-free worker boundary [IMPL.9]. Residual risk: exported
  libc symbols vary by distribution, so unavailable bindings must fail explicitly.
  External review remains required.
- Tier-1 inventory, 2026-11-05: Claude CLI liveness returned `PROBE-OK`;
  `--permission-mode plan --allowedTools Read,Grep,Glob` write-attempt probe
  refused repository write; `probe-write-test.txt` remained absent. A plan
  artifact was created outside the repository by the CLI probe. Claude is
  review-eligible (different family, harness-level containment).
- Cross-family plan review, 2026-11-05: Claude via
  `skills/call-agent/SKILL.md`, 540-second bounded invocation, exit 0, PASS.
  Reviewed verbatim plan, live code/specs, baseline and standing constraints.
  No blocking finding; nonblocking edits verified locally per runbook, with
  no second review round required. Verbatim findings and dispositions follow.
- The plan-authoring phase did not execute implementation or deployment;
  implementation evidence follows below.

### Cross-family findings (verbatim)

| ID | severity | location | finding | suggested disposition |
| --- | --- | --- | --- | --- |
| F1 | P3 | Header "Source specs"; Invariants bullet 1 | [IMPL.9] (`07-System_Invariants.md:898`) is about task worker lanes being broker-free. It does not own "one selector, no polling floor, no helper thread, no signal handler"; those come from [RM-5.2], [WT-2] item 4 and [ALT-WT-021]. Citing [IMPL.9] as their owner misleads traceability. | modify: cite [IMPL.9] only as "adds no broker access on the worker lane", or drop it from Source specs |
| F2 | P3 | Task 2 table, row "Real Linux native fd flags and close ownership" | Fd ownership after acquisition doesn't depend on which binding opened the fd. `_DescriptorExitSource.close`, `_PosixSelectorProcessIO.close` (`subprocess_runner.py:977-986`) and the cleanup on `__init__` failure (`:817`) are unchanged. The kernel always sets close-on-exec on a pidfd. So the registration-failure and close-on-exec checks only re-test existing behaviour. The EBADF-after-close check can also fail at random, because the fd number can be reused. | modify: replace the row with one leak check inside the real-libc supervisor test (count `/proc/self/fd` before and after); drop the registration-failure and EBADF checks |
| F3 | P3 | Task 2 table, row "Extend narrow error table" | If the native helper raises `OSError(code, strerror)` into the existing error handling (`:736-749`), running all 8 errnos on both bindings repeats the shared classification. The risks only the native path has are: -1 not turned into an exception, errno lost, the `OSError` constructor not picking the subclass (ESRCH→`ProcessLookupError`), and fd 0. | modify: native table = ESRCH, EPERM (one capability case), EMFILE (one pass-through case); the existing Python-binding tests (`test:2522`, `test:2634`) remain the proof of classification |
| F4 | P3 | Task 2 table, row "Missing dependency/symbol/loader cases" (loader part) | There is no catch around the loader for the "loader OSError is preserved" test to guard. `CDLL(None)` is `dlopen(NULL)`, which in practice cannot fail. The test would need a fake failing CDLL, and it would only catch a broad catch that someone adds later. | modify: keep the spec sentence; drop the loader-OSError test |
| F5 | P3 | Task 2 oracle "Hide only module-local Python wrapper" | `_Forwarding` (`test:157`) passes every attribute it lacks through to the real `os` via `__getattr__`. So it cannot make `hasattr(os, "pidfd_open")` false. The existing missing-wrapper test replaces `os` with `object()` (`test:2545`). That would break `os.close` and `os.strerror` on the real libc path. | accept, with a clarification: name a test-local forwarder that raises `AttributeError` for the hidden names, and forbid `os=object()` in the real-libc variant |
| F6 | nit | Proposed spec delta, paragraph 2 | Requiring "the Python executable/version" in the error makes diagnostic content part of the spec. CLAUDE.md lists log message formats as safe to change. | modify: the spec says "identifying the unavailable binding"; executable and version stay an implementation detail |
| F7 | nit | Proposed spec delta, paragraph 2, first sentence | "lacks os.pidfd_open and ctypes or libc's exported pidfd_open symbol is unavailable" can be read two ways. | modify: "…and either ctypes or libc's exported `pidfd_open` symbol is unavailable" |
| F8 | nit | Task 2, CI paragraph | "Without … capability-based skips" next to "report an unsupported test environment" reads as permission to skip the test. | modify: on Linux, a missing symbol makes the test fail rather than skip; that failure is reported as an unsupported environment |
| F9 | nit | Task 1 vs Promotion strategy B | Task 1's "record its promotion baseline identifier" sounds like a separate promotion commit. That contradicts the single atomic change. | modify: the identifier is the commit of the combined spec-and-code change, recorded in Task 3 |
| F10 | nit | Task 2 last row, "fd 0 is accepted with a fake fd owner" | This is underspecified. If the injected native call returns 0, closing the real `_DescriptorExitSource(0)` would close stdin. | accept, with a clarification: assert `fileno() == 0` on the returned source, and close it only through a patched `os.close` (`_patch_os`) |
| F11 | nit | README sync item | The libc route needs a libc that exports `pidfd_open`: glibc 2.36 or newer; musl doesn't export it. Operators need that concrete requirement. | modify: the README names glibc 2.36+ as the libc condition; the spec wording stays generic |

### Dispositions

| ID | Disposition and operative change |
| --- | --- |
| F1 | Accept: Source specs now names RM-5.2 only; IMPL.9 is confined to the unchanged broker-free worker boundary. |
| F2 | Modify: dropped extra fd-flag/registration-failure/EBADF tests; did not adopt process-wide fd counts, which can reflect unrelated opens. Native acquisition rejoins the existing descriptor owner, and real supervision exercises that route. |
| F3 | Accept: native errno conversion uses ESRCH/EPERM/EMFILE; existing Python cases retain shared classifier coverage. |
| F4 | Reject suggested deletion: the current acquisition block catches OSError around source opening. A loader OSError with a capability errno could therefore be misclassified if helper acquisition is placed there. Retain one injected loader-error proof for the explicitly promised acquisition/syscall separation. |
| F5 | Accept: specify a hiding forwarder that delegates other OS methods, and prohibit os=object() for real-libc supervision. |
| F6 | Accept: executable/version remain implementation diagnostic context, not required spec fields. |
| F7 | Accept: exact proposed text now says “either ctypes or libc's exported pidfd_open symbol”. |
| F8 | Accept: a missing native symbol on Linux fails the integration test; no capability skip. |
| F9 | Accept: promotion identifier is recorded for the combined change in Task 3, not a separate promotion commit. |
| F10 | Accept: fd-zero test patches only local os.close before source closure; real stdin is protected. |
| F11 | Modify: verified glibc 2.36 introduction from its primary release announcement; README will name that concrete availability example. Do not add an unverified universal musl claim or a version gate. |

### Plan-authoring verification

- Metadata and spec-hygiene tests: 6 passed using the repo virtualenv and default concurrency.
- `bin/check-doc-paths`: passed.
- `git diff --check`: passed.
- Backstitch qualification remains an explicit pre-implementation stop gate above; no passing traceability claim is made.

### Implementation preflight, 2026-11-05

- Comprehension: inherited descendants can hold stdout/stderr open after root
  exit, so pipe EOF cannot replace the independent pidfd wake. The supervisor's
  Popen.poll() reaps; cached returncode forbids numeric PID lookup, and live
  exit registration precedes the first poll.
- User explicitly authorized baseline-versus-candidate traceability comparison
  instead of a passing corpus-wide backstitch gate; corpus repair stays out of
  scope. This supersedes the earlier pre-implementation qualification stop gate.
- Qualified comparison invocation: sibling backstitch executable, check with
  --no-config, --spec-root docs/specifications, --plan-root docs/plans,
  --code-root weft, --code-root tests, --format json. Baseline git archive of
  HEAD and candidate both reported 36 errors, 1323 warnings, and 766 infos.
  Compare issue signatures, not only totals, after implementation.

### Implementation evidence, 2026-11-05

- Promoted [RM-5.2] and implemented `_linux_pidfd_opener` plus deferred
  `get_ctypes()`. The Python path remains preferred; loader errors precede the
  syscall classifier, and the native function retains its CDLL owner. The
  unchanged selector and fd owner handle both bindings. README and Unreleased
  changelog now describe the compatibility behavior.
- RED: repo-default pytest selection `-k 'linux_libc or missing_bindings'`
  produced 7 expected failures before production edits. In Linux Python 3.13,
  the real inherited-pipe libc case failed with the old missing-wrapper
  RuntimeError, not a setup error.
- GREEN: full subprocess module in init-equipped Linux containers, with
  project-default concurrency: Python 3.12.16, 3.13.18, and 3.14.10 each
  reported 84 passed, 8 Windows-only skipped. Each used glibc 2.41 and the
  actual libc symbol. These are local Linux matrix results, not hosted CI.
- Initial Linux container failures occurred in descendant cleanup because its
  PID 1 was sleep and left orphan zombies unreaped. Re-running the same module
  and concurrency with Docker --init passed; no tests, limits, or timeouts were
  relaxed to correct the container topology.
- macOS subprocess plus metadata/spec-hygiene selection reported 88 passed,
  10 platform skips before review nits; final reruns are recorded below.
- Sensitivity control in an isolated archive changed errno capture to 0:
  native ESRCH/EPERM/EMFILE cases each failed for their intended errno outcome;
  restoring exact candidate source produced 3 passed. The working source was
  not mutated for this control.
- Traceability: final qualified comparison retained baseline's 36 errors,
  1323 warnings, 766 infos, with no added or removed issue signatures. A new
  mapping warning during development was fixed by using path-qualified symbol
  mappings for `_open_child_exit_source` and `get_ctypes`, not suppressed.
- Whole-repository Ruff passed; mypy passed for the plan's complete target
  list (488 source files). Formatting, doc paths, and diff checks passed.
- Hosted CI and a Windows runtime execution remain unverified; no code was
  pushed and no ops service or installation was changed. Keep status promoted
  until the plan's hosted/platform acceptance evidence is recorded.

### Completed-work cross-family review (verbatim findings)

| ID | severity | location | finding | suggested disposition |
| --- | --- | --- | --- | --- |
| F1 | P3 | `tests/core/test_subprocess_runner.py:2592`, `:2613`, `:2655` (`raising=False` on `get_ctypes`) | `get_ctypes` now exists in the module, so `raising=False` only mattered for the baseline RED run. In the capability test it also makes the trap that forbids retrying through libc vacuous: if the import is renamed, monkeypatch quietly adds an unused attribute and the test still passes. The descriptor-zero test already patches without `raising=False`, so the file is inconsistent. | Drop `raising=False` in all three places. |
| F2 | P3 | `CHANGELOG.md` (not touched) | This change is visible to users: Python builds without the wrapper used to be rejected and now work, and the error text is different. Recent behavior changes (`0ff8cfbf`, `7130643b`, `14d84f29`) each added a CHANGELOG entry. The plan's file table leaves it out. | Add a "Changed" entry citing [RM-5.2], or record in the plan why it's omitted. |
| F3 | nit | `test_linux_pidfd_requirement_explains_missing_bindings` (`:2622-2623`) | The test pins `sys.executable` and the version string inside the error message. The plan (F6) says those fields are implementation detail, not contract, and CLAUDE.md lists message formats as safe to change. The test makes them a de facto contract. | Keep the binding-identity regex and the cause checks; drop the two executable/version asserts. |
| F4 | nit | `pidfd_binding` fixture ids (`:189`) | On macOS and BSD the `python` id actually exercises the kqueue leaf, not Python's pidfd wrapper, so the test ids are misleading in non-Linux runs. | Rename the ids to `default` / `libc`. |
| F5 | nit | `test_live_child_exit_source_is_registered_before_first_reaping_poll` with `pidfd_binding` | Registration order is decided in `_PosixSelectorProcessIO.__init__`, whichever binding is used. Once the inherited-pipe `libc` test proves the real fallback reaches the selector, this second variant adds little. The plan's table did ask for it, though, and it's cheap. | Optional: keep it as the plan requires, or drop the fixture here to cut duplicate work. |
| F6 | nit | `docs/specifications/06-Resource_Management.md` Related Plans | A blank line after the new bullet splits the list in two, and the new entry is placed first even though the existing entries run oldest to newest. | Remove the blank line and move the entry to the end of the dated bullets. |
| F7 | nit | `_open_child_exit_source` docstring (`:765`) | The re-wrapped line is about 92 characters, longer than the 88-character limit used elsewhere. E501 is ignored and `ruff format` doesn't reflow docstrings, so nothing will flag it. | Re-wrap it. |

Verdict: no blocker. Tier-1 Claude invocation used the verified read-only
posture from skills/call-agent/SKILL.md, with the plan and implementation diff
embedded at baseline 44d7f90050c559bda10cdd0b3360e09a553f338b; exit 0.

| ID | Disposition and operative change |
| --- | --- |
| F1 | Accept: removed permissive raising=False from the get_ctypes patches after the baseline RED proof; patches now require the real dependency seam. |
| F2 | Accept: added Unreleased Changed entry and CHANGELOG.md to the plan's file table. |
| F3 | Accept: retained cause and binding-identity assertions; dropped executable/version string assertions. |
| F4 | Accept: renamed fixture ids default/libc to describe non-Linux runs accurately. |
| F5 | Reject optional deletion: retain the cheap real-libc registration case to prove the new acquisition route preserves watch-before-reaping order; no extra orchestration or production seam is added. |
| F6 | Accept: keep the Related Plans list contiguous and put the new dated entry after the existing dated bullets. |
| F7 | Accept: rewrapped the changed docstring. |

Author fresh-eyes completion review: binding selection, errno/subclass
conversion, fd-zero success, existing fd ownership, platform isolation, and
spec/README/code traceability checked against the live diff. Residual limits:
ctypes and an exported libc symbol are required when Python omits its wrapper;
this does not bypass seccomp or old-kernel restrictions. No new theory
constraint or deferred production concern was introduced.

### Final local verification after review dispositions

- Linux subprocess module: 84 passed, 8 Windows-only skips on each of Python
  3.12.16, 3.13.18, and 3.14.10, with real libc variants executed. Docker init
  supplied normal orphan reaping; default test concurrency stayed unchanged.
- macOS subprocess plus metadata/spec-hygiene tests: 88 passed, 10 platform
  skips. Ruff format check passed for the changed Python files; repo-wide
  Ruff passed; mypy passed for 488 source files with the full planned targets.
- Doc-path and diff checks passed. Baseline-versus-candidate issue multisets
  are identical: 36 errors, 1323 warnings, 766 infos, with no new instances.
- Review dispositions required no second review round: verdict was no blocker
  and the changes did not expand runtime scope or reverse constraints.
- No commit, push, release, production task replay, or ops deployment was
  performed. Hosted Windows/CI evidence is the remaining acceptance item;
  status and index therefore remain promoted rather than completed.
