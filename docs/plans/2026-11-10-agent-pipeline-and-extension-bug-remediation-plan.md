# Agent Runtime, Pipeline Stage Validation, and Sandbox Runner Bug Remediation Plan

Status: completed
Source specs: `docs/program-theory.md` ([WT-1], [WT-2a.4], [WT-2a.9], [WT-2a.11], [WT-3], [WT-4.1], [WT-4.2], [WT-5], [ALT-WT-011], [ALT-WT-012], [WT-7]); `docs/specifications/12-Pipeline_Composition_and_UX.md` ([PL-2.7], [PL-4.3]); `docs/specifications/13-Agent_Runtime.md` ([AR-2.1], [AR-5], [AR-6]); `docs/specifications/08-Testing_Strategy.md` ([TS-1], [TS-3.1])
Superseded by: none

Class: 5 (`implementation with spec revision` across `provider_cli` persistent session isolation and context wiring, pipeline stage template validation, and `weft_docker` / `weft_microsandbox` runner execution; hardened per `docs/agent-context/runbooks/hardening-plans.md`).

## 1. Goal

Remediate the empirically verified, concretely reachable defects in Weft's delegated `provider_cli` agent runtime, pipeline stage template validator, and first-party sandbox runner extensions (`weft_docker` and `weft_microsandbox`) identified in the 2026-11-10 deep-dive review (`HEAD = dbbf5526`, `v0.9.124`) across three commit-isolated slices.

## 2. Source Documents

- **Program Theory (`docs/program-theory.md`)**:
  - `[WT-1]` (*Failures remain visible where they happened* — post-exit artifact copy-back failure on a `0`-exit guest command must fail visibly rather than returning empty stdout)
  - `[WT-2a.4]` (*Configuration is frozen at creation* — `TaskSpec.spec` sandbox/cwd/context settings remain fixed across every turn of a persistent agent session and cannot be silently ignored by pipeline compilation)
  - `[WT-2a.9]` (*One current contract* — update all Weft-owned agent runtime adapters, test fixtures, and session tests together)
  - `[WT-2a.11]` & `[WT-5]` (*One operator trust domain*; *Non-Goals and Boundary Refusals* — Weft adds no in-band multi-tenant queue auth, delegating optional resource/sandbox bounds to configured provider and runner options)
  - `[WT-3]`, `[WT-4.1]` (*Keep an identity you already have*), & `[WT-4.2]` (*One owner for each resource*)
- **Agent Context & Runbooks**:
  - `AGENTS.md`, `docs/agent-context/decision-hierarchy.md` (`[DOM-15]`), `docs/agent-context/engineering-principles.md`, `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/hardening-plans.md`, `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- **Governing Specifications**:
  - `docs/specifications/13-Agent_Runtime.md` `[AR-2.1]`, `[AR-5]`, `[AR-6]`
  - `docs/specifications/12-Pipeline_Composition_and_UX.md` `[PL-2.7]`, `[PL-4.3]`
  - `docs/specifications/08-Testing_Strategy.md` `[TS-1]`, `[TS-3.1]`

## 3. Context, Root-Cause Traces & Key Files

1. **`CodexProvider` ambient `resume --last` collision & dropped flags after turn 0 (`weft/core/agents/provider_cli/registry.py::CodexProvider`, `tests/fixtures/provider_cli_fixture.py::_run_codex`, `tests/core/test_provider_cli_session_backend.py`)**:
   - *Trace*: `CodexProvider.build_session_invocation` runs `codex exec resume --last` on `session.turn_index > 0`, sharing `~/.codex/sessions` across concurrent tasks/terminals, and gates `--sandbox`, `-C <cwd>`, and configured `codex exec` options behind `if session.turn_index == 0:` (`[AR-2.1]`, `[AR-6]` violation). Verified against `/opt/homebrew/bin/codex`: `codex exec` accepts top-level flags before `resume <SESSION_ID>`, and `--json` emits `{"type":"thread.started","thread_id":"<uuid>"}` on stdout while `-o <output_path>` writes the clean assistant message; `ProviderCLIBackendSession` (`weft/core/agents/backends/provider_cli.py`) already persists `provider_result.session_id` onto `self._session.session_id`. Both `tests/fixtures/provider_cli_fixture.py::_run_codex` and `tests/core/test_provider_cli_session_backend.py` currently encode the old `resume --last` / dropped-option behavior and must be updated in the same slice (`[WT-2a.9]`).
2. **`ClaudeCodeProvider` blocks implemented `workspace_access="none"` (`weft/core/agents/provider_cli/registry.py::ClaudeCodeProvider`)**:
   - *Trace*: `ClaudeCodeProvider.build_invocation` and `build_session_invocation` already implement `workspace_access == "none"`, yet `supported_workspace_access_modes = ("read-only",)` rejects `"none"` during `validate_tool_profile` (`[AR-5]`).
3. **`ProviderCLIBackend` & `probe-agents` resolve `.weft/agents.json` and `.weft/agent-health.json` from `cwd` instead of the selected Weft context (`weft/core/agents/backends/provider_cli.py`, `weft/core/agents/backends/llm.py`, `weft/core/agents/validation.py`, `weft/core/agents/runtime.py`, `weft/builtins/agent_probe.py`)**:
   - *Trace*: `ProviderCLIBackend` derives `spec_context` from `Path.cwd()` / `invocation.cwd` instead of `spec.get("weft_context")` and `get_runtime_identity().context_root`, breaking `.weft/agents.json` (`provider_cli.providers`) executable overrides and writing `.weft/agent-health.json` into `spec.working_dir` (or `OpenCodeProvider`'s session tempdir) when `working_dir` differs from the Weft project root. Meanwhile, `weft/builtins/agent_probe.py` calls raw `shutil.which` instead of `_resolve_provider_cli_command_path`. To honor `[WT-2a.9]`, add `spec_context: str | None = None` uniformly across `AgentRuntimeAdapter`, `LLMBackend`, and `ProviderCLIBackend` while retaining `_callable_supports_keyword(..., "spec_context")` solely for external `register_agent_runtime` plugins (matching `_callable_supports_bundle_root`).
4. **`_validate_stage_template` silently ignores `spec.parameterization` and `spec.run_input` (`weft/core/pipelines.py::_validate_stage_template`)**:
   - *Trace*: Pipeline stages receive work items from runtime edge queues rather than CLI submission adapters, so `spec.parameterization` and `spec.run_input` on a stage template are silently ignored during `compile_linear_pipeline` instead of being rejected up front (`[PL-2.7]`). (`PipelineEdgeTask` non-atomic `move_one` + checkpoint write is an explicit `[PL-4.3]` contract and is not modified.)
5. **`weft_docker` & `weft_microsandbox` reachable runtime defects**:
   - `extensions/weft_docker/weft_docker/plugin.py`: `DockerCommandRunner.run_with_hooks` leaves ephemeral `weft-build-<12hex>` image tags behind when `self._build is not None`, and `DockerCommandRunner._describe_runtime` calls `container.stats(stream=False)` whenever `container is not None`, risking a post-exit Docker API error when inspected after container exit.
   - `extensions/weft_docker/weft_docker/agent_runner.py`: `DockerProviderCLIRunner` omits `work_item_mounts` from `_path_mappings` (breaking container path rewriting on mounted work-item paths), lets explicit `self._network` override `max_connections == 0` instead of enforcing `"none"`, and skips `container.stop(timeout=2)` before cleanup on cancel.
   - `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`: `_copy_back_files` runs unconditionally before returning `MicrosandboxRunResult` and swallows `copy_to_host` exceptions with a warning, so when guest execution succeeded (`int(output.exit_code) == 0`) and required output copy-back failed, the caller misreports `"produced empty stdout"` instead of raising `MicrosandboxRuntimeError`. Conversely, when guest execution itself failed (`int(output.exit_code) != 0`, e.g., auth/startup failure before writing `-o output.txt`), raising from `_copy_back_files` would mask the guest's real exit code and `stderr`! Therefore `_copy_back_files` must fail closed (`strict=True`) when `int(output.exit_code) == 0` and run best-effort (`strict=False`) when `int(output.exit_code) != 0`. (Note: `TemporaryDirectory(dir="/tmp")` in `weft_microsandbox/plugin.py` was verified non-buggy in Round 1 `F10` and excluded.)

### Comprehension Question & Answer

- **Q: Why must `weft_microsandbox/_runtime.py` enforce strict `_copy_back_files` failure ONLY when `int(output.exit_code) == 0`?**
  - **A**: When a guest CLI fails before creating its `-o <output_path>` file (`int(output.exit_code) != 0`), raising `MicrosandboxRuntimeError` during `_copy_back_files` discards `output.exit_code` and `output.stderr_text`. Enforcing `strict=True` on `int(output.exit_code) == 0` and `strict=False` on `int(output.exit_code) != 0` surfaces copy-back failures on otherwise-successful runs while preserving authentic guest failure diagnostics on non-zero exits.

## 4. Invariants, Constraints, Rollout, and Rollback

- **Frozen per-session configuration & single adapter contract (`[WT-2a.4]`, `[WT-2a.9]`, `[AR-2.1]`, `[AR-6]`)**: Every turn of a persistent `CodexProvider` session enforces the same `--sandbox`, `-C <cwd>`, and configured `codex exec` options as turn 0 against the captured `thread_id`. Missing `session_id` on `turn_index > 0` or missing `thread_id` in `--json` output fails closed (`RuntimeError`). Both Weft-owned agent backends (`LLMBackend`, `ProviderCLIBackend`) accept `spec_context: str | None = None`.
- **Extension isolation (`[WT-3]`)**: Core `weft/` imports zero Docker or Microsandbox packages; suppression ratchet (`bin/ruff_suppression_index.py --check`) stays green in every commit.
- **Commit isolation & review schedule**: Each slice (Slices 1, 2, 3) lands as one atomic commit pairing code, tests, and Strategy B spec updates, followed by a cross-family completion review over the full diff.
- **Rollback**: Pure behavioral bug fixes with zero persistent schema changes; any slice reverts cleanly via `git revert <commit>`.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| `[PL-2.7]`, `[PY-3]` | Task 2.1 Outcome prose noted that `_validate_stage_template` raises `ValueError`, "which `prepare_pipeline` surfaces as `SubmissionValidationError`." | `_validate_stage_template` raises `ValueError`, and `prepare_pipeline` propagates `ValueError` directly without wrapping in `SubmissionValidationError` (`weft/commands/submission.py` untouched). | Per `docs/specifications/14-Python_API_Surfaces.md` `[PY-3]`, `prepare(...)` and `prepare_pipeline(...)` propagate raw `ValueError` / `TypeError` (matching existing `persistent`/`interactive`/`stream_output` stage rejections), whereas only `prepare_spec(...)` translates to `SubmissionValidationError`. | None (`[PY-3]` already governs). |
| `[AR-2.1]`, `[AR-5]` | Task 1.1 planned adding `"none"` to `ClaudeCodeProvider.supported_workspace_access_modes` based on the `elif tool_profile.workspace_access != "none":` branch in `build_invocation` / `build_session_invocation`. | Retained `ClaudeCodeProvider.supported_workspace_access_modes = ("read-only",)`, simplified the unreachable `elif tool_profile.workspace_access != "none":` branch to `else:`, updated `13-Agent_Runtime.md` Security Model prose to remove the stale `if workspace_access is not "none"` wording, and added a regression proving `workspace_access="none"` is rejected while `"read-only"` is enforced. | Post-implementation cross-family review (`01a1c886-be86-7703-8f25-6b02ae0a3179`) demonstrated that omitting `--dangerously-skip-permissions` (`claude -p <prompt>`) under `general` authority or passing `--permission-mode plan` under `bounded` authority does not disable Claude Code workspace tools, so advertising `"none"` violated `[AR-5]` by accepting an unenforced restriction. | Updated `13-Agent_Runtime.md` `[AR-2.1]` Security Model bullet in place; `claude_code` `workspace_access` remains `read-only` only. |

## Spec Baseline

- **Baseline Commit / Tag**: `dbbf5526` (`v0.9.124` on `main`, `2026-11-10`)
- **Plan Type**: `implementation with spec revision`
- **Promotion Strategy**: **Strategy B (atomic per slice)** for `docs/specifications/13-Agent_Runtime.md` (Slice 1) and `docs/specifications/12-Pipeline_Composition_and_UX.md` (Slice 2).
- **Promotion Baseline Identifier**: `Slices 1–3 atomic commits on main (2026-11-10)`

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/13-Agent_Runtime.md` | B — atomic in Slice 1 (+ completion review sync) | `[AR-2.1]` Codex persistent session `thread_id` & flag preservation, `claude_code` Security Model clarity; `## Related Plans` |
| `docs/specifications/12-Pipeline_Composition_and_UX.md` | B — atomic in Slice 2 | `[PL-2.7]` stage template rejection of `spec.parameterization` and `spec.run_input`; `## Related Plans` |

### 1. `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]` & `[AR-5]` — Slice 1)

- **Exact pre-change target A (`docs/specifications/13-Agent_Runtime.md:258-266`)**:
  - Starts with: `  container boundary, not the Codex sandbox, is the isolation there. Persistent`
  - Ends with: ``  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`)``
- **Clause audit A**: Replaces `codex exec resume --last` and turn-0-only flag gating with per-session `--json` `thread_id` capture and top-level `codex exec [EXEC_OPTIONS] resume <thread_id>` flag preservation across turns, retaining the `CODEX_HOME` (`auth.json` / `config.toml`) note verbatim.

Replacement (`docs/specifications/13-Agent_Runtime.md`):
```markdown
  container boundary, not the Codex sandbox, is the isolation there. Persistent
  Codex sessions pass `--json` alongside `-o <output_path>`, capture the
  created session's `thread.started` `thread_id` on turn 0, and invoke
  `codex exec [EXEC_OPTIONS] resume <thread_id> -o <output_path> <prompt>` on
  subsequent turns so `--sandbox`, `-C <cwd>`, and configured `codex exec`
  options remain enforced on every turn without sharing ambient `resume --last`
  state across concurrent tasks (`CODEX_HOME` still supplies `auth.json` and
  `config.toml` unless overridden via `spec.env` or container runtime
  isolation). (Plans:
  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`,
  `docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md`)
```

- **Exact pre-change target B (`docs/specifications/13-Agent_Runtime.md:238-245`)**:
  - Starts with: ``- **`claude_code`** (`ClaudeCodeProvider.build_invocation` /``
  - Ends with: ``  explicit `--mcp-config` file.``
- **Clause audit B**: Removes the misleading `if workspace_access is not "none"` phrasing now that the unreachable `!= "none"` branch is removed and `ClaudeCodeProvider` explicitly rejects `workspace_access="none"` until workspace-tool disablement is enforced.

Replacement (`docs/specifications/13-Agent_Runtime.md`):
```markdown
- **`claude_code`** (`ClaudeCodeProvider.build_invocation` /
  `build_session_invocation`): appends `--permission-mode plan` when
  `authority_class="bounded"` **or** the resolved tool profile's
  `workspace_access="read-only"`. Otherwise, it appends
  `--dangerously-skip-permissions` (`workspace_access="none"` is rejected by
  `ClaudeCodeProvider` validation until workspace-tool disablement is enforced).
  `bounded` also forces `strict_mcp_config=True` and routes all configured MCP
  servers through an explicit `--mcp-config` file.
```

### 2. `docs/specifications/12-Pipeline_Composition_and_UX.md` (`[PL-2.7]` — Slice 2)

- **Exact pre-change target (`docs/specifications/12-Pipeline_Composition_and_UX.md:423-430`)**:
  - Starts with: `- pipeline-compatible stages must not be interactive`
  - Ends with: ``  `compile_linear_pipeline` realizes concrete stage and edge TaskSpecs).``
- **Clause audit**: Preserves all existing `[PL-2.7]` bullets and implementation mapping verbatim; adds the stage-compatibility rule rejecting submission-only `spec.parameterization` and `spec.run_input` adapters plus plan backlink.

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):
```markdown
- pipeline-compatible stages must not be interactive
- pipeline-compatible stages must not use streaming output
- pipeline-compatible stages must not declare submission-only
  `spec.parameterization` or `spec.run_input` adapters
- successful pipeline-compatible stages must emit exactly one handoff payload

_Implementation mapping_: `weft/core/pipelines.py` (`_validate_stage_template`
enforces stage compatibility and rejects pipeline, internal edge, or
submission-only parameterized/`run_input` targets;
`compile_linear_pipeline` realizes concrete stage and edge TaskSpecs; plan:
`docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md`).
```

## 5. Tasks

### Slice 1: Agent Runtime Session Isolation, Authority Preservation & Context Propagation (`[AR-2.1]`, `[AR-5]`, `[AR-6]`)

1. **Task 1.1: Isolate Persistent `CodexProvider` Sessions by `thread_id`, Remove Unreachable `ClaudeCodeProvider` `workspace_access="none"` Branch, and Propagate Selected Weft Context**
   - **Outcome**:
     1. `CodexProvider.build_session_invocation` passes `--json` + `-o <output_path>`, places top-level `codex exec` flags (`--model`, `--profile`, `--oss`, `--local-provider`, `--config`, `--sandbox`, `--full-auto`, `--dangerously-bypass-approvals-and-sandbox`, `--add-dir`, `--skip-git-repo-check`, `--color`, `-C <cwd>`) before `resume <session.session_id>` on `session.turn_index > 0`, and `CodexProvider.parse_result` extracts `thread_id` (and `turn.completed` `usage` when present) from JSONL stdout while failing closed with `RuntimeError` if `session_id` or `thread_id` is missing on a session turn. One-shot `CodexProvider.build_invocation` remains unchanged.
     2. Update `tests/fixtures/provider_cli_fixture.py::_run_codex` to parse top-level `codex exec` options before `resume <session_id>`, accept `--json`, and emit `{"type": "thread.started", "thread_id": session_key}` on stdout, and update `tests/core/test_provider_cli_session_backend.py` to assert `provider_session_id` continuity AND preserved `sandbox`, `profile`, `color`, and `cwd` across turn 2+ (plus concurrent two-session isolation sharing `CODEX_HOME`).
     3. Keep `ClaudeCodeProvider.supported_workspace_access_modes = ("read-only",)` and remove the unreachable `!= "none"` branch (see `## Deviation Log`).
     4. Add `spec_context: str | None = None` to `AgentRuntimeAdapter.execute` (`weft/core/agents/runtime.py`), `LLMBackend` (`execute`, `start_session`, `validate` in `weft/core/agents/backends/llm.py`), and `ProviderCLIBackend` (`validate`, `validate_tool_profile`, `execute`, `start_session`, `_run_provider_invocation`, `ProviderCLIBackendSession` in `weft/core/agents/backends/provider_cli.py`), using `_callable_supports_keyword(..., "spec_context")` in `validation.py` and `runtime.py` only for custom third-party `register_agent_runtime` adapters. `ProviderCLIBackend` resolves `effective_spec_context = spec_context or (identity.context_root if (identity := get_runtime_identity()) is not None and identity.context_root else None) or str(Path.cwd())` for `.weft/agents.json` and `.weft/agent-health.json` while **preserving `invocation.cwd` untouched**; `weft/builtins/agent_probe.py` uses `_resolve_provider_cli_command_path`, and `_resolve_provider_cli_command_path` returns `None` on missing explicit relative paths instead of falling back to `shutil.which`.
   - **Files to touch**: `weft/core/agents/provider_cli/registry.py`, `weft/core/agents/backends/provider_cli.py`, `weft/core/agents/backends/llm.py`, `weft/core/agents/validation.py`, `weft/core/agents/runtime.py`, `weft/builtins/agent_probe.py`, `docs/specifications/13-Agent_Runtime.md` (§4c.1 + `## Related Plans`), `tests/fixtures/provider_cli_fixture.py`, `tests/core/test_provider_cli_session_backend.py`, `tests/core/test_provider_cli_backend.py`, `tests/core/test_agent_runtime.py`, `tests/core/test_agent_validation.py`, `tests/core/test_builtin_agent_probe.py`.
   - **Done when**: `pytest tests/core/test_provider_cli_session_backend.py tests/core/test_provider_cli_backend.py tests/core/test_agent_runtime.py tests/core/test_agent_validation.py tests/core/test_builtin_agent_probe.py -q` passes.

### Slice 2: Pipeline Stage Template Compatibility Validation (`[PL-2.7]`)

2. **Task 2.1: Reject Submission-Only `spec.parameterization` and `spec.run_input` on Pipeline Stage Templates**
   - **Outcome**: `weft/core/pipelines.py::_validate_stage_template` raises a clear `ValueError` when a stage template declares `spec.parameterization` or `spec.run_input` (`[PL-2.7]`), which `prepare_pipeline` surfaces as `SubmissionValidationError`.
   - **Files to touch**: `weft/core/pipelines.py`, `docs/specifications/12-Pipeline_Composition_and_UX.md` (§4c.2 + `## Related Plans`), `tests/core/test_pipelines.py`.
   - **Constraints**: Do **not** alter `PipelineEdgeTask._handle_work_message` (`[PL-4.3]`).
   - **RED -> GREEN tests**: Assert `compile_linear_pipeline` rejects stage templates carrying `spec.parameterization` or `spec.run_input` with `ValueError` naming the stage and unsupported submission-only adapter.
   - **Done when**: `pytest tests/core/test_pipelines.py -q` passes.

### Slice 3: `weft_docker` and `weft_microsandbox` Reachable Runtime Fixes

3. **Task 3.1: Fix `weft_docker` One-Shot Build Tag Cleanup, Safe `_describe_runtime` Stats, and `DockerProviderCLIRunner` Mount/Network/Cancel Handling**
   - **Outcome**:
     1. `DockerCommandRunner.run_with_hooks` removes ephemeral `weft-build-<12hex>` image tags (`client.images.remove(image=image_ref, force=True)` best-effort in `finally:`) when `self._build is not None`, while leaving pre-existing user `image` refs untouched.
     2. `DockerCommandRunner._describe_runtime` calls `container.stats(stream=False)` only when `state == "running"` and suppresses `DockerException` / `OSError` best-effort so exited containers never fail `run_with_hooks`.
     3. `DockerProviderCLIRunner` includes `work_item_mounts` in `_path_mappings`, enforces `max_connections == 0 -> "none"` in `_network_mode` (and rejects explicit `network` conflicting with `max_connections == 0` during validation), and calls `container.stop(timeout=2)` before container removal on cancellation.
   - **Files to touch**: `extensions/weft_docker/weft_docker/plugin.py`, `extensions/weft_docker/weft_docker/agent_runner.py`, `extensions/weft_docker/tests/test_docker_plugin.py`, `extensions/weft_docker/tests/test_agent_runner.py`.
   - **Done when**: `pytest -m "" extensions/weft_docker/tests -q` passes.

4. **Task 3.2: Fail Closed on `weft_microsandbox` Copy-Back Errors When Guest Execution Succeeded While Preserving Non-Zero Guest Failures**
   - **Outcome**: In `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`, when `int(output.exit_code) == 0`, `_copy_back_files(..., strict=True)` raises `MicrosandboxRuntimeError` if `copy_to_host` fails on any `spec.copy_back` file instead of swallowing it and misreporting `"produced empty stdout"`; when `int(output.exit_code) != 0`, `_copy_back_files(..., strict=False)` logs best-effort without raising so the guest's original non-zero `exit_code` and `stderr` are returned intact.
   - **Files to touch**: `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`, `bin/ruff_suppression_index.py` (if `# noqa: BLE001` count changes), `extensions/weft_microsandbox/tests/test_microsandbox_agent_runner.py`, `extensions/weft_microsandbox/tests/test_runtime_adapter.py`.
   - **RED -> GREEN tests**: Add regressions for both cases: (a) `exit_code == 0` with failing `copy_to_host` raises `MicrosandboxRuntimeError`; (b) `exit_code == 2` with missing output file (`copy_to_host` raising `FileNotFoundError`) returns `exit_code == 2` and the guest's original `stderr` rather than masking it.
   - **Done when**: `pytest -m "" extensions/weft_microsandbox/tests -q` and `python bin/ruff_suppression_index.py --check` exit `0`.

## 6. Testing Plan

- Updated `tests/fixtures/provider_cli_fixture.py` and `tests/core/test_provider_cli_session_backend.py` plus `test_provider_cli_backend.py`, `test_agent_runtime.py`, `test_agent_validation.py`, and `test_builtin_agent_probe.py`; real `compile_linear_pipeline` tests in `tests/core/test_pipelines.py`; unit and adapter tests in `extensions/weft_docker/tests/` and `extensions/weft_microsandbox/tests/`.

## 7. Verification and Gates

```bash
./bin/mypy-check
./.venv/bin/ruff check weft tests bin extensions integrations
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python bin/check-doc-paths --self-test && ./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m pytest -m "" tests/specs/ tests/core/test_provider_cli_session_backend.py tests/core/test_provider_cli_backend.py tests/core/test_agent_runtime.py tests/core/test_agent_validation.py tests/core/test_builtin_agent_probe.py tests/core/test_pipelines.py extensions/weft_docker/tests extensions/weft_microsandbox/tests -q
```

## 8. Independent Review Loop

### 8.1 Author's `[WT-7]` Theory Fit Account

- **Verdict**: Fits with strain.
- **Rests on**:
  - `[WT-2a.4]` (*Configuration is frozen at creation*), `[WT-3]`, & `[WT-4.1]` (*Keep an identity you already have*): persistent `CodexProvider` sessions capture and resume their own `thread_id` while enforcing the TaskSpec's `--sandbox`, `-C <cwd>`, and options on every turn (`[AR-2.1]`, `[AR-6]`), `ProviderCLIBackend` binds `.weft/agents.json` and `.weft/agent-health.json` to the selected Weft context rather than execution `cwd`, and `_validate_stage_template` rejects submission-only stage adapters at compile time (`[PL-2.7]`).
  - `[WT-1]` (*Failures remain visible where they happened*): `weft_microsandbox` raises `MicrosandboxRuntimeError` when artifact copy-back fails after `exit_code == 0`, while preserving the guest's real `exit_code` and `stderr` when `exit_code != 0`.
  - `[WT-2a.9]` (*One current contract*), `[WT-4.2]`, & `[WT-5]`: `AgentRuntimeAdapter`, `LLMBackend`, and `ProviderCLIBackend` all accept `spec_context`, `provider_cli_fixture.py` and `test_provider_cli_session_backend.py` move in lockstep with `CodexProvider`, and `weft_docker` cleans up its owned ephemeral build tags and enforces `max_connections == 0 -> "none"`.
- **Refuses**:
  - Ambient host-global session continuation (`codex exec resume --last`), refused by `[WT-4.1]` and `[AR-6]`.
  - `[ALT-WT-011]` (no hidden preflight probes on the ordinary execution path; locating `.weft/agent-health.json` remains advisory recording, never admission gating).
  - `[ALT-WT-014]` (sandbox/network enforcement honors declared runner/provider limits without inventing multi-tenant queue auth).
- **Predicts**:
  - Two interleaved `codex` sessions sharing `CODEX_HOME` retain separate conversation state and turn-2+ `--sandbox`/`-C` flags; selecting context `A` while running in `cwd` `B` reads `A/.weft/agents.json` and writes `A/.weft/agent-health.json` while executing in `B`; `weft_microsandbox` raises `MicrosandboxRuntimeError` on `exit_code == 0` copy-back failure and preserves `exit_code == 2` + `stderr` on guest failure.
- **Fits worst**:
  - `[WT-2a.9]` / `[ALT-WT-012]` (*One current contract; no backward-compatibility code for changed contracts*): retaining `_callable_supports_keyword(..., "spec_context")` in `validation.py` and `runtime.py` accommodates third-party `register_agent_runtime` test/extension callables that omit optional `spec_context` (matching existing `_callable_supports_bundle_root`), whereas every Weft-owned backend (`LLMBackend` and `ProviderCLIBackend`) is updated to the single current signature.

### 8.2 Blind Other-Family `[WT-7]` Fit Account & Divergence Classification

- **Reviewer family & isolated invocation (`2026-11-10`)**: OpenAI Codex CLI (`codex exec --skip-git-repo-check -s read-only -C /tmp/weft-wt7-blind-6cb1b97f/p2 -c 'model_reasoning_effort="high"'`, thread `01a1c763-b87d-7eb1-b4ec-5df966e43530`), given **only** `program-theory.md` and `boundary.diff`.
- **Blind account summary**:
  - *Verdict*: `fits with strain` (valid 5-part account).
  - *Rests on*: `[WT-3]` and `[WT-4.1]` (*Keep an identity you already have*) for per-session `thread_id`; `[WT-2a.4]` for fixed settings across turns and selected-context resolution; `[WT-5]` and `[WT-4.2]`/`[WT-4.4]` for runner sandbox bounds and Docker build-tag cleanup; `[WT-1]` for visible copy-back failures.
  - *Refuses*: Ambient `resume --last` (`[WT-4.1]`), silent success on failed artifact delivery (`[WT-1]`), hidden preflight (`[ALT-WT-011]`), and caller queue auth (`[WT-5]`, `[ALT-WT-014]`).
  - *Predicts*: Interleaved `codex` sessions sharing `CODEX_HOME` stay isolated with preserved flags; context `A` vs `cwd` `B` separates config/health from execution `cwd`; Docker mounts/network/stats/tag cleanup and Microsandbox copy-back behave as contracted; pipeline validation rejects `parameterization`/`run_input`.
  - *Fits worst*: `[WT-2a.9]` / `[ALT-WT-012]` on `_callable_supports_keyword(..., "spec_context")` potentially preserving two callable shapes across Weft-owned adapters.
- **Divergence classification**:
  - **Change deficiency resolved in Plan 2**: The blind account's `[WT-2a.9]` observation exposed that our initial draft left `LLMBackend` and `AgentRuntimeAdapter` on the old signature without `spec_context`. Task 1.1 now updates `AgentRuntimeAdapter`, `LLMBackend`, and `ProviderCLIBackend` together (`[WT-2a.9]`), confining `_callable_supports_keyword` strictly to external `register_agent_runtime` plugins.

### 8.3 Cross-Family Plan Review Log

- **Post-split Round 1 (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `BLOCKED` (`F4`, `F5`, plus `F8`, `F9`, `F10` affecting Plan 2).
  - **Dispositions (all verified against live code and applied in place)**:
    - `F4` (`[P2]`, Task 1.1 omitted `tests/core/test_provider_cli_session_backend.py` and `tests/fixtures/provider_cli_fixture.py::_run_codex`): **Accept (`fixed in plan`)** — added both files to §3, Task 1.1, §6, and §7 gates.
    - `F5` (`[P2]`, Task 3.2 unconditional `_copy_back_files` raise masks non-zero guest failures): **Accept (`fixed in plan`)** — gated strict `MicrosandboxRuntimeError` on `int(output.exit_code) == 0` while keeping `strict=False` on `int(output.exit_code) != 0` with regressions for both.
    - `F8`, `F9`, `F10` (`[P3]`, removed `[WT-2a.5]` citation, replaced nonexistent `test_microsandbox_plugin.py` with `test_microsandbox_agent_runner.py`, and dropped the non-buggy `weft_microsandbox` `/tmp` change): **Accept (`fixed in plan`)**.
- **Post-split Round 2 Scoped Verification (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -C /Users/van/Developer/weft resume -c 'sandbox_mode="read-only"' -c 'model_reasoning_effort="high"' 01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `PASS` for Plan 2 (`F4`, `F5`, `F8`–`F10`, and §8.1/§8.2 `[WT-7]` fit accounts verified aligned with `docs/program-theory.md`; suite-wide `Verdict: PASS` confirmed in Round 3).

### 8.4 Post-Implementation Completion Review

- **Reviewer family**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c886-be86-7703-8f25-6b02ae0a3179`, `2026-11-10`).
- **Round 1 dispositions (verified with `RED -> GREEN` regressions)**:
  1. `ClaudeCodeProvider` accepting `workspace_access="none"` without disabling workspace tools (`[P2]`): **Accept (`fixed`)** — retained `supported_workspace_access_modes = ("read-only",)`, simplified the unreachable `!= "none"` branch to `else:`, synchronized `13-Agent_Runtime.md` (`238-245`, `649`), and added `test_validate_agent_tool_profile_rejects_claude_workspace_access_none`.
  2. `_resolve_provider_cli_command_path` falling back to `shutil.which` on missing context-relative paths (`[P2]`): **Accept (`fixed`)** — returned `None` immediately when an explicit relative path does not exist under `spec_context`, with regressions in `test_provider_cli_backend.py` and `test_builtin_agent_probe.py`.
- **Round 2 scoped verification (`01a1c886-be86-7703-8f25-6b02ae0a3179`)**: Both fixes and falsifiable regressions verified with zero remaining findings; Task 1.1 outcome 3 aligned during closeout.
- Final Completion Review Verdict: APPROVE

## 9. Out of Scope

- Core control-plane, process-tree reap ownership, and Manager drain fixes (`docs/plans/2026-11-10-deep-dive-reachable-bug-remediation-plan.md`).
- Extension `py.typed` markers and `mypy` test-suite fixes (`docs/plans/2026-11-10-extension-typing-and-spec-sync-plan.md`).
- `PipelineEdgeTask` checkpoint fault handling (`[PL-4.3]`) and `weft_microsandbox` `/tmp` tempdir prefix (`F10`).

## 10. Fresh-Eyes Review

- Completed post-split author fresh-eyes pass and Round-1/2 disposition audit (`2026-11-10`): verified `provider_cli_fixture.py` / `test_provider_cli_session_backend.py` inclusion, `LLMBackend` + `ProviderCLIBackend` `spec_context` parity, `exit_code == 0` vs `!= 0` copy-back handling, and byte-for-byte `Replacement (...)` matches.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-10 | `dbbf5526` (post-split R1–R2) | §2, §3, Tasks 1.1 & 3.2, §7, §8 | Resolved Codex Round-1 `F4`, `F5`, `F8`–`F10` and blind `[WT-7]` `[WT-2a.9]` `LLMBackend` parity (`01a1c763-b87d-7eb1-b4ec-5df966e43530`). | Completed (`Verdict: PASS` on `01a1c763-c263-7b31-96a5-17058e503b35`) |
| 2026-11-10 | `31c5253b` + worktree (completion R1–R2) | Deviation Log, §4c.1 B, Task 1.1, §8.4, `registry.py`, `13-Agent_Runtime.md`, tests | Retained `ClaudeCodeProvider` rejection of unenforced `workspace_access="none"` and prevented `_resolve_provider_cli_command_path` from falling back to `shutil.which` on missing context-relative paths (`01a1c886-be86-7703-8f25-6b02ae0a3179`). | Completed (`APPROVE` on `01a1c886-be86-7703-8f25-6b02ae0a3179`) |
