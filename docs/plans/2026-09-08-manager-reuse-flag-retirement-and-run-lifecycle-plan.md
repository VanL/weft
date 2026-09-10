# Manager Reuse Flag Retirement and Run Lifecycle Completion Plan

Status: draft
Source specs: docs/specifications/00-Quick_Reference.md (environment table row); docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/03-Manager_Architecture.md [MA-1.5]; docs/specifications/07-System_Invariants.md [MANAGER.7], [MANAGER.8]
Superseded by: none

Class: 5 — removes the `WEFT_MANAGER_REUSE_ENABLED` environment row from the
Quick Reference table, rewrites the normative `weft run` interactive-exit
paragraph in [CLI-1.1.1], extends the [PY-2] `RunSession` member list, and
updates [MF-3] and [SB-0.4] implementation mappings that name symbols this plan
deletes. Plan type: implementation with spec revision. Promotion strategy
(writing-plans.md §4d, definitions at :425-450): **D** for the two
[CLI-1.1.1] clarifications (manager end-of-life bullet; the stale
"presentation callbacks in the command layer" sentence) — no code cites new
behavior; **B (atomic)** for everything code-coupled — the Quick Reference row
removal lands with the REMOVED rule (task 3), the [CLI-1.1.1] interactive-exit
paragraph and the [PY-2] `request_exit` sentence land with the port (task 4b),
and the [MF-3]/[SB-0.4] mapping edits land with the deletion (task 4c). The
hardening-plans checklist applies: a risky trigger fires (a public contract /
persisted config key is removed — the retired env var fails fast at
`load_config()` — and the change moves an execution path's exit ladder across
CLI, command, and task-control contexts). `hardening: applied`.

## 1. Goal

Retire `WEFT_MANAGER_REUSE_ENABLED` completely and finish the `weft run`
lifecycle migration that stalled at commit `8cc12299` (2026-08-12, "complete
public command API surface"). Three coupled pieces:

1. **The reuse flag is dead.** Since `8cc12299`, `weft/cli/app.py::run` calls
   `commands.cmd_run(..., wait=wait)`, and `cmd_run` always calls
   `execute_run(..., wait=False, ..., session_wait_requested=wait)`
   (`weft/commands/run.py:1614-1644`; `wait=False` at :1632; the session is
   returned at :1644). The only reader of `WEFT_MANAGER_REUSE_ENABLED` is the
   `finally` block of `_run_with_managed_execution` (:200-210), whose condition
   `not failed and started_here and wait and not reuse_enabled` can never be
   true because every caller passes `wait=False` (`_execute_inline` :1218,
   `_execute_spec_via_manager` :1304, `_execute_pipeline` :1387; the
   manager-backed flow returns at :178 before the `finally` can stop anything).
   Reproduced at `178e3a34`: the public route makes zero `stop_manager` calls;
   only a direct legacy `execute_run(wait=True)` call makes one. Owner decision
   (Van, 2026-09-08): "WEFT_MANAGER_REUSE_ENABLED was only for testing. If we
   are not using it, we delete it." Retire the flag; do not restore the old
   `started_here` teardown on the public path; make both stale-key entry points
   fail at the CLI boundary with a one-line diagnostic (no traceback).

2. **The specified interactive exit ladder exists only on a dead path.**
   [CLI-1.1.1] (10-CLI_Interface.md:295-302) specifies STOP for the
   `INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget, then KILL, then a bounded wait
   — implemented by `_InteractiveRunLifecycle.request_exit`
   (`weft/commands/run.py:700-740`), which production has not run since
   `8cc12299`. The F5 correction of 2026-09-08 (`178e3a34`) touched only that
   dead path (two hunks: import :33, `request_exit` :697-728; zero touches to
   `drive_interactive_session`, `_LiveRunSession`, `cmd_task_stop`,
   `stop_tasks`). The live path (`weft/cli/run.py::drive_interactive_session`
   :149-181 → `_LiveRunSession.stop()` :411-415 → `cmd_task_stop`) sends STOP
   with runner-stop fallback but **no KILL and no bound**: after `stop_tasks`
   returns, the adapter blocks in `output_thread.join()` (:181, no timeout) and
   `consume_run_session` in `session.wait()` (`await_task_result(timeout=None)`).
   On EOF the live path only writes `{"close": true}` (:171-173 → :403-409), and
   the task side only closes the child's stdin (`weft/core/tasks/interactive.py:192-201`,
   no grace timer, no terminate) — an EOF-ignoring child hangs the CLI
   indefinitely today. Under the owner's no-capability-diminution rule this
   plan **ports the specified ladder to the live path** (one implementation,
   in the command-layer session, reusing `cmd_task_stop`/`cmd_task_kill`) rather
   than reconciling the spec down to the live behavior, then deletes the dead
   private stack (`execute_run(wait=True)`, `_run_with_managed_execution`'s wait
   branch, `_wait_for_inline_completion`, `_wait_for_task_completion`,
   `_run_interactive_session`/`_prompt`/`_piped`, `_InteractiveRunLifecycle`,
   `weft/commands/interactive.py::InteractiveStreamClient`).

3. **Intentional `:quit` exit code.** Before `8cc12299` the CLI adapter called
   `execute_run` directly (`8cc12299^:weft/cli/run.py:68`) and an intentional
   `:quit`/`:exit` was normalized to `("completed", None)` → exit 0
   (`outcome(quit_requested=...)`, now `weft/commands/run.py:774-780`; origin
   commit `08f77f42`, 2026-04-08). The live path returns `cancelled` and
   `render_run_result` maps that to "Task cancelled" / exit 1
   (`weft/cli/run.py:103-121`). That is an unreviewed class-D regression; this
   plan restores exit 0 for `:quit`/`:exit` in the adapter (Open Owner
   Question 2, recommended default: restore). **EOF is not part of that
   restoration**: the retired prompt loop set `quit_requested = True` only for
   `:quit`/`:exit` (`weft/commands/run.py:922-926`); on `EOFError` it merely
   called `close_input()` (:916-918) and then waited without a budget, so an
   EOF that ended `cancelled`/`killed` never rendered as success. Treating
   EOF-at-the-prompt like `:quit` is **new policy**, escalated as Open Owner
   Question 5 (recommended default: same as `:quit`, for consistency).

Neither change alters runtime manager lifetime under the shipping default
configuration: the default was already `reuse=True` (manager stays alive), and
**all existing manager lifecycle rules remain in force** — [MA-1.5] idle
timeout, control/signal drain ([MANAGER.7], 03-Manager_Architecture.md:568-573),
supersession and leadership yield ([MANAGER.8]/[MANAGER.8a]), and explicit
`weft manager stop`. The only statement this plan promotes is that **task
completion never causes `weft run` to tear down its manager**.

## 2. Source Documents

- docs/specifications/00-Quick_Reference.md — Environment Variables table, row
  `WEFT_MANAGER_REUSE_ENABLED` (line 184): "Whether CLI-started managers stay
  alive after task completion." (removed by this plan). `## Related Plans` at
  :238 (backlink added).
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1] `run` — the stale
  sentence at :107-108 ("Interactive prompt mode still has presentation
  callbacks in the command layer because the prompt loop is not yet a public
  `WeftClient.run()` API."); the "Current behavior" list (:110-121); the
  interactive-exit paragraph plus its Implementation/Correction lines
  (:295-302) that name `INTERACTIVE_STOP_COMPLETION_TIMEOUT` and
  `_InteractiveRunLifecycle.request_exit`. [CLI-1.2.3] governs `task stop` /
  `task kill` control convergence, which the port reuses. `## Related Plans`
  at :1032.
- docs/specifications/05-Message_Flow_and_State.md [MF-3] — the control-producer
  implementation-mapping sentence (:266-268) that names
  `weft/commands/run.py::_InteractiveRunLifecycle._send_control`. `## Related
  Plans` at :1525.
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4] — the direct-queue
  owner implementation mappings (:9-14, :587-596) and the "interactive queue
  client that owns its own task-local inbox lifecycle" bullet (:645) that name
  `weft/commands/interactive.py`. `## Related Plans` at :724.
- docs/specifications/14-Python_API_Surfaces.md [PY-1] (:15-17: "Names not
  exported there, including `weft.core.*`, helpers, constants, command leaves,
  and `execute_run`, are private.") and [PY-2] (:83-86: "`RunSession` exposes
  `tid`, `events() -> CommandStream[TaskEvent]`, `send_input(text)`,
  `close_input()`, `stop() -> TaskControlResult`, `wait(timeout=None) ->
  RunExecutionResult`, and idempotent `close()`. Close releases owned resources
  but does not cancel the task."). This plan keeps every listed member's
  semantics and adds one member (`request_exit`). `## Related Plans` at :246
  (backlink added, task 4b).
- docs/specifications/03-Manager_Architecture.md [MA-1.5] (:206-212) — idle
  timeout (`idle_timeout` metadata, else `WEFT_MANAGER_LIFETIME_TIMEOUT`,
  default 600 s at `_constants.py:1864`); drain/supersession text at :555-575.
- docs/specifications/07-System_Invariants.md [MANAGER.7] (:754-755, graceful
  drain on termination signals) and [MANAGER.8] (:756-770, leadership yield /
  supersession) — cited so the promoted text does not contradict them.
- Historical (non-normative) plans, cited as evidence of decisions only:
  - `docs/plans/2026-08-12-public-api-surface-remediation.md` — the migration
    that made `cmd_run` return a `_LiveRunSession` and left `execute_run(wait=True)`
    unreachable.
  - `docs/plans/2026-08-10-interactive-session-lifecycle-refactor-plan.md` — the
    private interactive-lifecycle owner being retired here.
  - `docs/plans/2026-09-08-complexity-review-corrections-plan.md` — its F5 item
    added `test_interactive_exit_uses_full_stop_budget_before_escalation` to the
    (already-dead) `_InteractiveRunLifecycle.request_exit`; the behavior it
    pins is ported here (task 4).
  - `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md:876`
    — a stale "Stop if ... reuse ineffective" note; the flag became ineffective
    via `8cc12299`, not monitor supervision.
- Format/standards guidance: `CLAUDE.md` §1.1 and §4;
  `docs/agent-context/runbooks/writing-plans.md` (strategy definitions :425-450);
  `docs/agent-context/runbooks/hardening-plans.md`;
  `docs/plans/2026-08-31-collation-store-toggle-removal-plan.md` (Class-5
  exemplar: its task 4 REMOVED-rule + dual-rejection-site pattern and its Review
  Record format are reused here).

## 3. Context and Key Files

### 3a. The reuse flag (finding 19)

Files to modify:
- `weft/_constants.py`
  - `WEFT_MANAGER_REUSE_ENABLED: Final[bool] = True` and its docstring (:1880) — delete.
  - Loader entry in `_load_weft_env_vars()` — the `"WEFT_MANAGER_REUSE_ENABLED": _load_weft_env_value(...)` block (:2885-2889) — delete.
  - Process-environment removed-key rejection: `_load_weft_env_vars()` rejects removed task-monitor env vars via the `removed_task_monitor_env` dict + loop (:2796-2817, raise at :2817). Add a **separate** general removed-env map next to it (do **not** rename the task-monitor-specific dict) that rejects `WEFT_MANAGER_REUSE_ENABLED` with the migration message.
  - In-process override rejection: the `_WEFT_OVERRIDE_RULES` entry
    `"WEFT_MANAGER_REUSE_ENABLED": _OverrideRule(kind=_OverrideKind.BOOLISH, parser=_parse_bool)` (:3203-3206) → replace with
    `_OverrideRule(kind=_OverrideKind.REMOVED, removed_message=<message>)` following the existing REMOVED pattern (:3296-3306; the rule raises at :3145).
  - Define one module constant for the message so both rejection sites are byte-identical (tests assert exact equality).
  - **Boundary-safe exception (Codex round-1 finding 1, reproduced).** Both
    shared REMOVED sites raise bare `ValueError` today (:2817, :3145). The CLI
    bootstrap (`weft/bootstrap.py:104-116`) catches only
    `simplebroker.ext.InvalidConfigError`, so a removed key renders a full
    Python traceback with exit 1 — reproduced at `178e3a34` for
    `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED=0` via process env and via
    `WEFT_ENV_FILE` (`weft status`, `weft --version`). Fix: add
    `WeftConfigError(WeftError, ValueError)` to `weft/_exceptions.py`
    (`_exceptions.py` imports only `typing`, so `_constants.py` importing it
    creates no cycle); raise it at the two shared REMOVED sites (:2817 and
    :3145 — the four existing removed keys inherit the fix; existing
    `pytest.raises(ValueError)` assertions stay green by inheritance); extend
    the `bootstrap.main` catch to `(InvalidConfigError, WeftConfigError)` and
    render `f"{PROG_NAME}: {exc}\n"` exactly as the existing branch does
    (:114-116). Do not widen the catch to `ValueError` (that would hide real
    bugs) and do not touch the parser `ValueError`s for malformed values
    (e.g. `WEFT_MANAGER_LIFETIME_TIMEOUT=bogus` also tracebacks today — out of
    scope, §9).
- `weft/bootstrap.py` (:104-116): the catch extension above; nothing else.
- `weft/_exceptions.py`: the one new class.
- `weft/commands/run.py`
  - `_run_with_managed_execution` (:146-210): delete parameters `wait`, `reuse_enabled`, `wait_for_completion`; delete the `if not wait: return ...` early return (:178-181) — the function now always returns the immediate result; delete the wait branch (:183-193); delete the `finally:` reuse-stop block (:200-210). **Keep** the `except Exception:` block (:195-198) that stops a `started_here` manager on submission failure — evidence finding 01 confirms it is live on the public path.
  - `_execute_inline` (:1173-1175, :1227), `_execute_spec_via_manager` (:1289, :1313), `_execute_pipeline` (:1385, :1398): delete the three `reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))` reads and the `reuse_enabled=` kwargs.
- `tests/helpers/weft_harness.py` (:669): delete `"WEFT_MANAGER_REUSE_ENABLED": "0"` from `_patch_environment` overrides. **Required**, not cosmetic: once the key is REMOVED, leaving it exported makes every harness test fail at `load_config()`. The harness already force-stops managers at close (`weft_harness.py:1024-1032`), so nothing depends on the flag's effect.
- `tests/helpers/long_session_utils.py` (:45): delete `env["WEFT_MANAGER_REUSE_ENABLED"] = "1"`.
- `tests/cli/test_cli_run.py` (:2917, :2956, :2999): delete the three `env["WEFT_MANAGER_REUSE_ENABLED"] = "1"` assignments, and the `"reuse_enabled": env.get("WEFT_MANAGER_REUSE_ENABLED")` diagnostic key in `_raise_parallel_manager_reuse_failure` (:339). Leave the `PARALLEL_MANAGER_REUSE_*` / `*_manager_reuse_*` symbol and test names unchanged — they describe parallel-manager **adoption/convergence** behavior, not the flag (§9).
- `tests/system/test_constants.py`: delete the `WEFT_MANAGER_REUSE_ENABLED` import (:84), the `weft_keys` set entry (:1251), and the defaults assertion (:1286); replace `test_manager_reuse_env` (:770-777) with a removed-var rejection test (task 3).
- `tests/cli/test_env_file_bootstrap.py`: add two subprocess tests modeled on `test_invalid_broker_config_is_safe_before_cli_import` (:216-231) — process-env form and `WEFT_ENV_FILE` form (task 3).
- `docs/agent-context/runbooks/testing-patterns.md` (:30): the example `./.venv/bin/python -m pytest tests/core/test_manager.py -k manager_reuse -q` collects zero tests (stale pointer). Replace it with a real selector or delete the line.
- `docs/specifications/00-Quick_Reference.md` (:184) and `README.md` (:1325): remove the env row / bullet.

Sibling embedders: `../engram/engram/_constants.py` maps
`ENGRAM_MANAGER_REUSE_ENABLED -> WEFT_MANAGER_REUSE_ENABLED` (:415, :458) inside
`EMBEDDED_WEFT_ENV_MAPPING`, applied by `_translate_embedded_weft_env_vars()`
(:535) only when the Engram env var is set. After this change, setting
`ENGRAM_MANAGER_REUSE_ENABLED` translates to a removed key and fails fast (one
line) at `load_config()`. **Resolved: document, no shim** (owner no-shim rule;
both reviewers concurred). The CHANGELOG entry (task 5) tells embedders to
unset it; no coordinated engram release gates this plan. `../simplebroker` has
no reference to the flag.

### 3b. The interactive exit ladder and the dead `wait=True` stack (finding 01)

Live-path facts (verified at `178e3a34`; the port builds on exactly these):
- `weft/cli/run.py::drive_interactive_session` (:149-181): piped stdin → render
  events and return (:152-154); prompt loop: `EOFError` → `session.close_input()`
  + break (:171-173); `:quit`/`:exit` → `session.stop()` + break (:176-178);
  else `session.send_input(...)`; `finally: output_thread.join()` (:181, **no
  timeout**). The output thread runs `_render_interactive_events(session.events())`
  (:133-146), which ends only when the follow stream yields its `end` event
  after a terminal event (`weft/commands/events.py:512-518`; with `timeout=None`
  the follow loop at :520-530 never expires).
- `weft/commands/run.py::_LiveRunSession` (:367-441): `events()` →
  `iter_task_realtime_events(follow=True)` (:382-390); `send_input`/`close_input`
  write `T{tid}.inbox` (:393-409); `stop()` → `cmd_task_stop(self.tid,
  context=self._context.root)` (:411-415); `wait(timeout)` →
  `await_task_result(..., wait_for_materialization=True)` (:417-432; a supplied
  `timeout` raises `CommandTimeoutError`, `weft/commands/result.py:739-742`);
  `close()` idempotent via `self._closed` (:434-441). `RunSession` Protocol:
  `weft/commands/types.py:79-89`.
- `weft/commands/tasks.py`: `cmd_task_stop` (:2060) → `_task_control_result`
  (:1982) → `stop_task` (:1678): `_require_controllable_task` (:1665, raises
  `TaskNotFound` for unknown TIDs) then **raises `ControlRejected` if the
  snapshot is already terminal** (:1689-1690), else `stop_tasks` (:1595): STOP,
  `_await_control_surface` for `CONTROL_SURFACE_WAIT_TIMEOUT` = 2.0 s
  (`_constants.py:695`), runner-stop fallback (`_stop_via_fallback` :1465,
  `plugin.stop(handle, timeout=0.2)`), one more observation window, return.
  **No client KILL.** `cmd_task_kill` (:2083) → `kill_task` (:1786) →
  `kill_tasks` (:1695): KILL, observation, `_kill_via_fallback` (SIGKILL tree
  via `plugin.kill`), host force-kill fallback, `accept_dead_runtime` (:1720-1785).
- Task side: `weft/core/tasks/interactive.py` inbox handling (:185-215) —
  `{"stdin": ...}` → `session.send`; `{"close": true}` → `session.close_stdin()`
  **only**. STOP/KILL → `_interactive_shutdown` (:452-483): close stdin, wait
  `INTERACTIVE_STOP_GRACE_SECONDS` (2.0 s, `_constants.py:1907`), then
  `session.terminate(deadline=...)` which escalates to `kill()`
  (`weft/core/tasks/sessions.py:766-830`), then terminal proof + ack
  (`_interactive_handle_control`, [MF-3]). STOP marks the task `cancelled` and
  publishes that state event **at receipt**, before the unwind (:509-513).
  Consequence: a child that ignores EOF **and** SIGTERM still converges
  through the STOP rung.
- **What the mapping scopes, and what the runner fallbacks do (reproduced at
  `178e3a34`, probes `0119-rev2/probe-pids-and-reap.txt` and
  `probe-sigstop-task-process.txt`).** The latest `weft.state.tid_mappings`
  row for a running interactive task carries `runtime_handle.observations.
  host_pids = (<command child pid>,)` — the task appends its managed child
  PIDs (`weft/core/tasks/base.py:2335-2341`) and the task host process itself
  (the child's parent) is **not** in the scoped set. `_stop_via_fallback`
  (`tasks.py:1465-1477`) → `HostRunnerPlugin.stop` (`host.py:1248-1250`) →
  `terminate_verified_process_tree(kill=False, timeout=0.2)`
  (`weft/helpers/__init__.py:403-436`) SIGTERMs the scoped tree and **SIGKILLs
  survivors after 0.2 s** — the runner's own "stop" already escalates. The
  manager publishes a manager-authored `failed` / `WRAPPER_LOST_ERROR` terminal
  envelope only for a child *process that has exited* without task-authored
  proof (`weft/core/manager.py:3593-3596`, `:3545-3560`); a SIGSTOPped process
  has not exited and is live to every liveness probe, so nothing reaps it.
- **Which rung a fixture can reach — decisive for 4a case 3.** Three
  reproduced facts bound the design: (i) SIGSTOP on `host_pids[0]` stops the
  *command child*; the task host process still consumes STOP and its
  `session.terminate()` escalates to SIGKILL, which kills a stopped process —
  STOP converges (`cancelled`), no KILL rung. (ii) SIGSTOP on the *task host
  process* (parent of the scoped child), with **no monkeypatching**: the STOP
  envelope is never consumed; after the observation window `stop_tasks`
  escalates to the runner fallback, which kills the command child but nobody
  can publish terminal proof (the only task-side author is stopped; the
  manager sees a live child); `stop()` returns after ≈4.4 s with status still
  `running`; the STOP budget then expires; `cmd_task_kill` writes KILL, its
  runner fallback finds the scoped PID already dead, the host fallback proves a
  dead runtime, and `kill_tasks` accepts via `accept_dead_runtime`
  (`tasks.py:1748-1785`) — **still with no terminal proof**; the KILL-rung
  wait then expires and the ladder exhausts (`CommandExecutionError`). Both
  STOP and KILL envelopes sit unconsumed on `T{tid}.ctrl_in` with broker
  timestamps, so the F5 "full STOP budget before escalation" rule is directly
  measurable as `ts(KILL) − ts(STOP) ≥ INTERACTIVE_STOP_COMPLETION_TIMEOUT`.
  (iii) A task-authored `killed` terminal state **after** the ladder's STOP
  rung is impossible by construction: STOP and KILL travel the same FIFO
  `ctrl_in`, and a task that consumes STOP marks `cancelled` at receipt, so
  KILL can only ever find a terminal task (:502-508 acks and returns). The
  only non-task terminal author is the manager's dead-child `failed` proof,
  which needs the task process to *exit*. Therefore the strict, deterministic
  KILL-rung proof on the live path is: KILL envelope on `ctrl_in` after the
  full STOP budget, `cmd_task_kill` accepted on a dead runtime, and the
  specified explicit error — not a `killed` status. The round-2 brief's
  "killed terminal state" expectation is corrected accordingly (Review
  Record, Codex R2-1).
- **Stream resources and ordering (Codex R2-2, reproduced).**
  `iter_task_realtime_events(context, tid, *, follow=True, cancel_event=None,
  timeout=None)` (`weft/commands/events.py:270-277`) already supports
  cooperative cancellation (`_is_cancelled` :52-53, checked before every
  yield) and releases its monitor and queues in its own `finally` (:531-535).
  `_LiveRunSession.events()` (:382-390) passes no `cancel_event`, and
  `close()` (:434-441) calls `stream.close()` on the raw generator — which
  raises `ValueError: generator already executing` when the adapter's output
  thread is inside it (probe `0119-rev2/probe-generator-close.txt`). In
  `weft/cli/app.py:2452-2466` an exception from `drive_interactive_session`
  reaches `_command_exit` before `consume_run_session` runs, so
  `session.close()` never runs on that path and the daemon output thread and
  its broker handles stay open until interpreter exit.
- Budgets: `INTERACTIVE_STOP_COMPLETION_TIMEOUT` = 2.0 + 3×2.0 + 0.2 + 0.5 =
  **8.7 s** (`_constants.py:1919-1930`; the composition is pinned by
  `tests/system/test_constants.py:333-340`). Reproduced rung costs inside the
  helpers the ladder reuses: `stop()` on a non-consuming task ≈ 4.4 s (two
  `CONTROL_SURFACE_WAIT_TIMEOUT` observations + the 0.2 s runner fallback);
  `cmd_task_kill` on a dead runtime ≈ 4.5 s.

Files to modify for the port (task 4b):
- `weft/commands/run.py::_LiveRunSession` — add `request_exit(*, timeout:
  float = INTERACTIVE_STOP_COMPLETION_TIMEOUT) -> None` (the single
  implementation of the ladder; see task 4b). It observes terminal proof
  **non-consumingly** through `weft/commands/tasks.py::task_status` and never
  reads the outbox; the consuming `wait()` stays the one place a result is
  collected. `events()` passes a per-stream `threading.Event` as
  `cancel_event`; `close()` sets every cancel event before closing streams and
  tolerates the executing-generator `ValueError`. `stop()` is unchanged.
- `weft/commands/types.py::RunSession` (:79-89) — add the `request_exit`
  member (public additive change; Open Owner Question 3).
- `weft/cli/run.py::drive_interactive_session` — both `EOFError` and
  `:quit`/`:exit` call `session.request_exit()`; the function returns `bool`
  (`True` when the operator requested the exit); the output thread is named
  (`weft-run-output`) and joined in the order drain-join → cancel → join
  (task 4b), every join bounded by `CONTROL_SURFACE_WAIT_TIMEOUT`.
- `weft/cli/run.py::render_run_result` — new keyword `quit_requested: bool =
  False`; when true and `status in {"cancelled", "killed"}`, render as
  `completed` with no result value (exit 0) — the adapter-level equivalent of
  the retired `outcome(quit_requested=True)` normalization (`run.py:774-780`).
- `weft/cli/app.py` (:2452-2466) — `try/finally` so `session.close()` runs on
  every path (including `CommandExecutionError` from `request_exit`); capture
  the `bool` from `drive_interactive_session` and pass `quit_requested=` to
  `render_run_result`.
- `docs/specifications/14-Python_API_Surfaces.md` — [PY-2] sentence (:83-86)
  and `## Related Plans` backlink (:246), same commit.
- `weft/_constants.py` — no change; `INTERACTIVE_STOP_COMPLETION_TIMEOUT`
  gains its live consumer, `INTERACTIVE_STOP_GRACE_SECONDS` and
  `CONTROL_SURFACE_WAIT_TIMEOUT` are reused for the cooperative window and the
  bounded joins. No new constants.

Files to delete/modify for the dead-stack removal (task 4c):
- `weft/commands/run.py` — delete:
  - `execute_run` parameter `wait` (:1429, and the `wait` argument at every internal call site); **keep** `session_wait_requested` (used by the persistent-spec guard at :1274-1277) and rewrite that guard from `spec.spec.persistent and (wait or session_wait_requested)` to `spec.spec.persistent and session_wait_requested`.
  - `_execute_inline` / `_execute_spec_via_manager` / `_execute_pipeline` parameter `wait` and the `wait=`/`wait_for_completion=` arguments they pass to `_run_with_managed_execution`.
  - `_wait_for_inline_completion` (nested in `_execute_inline`, :1177-1204) and the `interactive` branch it contains.
  - `_wait_for_task_completion` (:575; its only callers are the deleted wait helpers).
  - `_run_interactive_session` (:959), `_run_interactive_prompt` (:881), `_run_interactive_piped` (:935), and `_InteractiveRunLifecycle` (:603, incl. `request_exit`, `wait_for_completion`, `_send_control`, `_poll_terminal_log`, `_poll_monitor_terminal`, `outcome`, `collect_piped_result`, prompt helpers).
  - Module docstring lines 3-6 ("Interactive prompt mode remains presentation-adjacent here because it owns prompt-toolkit callbacks and live stream display.") → replace with: "Interactive prompt mode lives in the Typer adapter (`weft/cli/run.py::drive_interactive_session`); this module exposes only the queue-backed `_LiveRunSession` returned by `cmd_run`."
  - Now-unused imports (ruff `F401` will flag them): `CONTROL_KILL`, `CONTROL_STOP`, `WEFT_GLOBAL_LOG_QUEUE`, `InteractiveStreamClient`, `collect_interactive_queue_output as _collect_interactive_queue_output` (:47-49), `poll_log_events` (:50-52), `is_pipeline_taskspec_payload`, `await_one_shot_result`, `encode_control_message`, `open_monitor_store`, `terminal_error_message`, `terminal_status_from_event`. **Keep** `threading` (per-stream cancel events), `time` (`time.monotonic` in `request_exit`), `INTERACTIVE_STOP_COMPLETION_TIMEOUT`, `INTERACTIVE_STOP_GRACE_SECONDS`, `INTERACTIVE_STOP_POLL_INTERVAL`, `CONTROL_SURFACE_WAIT_TIMEOUT` (all now used by `request_exit`), `QUEUE_CTRL_IN_SUFFIX`/`QUEUE_CTRL_OUT_SUFFIX`/`QUEUE_INBOX_SUFFIX`/`QUEUE_OUTBOX_SUFFIX`, `replace`, `cast`.
- `weft/commands/interactive.py` — delete the whole module (`InteractiveStreamClient`, 314 lines). Its only non-test consumer is `weft/commands/run.py::_InteractiveRunLifecycle`; private per [PY-1] (not in `weft.commands.__all__`); `../engram` does not use it.
- `weft/commands/_streaming.py::collect_interactive_queue_output` (:149) and its `__all__` entry (:30) — delete. Evidence: its only production consumer is the deleted `collect_piped_result` (`run.py:788`); not in `weft.commands.__all__`; no `../engram` use. Two direct-only tests go with it: `tests/commands/test_result.py::test_collect_interactive_queue_output_handles_malformed_base64` (:457) plus its import (:24), and `tests/commands/test_run.py::test_collect_interactive_queue_output_reads_beyond_fixed_window` (:1457) plus its `_PeekOnlyQueue`-style helper (:1449-1455) and import (:57). `poll_log_events` stays (live consumers in `result.py`/`_result_wait.py`).
- `tests/specs/test_command_queue_seam.py` (:12-17, :40): delete the `"weft/commands/interactive.py"` entry (the allowlist becomes `{}`), and change `test_command_layer_queue_allowlist_entries_have_reasons` to drop the `assert DIRECT_QUEUE_ALLOWLIST_REASONS` nonempty assertion (keep the all-entries-have-reasons assertion, which is vacuously true on an empty allowlist).

Tests to delete (private-only):
- `tests/commands/test_interactive_exit_terminal.py` (whole file; 3 tests, all construct `_InteractiveRunLifecycle` directly) — each behavior it pins is re-proven on the live path by task 4a before this deletion.
- `tests/commands/test_interactive_client.py` (whole file; 445 lines, all on `InteractiveStreamClient`).
- In `tests/commands/test_run.py`: the `_run_interactive_session` tests (`test_interactive_prompt_completion_exits_without_cross_thread_app_mutation` :638, `test_interactive_start_failure_closes_owned_resources` :699, `test_interactive_quit_escalates_stop_before_kill` :749, `test_interactive_piped_result_precedence` :798, `test_interactive_completion_ignores_unexpected_monitor_store_failure` :858) and their helper classes (:379-635); the `_wait_for_task_completion` tests (:1312, :1365, :1419) and import (:63); the sync-wait tests `test_run_inline_wait_succeeds_when_post_proof_acknowledgement_fails` (:2409, `wait=True` at :2449) and `test_run_spec_via_manager_returns_timeout_exit_code` (:3043, `wait=True` at :3085).

Tests to modify — complete inventory of surviving `wait=` callers of the
changed signatures (AST-verified at `178e3a34`; drop the kwarg, keep the test):
- `tests/commands/test_run.py`: `_execute_inline` at :2212 (`test_run_inline_enqueues_task_before_ensuring_manager`), :2249 (`test_execute_inline_rejects_invalid_function_target_before_context`), :2349 (`test_execute_inline_contains_unexpected_command_boundary_failure`), :2396 (`test_run_inline_no_wait_succeeds_when_post_proof_acknowledgement_fails`), :2937 (`test_run_inline_deletes_spawn_request_when_ensure_manager_fails`); `execute_run` at :2304 (`test_execute_run_inline_returns_structured_result_without_rendering`); `_execute_spec_via_manager` at :2984, :3036, :3146, :3206, :3251; `_execute_pipeline` at :3300 (`test_run_pipeline_deletes_spawn_request_when_ensure_manager_fails`, `wait=True` — exercises the live submission-failure `except` path; keep), :3367, :3422.
- `tests/commands/test_run_public.py::test_cmd_run_wait_returns_a_session` (:143): delete the `assert kwargs["wait"] is False` line (:157); the `fake_execute(**kwargs)` stub itself keeps working.
- Renderer tests in `tests/commands/test_run.py` (`render_run_result` at :114, :124, :159 — `test_run_renderer_preserves_terminal_handoff_categories` :94, `test_run_renderer_keeps_timeout_exit_124` :143): **unchanged** (the renderer's own `wait` stays); task 4b adds a sibling case for `quit_requested`.

Do **not** touch (unrelated `wait` parameters that stay):
- `weft/cli/run.py::render_run_result(..., wait, ...)` — the CLI renderer's own
  `wait` (TID-only vs waited-result rendering), passed `wait=wait` from `weft/cli/app.py`.
- `weft/commands/run.py::cmd_run(..., wait=True, ...)` — the public [PY-2]
  branch selector (`RunSession` iff wait, else `RunExecutionResult`) stays.
- `weft/commands/_streaming.py::poll_log_events`; `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher`
  (inherited by `BaseTask`; embedded externally — do not delete);
  `iter_task_realtime_events`; task-side interactive execution; the
  pre-existing unused `run.py:492 _drain_stream_queue` helper (no drive-by).

Comprehension checks (answer before editing):
1. Which function, and which of its parameters, is the *only* reader of
   `WEFT_MANAGER_REUSE_ENABLED`, and why is its stop-manager branch unreachable
   in production? (Answer: `_run_with_managed_execution`'s `finally` block; the
   `wait` term is always false because `cmd_run` calls `execute_run(wait=False)`.)
2. On the live path today, what happens when the operator presses Ctrl-D and the
   child ignores EOF? (Answer: `close_input()` writes `{"close": true}`; the task
   closes the child's stdin and nothing else; no terminal event; the CLI blocks
   forever in `output_thread.join()`.)
3. Why can neither a SIGTERM-trapping child nor a SIGSTOPped *child* exercise
   the client KILL rung, and what does? (Answer: task-side
   `session.terminate()` escalates to `kill()`, so the STOP rung converges in
   both cases; the mapping scopes the child, not the task host process. Only
   a task host process that cannot consume STOP — SIGSTOPped — reaches KILL,
   and then no terminal proof can exist, so the strict outcome is the
   explicit exhaustion error, never `killed`.)
4. Why must `request_exit` tolerate `ControlRejected` from `stop()`? (Answer:
   `stop_task` rejects already-terminal tasks; a cooperative child can exit at
   the edge of the cooperative window, making STOP arrive after terminal.)
5. Why does `request_exit` observe terminal proof through `task_status`
   rather than `wait(timeout=...)`? (Answer: `wait()` → `await_task_result`
   consumes the outbox, while the adapter's output thread only peeks; a
   consuming wait racing the peeking renderer can swallow unrendered stream
   chunks. One consuming `wait()` after the output thread has drained keeps
   today's ordering.)
6. Why must `close()` set a cancel event instead of only calling
   `generator.close()`? (Answer: closing a generator that another thread is
   executing raises `ValueError: generator already executing`; the follow
   loop checks `cancel_event` before every yield and releases its queues in
   its own `finally`.)

## 4. Invariants and Constraints

- **[PY-2] `RunSession` contract is preserved and extended additively.**
  `tid`, `events`, `send_input`, `close_input`, `stop`, `wait`, `close` keep
  their signatures and semantics; `stop()` stays graceful (STOP + [CLI-1.2.3]
  convergence, never KILL); `close()` stays idempotent and never cancels the
  task. `request_exit()` is the only new member (Open Owner Question 3).
- **[PY-1] privacy.** `execute_run`, `_InteractiveRunLifecycle`,
  `weft/commands/interactive.py::InteractiveStreamClient`, and
  `weft/commands/_streaming.py::collect_interactive_queue_output` are private
  (not in any package `__all__`); removing them is not a public-surface change.
  Verified no `../engram`/`../simplebroker` consumer.
- **Submission-failure manager stop stays live.** The `except` branch of
  `_run_with_managed_execution` (:195-198) must still stop a `started_here`
  manager when submission fails. Do not delete it with the `finally` block.
- **Manager lifecycle rules are untouched.** Task completion never causes
  `weft run` to stop a manager (it did not in production before this change
  either). Idle timeout [MA-1.5], control/signal drain [MANAGER.7],
  supersession and leadership yield [MANAGER.8]/[MANAGER.8a], `--replace`
  supersession, and explicit `weft manager stop` all remain exactly as
  specified. No plan text may describe idle timeout or explicit stop as the
  "only" end-of-life paths.
- **Fail-closed, no shim (owner rule, Van 2026-09-08).** Setting or overriding
  `WEFT_MANAGER_REUSE_ENABLED` raises `WeftConfigError` at `load_config()` with
  the migration message, rendered as one line at the CLI boundary. No legacy
  tolerance, no drain window. Record the break in CHANGELOG.
- **Interactive streaming capability is unchanged.** The live path surfaces
  interactive stdout/stderr/terminal via `iter_task_realtime_events` (peeking,
  non-consuming). Deleting `InteractiveStreamClient` removes a *consuming*
  client that production no longer used; it does not remove observable output.
- **The specified interactive exit ladder is ported, not dropped.** After
  `:quit`/`:exit` or EOF at the prompt: close input → bounded cooperative
  window → STOP (`cmd_task_stop`, [CLI-1.2.3]) → wait for terminal proof
  within `INTERACTIVE_STOP_COMPLETION_TIMEOUT` measured from the STOP send →
  KILL (`cmd_task_kill`) → bounded wait → explicit `CommandExecutionError`.
  One implementation (`_LiveRunSession.request_exit`), no second copy in the
  adapter. Every rung is bounded; every adapter join is bounded. Piped-stdin
  mode is unchanged (§9).
- **Terminal proof, not acknowledgement, ends the wait** — `request_exit`
  observes task-published terminal state through the non-consuming
  `task_status` snapshot, consistent with [MF-3] "acknowledgement is not
  terminal proof". Acks are evidence-if-present in tests (the runner-stop
  fallback can SIGKILL the task before its ack lands — Claude R2-1), never
  the exit condition and never a required assertion.
- **Ordered stream teardown on every path.** The adapter owns the output
  thread; the session owns the streams. Order is fixed: bounded drain-join →
  `session.close()` (sets each stream's `cancel_event`, then closes) → bounded
  join → the single consuming `wait()` → idempotent `close()`. `session.close()`
  runs on success and on every error path (`try/finally` in `weft/cli/app.py`),
  and never raises on an executing generator.
- **Exit-code selection stays in the adapter.** [CLI-1.1.1] :101-102 assigns
  rendering and exit-code selection to `weft/cli/run.py`; the `:quit` → exit 0
  normalization lives there (`render_run_result(quit_requested=...)`), and the
  [PY-2] result stays truthful (`cancelled`/`killed`).
- **TID format/immutability, forward-only state transitions, reserved-queue
  policy, `spec`/`io` immutability, spawn-based process behavior, strict
  control envelopes, and runtime-only `weft.state.*` queues** are untouched.
- **Fatal vs best-effort:** `load_config()` rejection of the removed key is
  fatal (fail fast). Manager stop on submission failure is best-effort and must
  not mask the original submission error (it re-raises). Ladder exhaustion is
  fatal for the CLI invocation (explicit error, exit 1) but never kills anything
  beyond what `cmd_task_kill` already does.
- **One-way door:** the config-key removal is a one-way compatibility break for
  anyone exporting the variable (including via `ENGRAM_MANAGER_REUSE_ENABLED`).
  No persisted-data one-way door exists.

Review gates for this slice:
- No new execution path beyond the ported ladder; no new abstraction, registry,
  constant, or event system. Forbidden inventions: a second exit ladder in the
  adapter; a new timeout constant; a client-side process-signalling helper
  (use `cmd_task_stop`/`cmd_task_kill` only); a compatibility decoder for the
  env var; widening the bootstrap catch to bare `ValueError`.
- No drive-by refactor (leave `render_run_result.wait`, `cmd_run.wait`,
  `session_wait_requested`, `_drain_stream_queue` at `run.py:492`, the
  `*manager_reuse*` test names, and the parser `ValueError`s alone).
- No public CLI shape change (`weft run` flags unchanged).
- No spec drift between touched docs and code; traceability acceptance = no
  new warning/error on the touched surfaces relative to the pre-change scanner
  run (aggregate baseline debt exists and is not this plan's to clear).
- External review before each atomic B slice lands and before completion
  (Class 5 + risky).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `178e3a34` — docs/specifications/00-Quick_Reference.md,
  10-CLI_Interface.md, 05-Message_Flow_and_State.md,
  04-SimpleBroker_Integration.md, 14-Python_API_Surfaces.md,
  03-Manager_Architecture.md, 07-System_Invariants.md at plan authoring time
  (2026-09-08). Plan type: implementation with spec revision. Promotion
  baseline identifiers: one per landing slice (task 2 for the D clarifications;
  tasks 3, 4b, 4c for their atomic B edits) — each recorded in this section
  (commit SHA, or diff base + worktree state) **before** the next slice starts.
  Tasks 3 and 4 are gated on the task-2 identifier being recorded here.

## Proposed Spec Delta

Promotion strategy (writing-plans.md §4d, :425-450):

| Spec file | Strategy | Lands with | Sections touched |
|-----------|----------|------------|------------------|
| docs/specifications/10-CLI_Interface.md | D | task 2 | [CLI-1.1.1] :107-108 stale sentence replaced; "Current behavior" list — one manager-end-of-life bullet appended after item 6 (:121) |
| docs/specifications/00-Quick_Reference.md | B | task 3 | remove `WEFT_MANAGER_REUSE_ENABLED` row (:184); backlink in `## Related Plans` (:238) |
| docs/specifications/10-CLI_Interface.md | B | task 4b | [CLI-1.1.1] interactive-exit paragraph + Implementation/Correction lines replaced (:295-302); backlink (:1032) |
| docs/specifications/14-Python_API_Surfaces.md | B | task 4b | [PY-2] `RunSession` sentence (:83-86) gains `request_exit`; backlink in `## Related Plans` (:246) |
| docs/specifications/05-Message_Flow_and_State.md | B | task 4c | [MF-3] control-producer mapping (:266-268) — replace the `_InteractiveRunLifecycle._send_control` clause; backlink (:1525) |
| docs/specifications/04-SimpleBroker_Integration.md | B | task 4c | [SB-0.4] mappings (:11, :590) and the "interactive queue client" bullet (:645) — remove `weft/commands/interactive.py`; backlink (:724) |

All deltas are exact replacement prose against `178e3a34`. Implementation
mapping symbols are path-qualified so the traceability scanner resolves them.

### [CLI-1.1.1] (D, task 2) — replace :107-108

Current: "Interactive prompt mode still has presentation callbacks in the
command layer because the prompt loop is not yet a public `WeftClient.run()`
API."

> Interactive prompt mode lives in the Typer adapter
> (`weft/cli/run.py::drive_interactive_session`); the command layer exposes
> only the queue-backed `RunSession` returned by `cmd_run` ([PY-2]).

### [CLI-1.1.1] (D, task 2) — append after "Current behavior" item 6 (:121)

> 7. never terminate the manager on task completion. A manager started to
>    service the run stays alive under the ordinary manager lifecycle rules —
>    idle timeout ([MA-1.5]), control/signal drain ([MANAGER.7]), leadership
>    yield and supersession ([MANAGER.8]/[MANAGER.8a]), or explicit
>    `weft manager stop`. Only
>    a post-enqueue submission failure stops a manager the same invocation
>    started.

### 00-Quick_Reference (B, task 3) — remove the `WEFT_MANAGER_REUSE_ENABLED` row (:184)

Delete the table row verbatim. Add this plan to `## Related Plans` (:238).

### [CLI-1.1.1] (B, task 4b) — replace :295-302

Current paragraph: "After requesting STOP, the interactive client observes
matching acknowledgement or existing terminal evidence for the named
`INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget before escalating to KILL.
Terminal evidence ends the wait promptly; acknowledgement remains after
task-side runtime unwind." plus "Implementation:
`weft/commands/run.py::_InteractiveRunLifecycle.request_exit`." and
"Correction plan: …".

> In prompt mode (stdin is a TTY; an empty pipe is piped mode, not prompt
> mode), `:quit`/`:exit` or end-of-input at the
> prompt requests a bounded exit: the client closes the task's input stream
> (`T{tid}.inbox`), allows a short cooperative window
> (`INTERACTIVE_STOP_GRACE_SECONDS`), then requests a graceful stop through
> the standard task-stop control convergence ([CLI-1.2.3]) and waits for
> task-published terminal proof for the `INTERACTIVE_STOP_COMPLETION_TIMEOUT`
> budget measured from the STOP request. If no terminal proof arrives it
> escalates to the standard task-kill convergence, waits one more bounded
> window (`CONTROL_SURFACE_WAIT_TIMEOUT`), and then fails the invocation with
> an explicit error. Terminal proof is observed without consuming result
> queues and ends every wait promptly; acknowledgements are evidence, not the
> exit condition. The client never waits without a budget, and it releases
> its event stream and broker handles on every exit path. The task-side
> interactive unwind still owns cooperative-exit grace and forced child
> termination and publishes terminal proof ([MF-3]). Piped-stdin mode sends
> the input with an end-of-input marker and waits for completion as before.
> An operator-requested exit (`:quit`/`:exit`, and — pending Open Owner
> Question 5 — end-of-input at the prompt) that ends `cancelled` or `killed`
> renders as success (exit 0); any other terminal status keeps its ordinary
> rendering.
>
> Implementation: `weft/commands/run.py::_LiveRunSession.request_exit` owns the
> ladder and delegates to `weft/commands/tasks.py::cmd_task_stop` and
> `weft/commands/tasks.py::cmd_task_kill`, observing terminal proof through
> `weft/commands/tasks.py::task_status`; `weft/cli/run.py::drive_interactive_session`
> drives terminal input and rendering with bounded output-thread joins and
> ordered stream cancellation; `weft/cli/run.py::render_run_result` owns the
> operator-requested-exit rendering.
> Plan: [Manager reuse flag retirement and run lifecycle completion](../plans/2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md).

### [PY-2] (B, task 4b) — replace :83-86

> - `RunSession` exposes `tid`, `events() -> CommandStream[TaskEvent]`,
>   `send_input(text)`, `close_input()`, `stop() -> TaskControlResult`,
>   `request_exit(timeout=INTERACTIVE_STOP_COMPLETION_TIMEOUT) -> None`,
>   `wait(timeout=None) -> RunExecutionResult`, and idempotent `close()`. Close
>   releases owned resources (including cooperative cancellation of any event
>   stream still being iterated) but does not cancel the task. `stop()` is the
>   graceful stop only; `request_exit()` is the bounded operator-exit ladder
>   ([CLI-1.1.1]): it returns once task-published terminal proof is observed
>   without consuming result queues — the result is then collected by
>   `wait()` — or raises `CommandExecutionError` after STOP and KILL have both
>   been requested and no terminal proof arrived.
>   After `close()`, `events()` raises `CommandUsageError`; a stream may not
>   be registered against a closed session.
>
> Add to `## Related Plans` (:246): `- [Manager reuse flag retirement and run
> lifecycle completion](../plans/2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md)`.

### [MF-3] (B, task 4c) — replace :266-268 clause

Current: "command producers live in `weft/commands/tasks.py::_send_control` and
`weft/commands/run.py::_InteractiveRunLifecycle._send_control`."

> command producers live in `weft/commands/tasks.py::_send_control` (used by
> `task stop`/`task kill` and by `weft/commands/run.py::_LiveRunSession.stop`
> and `weft/commands/run.py::_LiveRunSession.request_exit`).

### [SB-0.4] (B, task 4c) — :11, :590, :645

Delete the `weft/commands/interactive.py,` entry from the :9-14 mapping list
and the `weft/commands/interactive.py;` entry from the :587-596 list, keeping
punctuation well-formed. Delete the :645 bullet ("interactive queue client that
owns its own task-local inbox lifecycle"); verify no sentence cross-references
it after removal.

## Spec-Changing Work — Slice Order

Implementation-with-spec-revision. Order: plan → independent review (rounds
1-2 recorded; revision 2 requests a scoped round 3) → task 2 (D clarifications + baseline id) →
task 3 (atomic B: row removal + REMOVED rule + boundary exception) → task 4a
(red live-path tests) → task 4b (atomic B: port + [CLI-1.1.1]/[PY-2] text) →
task 4c (atomic B: deletion + [MF-3]/[SB-0.4] mappings) → task 5 (docs,
CHANGELOG, traceability). Each B slice lands text, link claims, code, and
reciprocal `Spec:` backlinks together, so no slice cites or strands a
spec-named symbol: after 4b the spec names `_LiveRunSession.request_exit`
(exists) and the old symbols are still present until 4c removes them together
with their mappings.

## 5. Tasks

1. **Independent review of the plan and the Proposed Spec Delta.** Rounds 1
   (Codex cross-family, BLOCKED; Claude, PASS) and 2 (Codex, FAIL; Claude,
   PASS) are dispositioned in the Review Record. Revision 2 redesigns the
   KILL-rung fixture, specifies stream teardown ordering, and separates the
   EOF exit-code policy; it requests a **scoped round-3 verification** of
   that delta (Revision Log) before any slice lands (§8).

2. **Clarification promotion slice (D).** Apply the two [CLI-1.1.1] D edits
   (:107-108 sentence; item 7 after :121). Record the promotion baseline
   identifier in Spec Baseline.
   - Verify: `./.venv/bin/python -m pytest tests/specs/ -q`.
   - The `docs/plans/README.md` index row is owned by the top-level agent.
   - Done when: text is in the spec verbatim and `tests/specs/` passes.

3. **Retire the reuse flag (atomic B).** Constants + config + boundary
   exception + Quick Reference row + tests, atomic with the test-harness env
   edits so `load_config()` never rejects a still-exported key mid-suite.
   - Red first (unit): replace `test_manager_reuse_env`
     (`tests/system/test_constants.py:770-777`) with a parametrized rejection
     test modeled on `test_removed_collation_store_toggle_fails_with_migration_message`
     (:1307-1323): over `source ∈ {environment, override}` × `value ∈ {"0", "1"}`,
     assert `load_config()` raises `WeftConfigError` (import from
     `weft._exceptions`) with the **exact** migration message. Red because
     neither the class nor the message exists yet.
   - Red first (boundary): in `tests/cli/test_env_file_bootstrap.py`, add
     `test_removed_env_key_is_safe_before_cli_import` (process env:
     `env["WEFT_MANAGER_REUSE_ENABLED"] = "0"`) and
     `test_removed_env_key_in_env_file_is_safe_before_cli_import`
     (`weft.env` containing `WEFT_MANAGER_REUSE_ENABLED=1`), both running
     `_run_module("weft", "system", "dump", cwd=tmp_path, ...)` exactly like
     :216-231 and asserting: `returncode == 1`; `stdout == ""`;
     `stderr.count("\n") == 1`; `stderr.startswith("weft: ")`; the migration
     message is in `stderr`; `"Traceback" not in stderr`; and
     `not (tmp_path / ".weft" / "broker.db").exists()` (no state created). Red
     today because the traceback has many lines.
   - `weft/_exceptions.py`: add `WeftConfigError(WeftError, ValueError)`
     ("Raised when Weft configuration is rejected at `load_config()`.").
   - `weft/_constants.py`: delete the default constant + docstring (:1880);
     delete the loader entry (:2885-2889); add a general removed-env map + loop
     next to `removed_task_monitor_env` (:2796-2817) rejecting the key; replace
     the override rule (:3203-3206) with a `REMOVED` rule; define one shared
     message constant used by both sites; change the two shared raise sites
     (:2817, :3145) from `ValueError(...)` to `WeftConfigError(...)`. Message:
     `"WEFT_MANAGER_REUSE_ENABLED was removed; weft run never stops a manager on task completion, and manager lifetime follows the ordinary manager lifecycle rules (idle timeout WEFT_MANAGER_LIFETIME_TIMEOUT, control/signal drain, supersession, or 'weft manager stop')"`.
   - `weft/bootstrap.py:104-116`: `from weft._exceptions import WeftConfigError`
     inside the same import-light block and `except (InvalidConfigError,
     WeftConfigError) as exc:`.
   - `weft/commands/run.py`: delete the three `reuse_enabled` reads/kwargs
     (:1175/:1227, :1289/:1313, :1385/:1398) and the `reuse_enabled` parameter
     of `_run_with_managed_execution` (the rest of its signature change is task
     4c).
   - `tests/system/test_constants.py`: remove import (:84), `weft_keys` entry
     (:1251), defaults assertion (:1286).
   - `tests/helpers/weft_harness.py` (:669), `tests/helpers/long_session_utils.py`
     (:45), `tests/cli/test_cli_run.py` (:2917/:2956/:2999 + the :339 diagnostic
     key): remove the env settings.
   - `docs/specifications/00-Quick_Reference.md`: remove row :184; add the
     backlink at :238. `README.md:1325`: remove the bullet.
   - Reuse the existing REMOVED `_OverrideRule` machinery and `_load_weft_env_value`
     helpers. Forbidden inventions: a new config framework, a compatibility
     decoder, a deprecation-warning path, a silent default fallback, a bare
     `ValueError` catch in bootstrap.
   - Stop if: any production module outside `weft/commands/run.py` reads
     `WEFT_MANAGER_REUSE_ENABLED` (grep says none), or `weft/_exceptions.py`
     turns out to import `_constants` (it does not at `178e3a34`).
   - Done when: the four unit cells and both subprocess tests pass; the
     existing four removed-key tests still pass; the allowed-residue check in
     §7 matches exactly; `tests/specs/` passes; baseline identifier recorded.

4. **Port the interactive exit ladder to the live path, then delete the dead
   stack** (three ordered commits; the proof lands before the deletion it
   authorizes).

   **4a — Red live-path tests (harness-backed, single execution owner).**
   In `tests/cli/test_cli_run.py`, using `weft_harness.ensure_foreground_manager()`
   (`tests/helpers/weft_harness.py:198`) as the **only** execution owner (no
   test-run `Consumer`), build the session with
   `session = commands.cmd_run((sys.executable, "-u", <script>), interactive=True,
   wait=True, context=workdir)` and drive `weft.cli.run.drive_interactive_session(session, None)`
   in a worker thread with `PromptSession` monkeypatched on the `prompt_toolkit`
   module (the adapter imports it inside the function) to yield scripted lines
   or raise `EOFError`. **Readiness barrier before any prompt input:** poll
   `weft.commands.tasks.task_status(session.tid, context=...)` until status is
   `running` (bounded, ≤ 10 s) — this avoids the known-task guard
   (`tasks.py:1665-1672`) and `ControlRejected` races. Each case joins the
   worker with a bound and asserts it finished (red today = the join times
   out). Evidence helpers: peek `T{tid}.ctrl_in` (open the handle with
   `persistent=False`; that flag is a handle-lifetime choice, `context.py:144`,
   not durability — `_send_control` writes with `persistent=True` and the rows
   are ordinary broker rows either way) with
   timestamps for control envelopes (`{"command": "STOP"|"KILL"}`, unconsumed
   only when the task cannot read them); the task-authored terminal status
   through `task_status`/`session.wait()`. Because `session.wait()` consumes,
   every case calls it exactly once, after the adapter has drained.
   Cases:
   1. *Cooperative child* (`tests/tasks/interactive_echo.py`; it blocks in
      `readline()` so it is alive until the exit request): lines `hello`, then
      `:quit`. Assert `echo: hello` rendered; `drive_interactive_session`
      returned `True`; terminal status `completed` (the child exits at EOF
      inside the cooperative window, so no STOP is ever sent — `completed` is
      itself the proof, since a consumed STOP marks `cancelled` at receipt,
      `interactive.py:509-511`); `render_run_result(execution, wait=True,
      json_output=False, verbose=False, suppress_result_value=True,
      quit_requested=True) == 0`; the `weft-run-output` thread is not alive
      after the adapter returned.
   2. *EOF-ignoring child* (script: print `ready`, `sys.stdin.read()`, then
      `while True: time.sleep(0.05)` — the child from the deleted
      `test_interactive_exit_waits_for_real_eof_ignoring_child`), parametrized
      over entry ∈ {`:quit` line, `EOFError`}. Assert: terminal status
      `cancelled` observed through the real broker (reproduced 3/3 at
      `178e3a34`: `0119-rev2/probe-live-stop-kill.txt`); **strict STOP
      evidence** — terminal `cancelled` **and no KILL envelope** on
      `T{tid}.ctrl_in` (a STOP ack on `T{tid}.ctrl_out` is recorded as
      evidence if present but is **not** asserted: the runner-stop fallback
      SIGKILLs the task 0.2 s after `cancelled` is visible, and in the probe
      no ack landed); elapsed from the exit request <
      `INTERACTIVE_STOP_GRACE_SECONDS + INTERACTIVE_STOP_COMPLETION_TIMEOUT`;
      renderer exit code 0 with `quit_requested=True` and 1 without (pins the
      exit-code decision both ways; the EOF entry additionally pins Open
      Owner Question 5's default); output thread not alive afterwards.
   3. *Non-consuming task host process — KILL rung, exhaustion, and ordered
      teardown* (`@pytest.mark.slow`, `@pytest.mark.skipif(sys.platform ==
      "win32", reason="SIGSTOP")`, the repo's existing POSIX-guard form).
      Fixture, designed from the reproduced control path (§3b, probe
      `0119-rev2/probe-sigstop-task-process.txt`), **no replacement or
      short-circuit of any control helper or plugin** — the only permitted
      wrappers are the two *delegating* spies around the real `cmd_task_stop`
      and `cmd_task_kill` described under (a), which record
      `time.monotonic()` at entry and call the real function unchanged: after
      readiness, read the latest mapping
      (`mapping_for_tid` → `_runtime_handle_from_mapping(...).scoped_host_pids()`),
      take the scoped command child PID, resolve its parent with
      `psutil.Process(pid).ppid()` — that is the task host process — and
      `os.kill(task_pid, SIGSTOP)`; register both PIDs with the harness and
      SIGCONT+SIGKILL them in `finally`. Then `:quit`. Why this fixture and
      not a monkeypatched `plugin.stop`: a real process that consumes STOP
      always converges (STOP marks `cancelled` at receipt and the task-side
      unwind escalates to SIGKILL), and the runner's own `stop()` escalates to
      SIGKILL after 0.2 s, so "STOP cannot terminate or publish proof" is
      only true when the task host process itself is not scheduled; that
      state is reachable with a real process and needs no double. Assert,
      strictly: (a) both a STOP and a KILL envelope are present on
      `T{tid}.ctrl_in` (unconsumed — the process is stopped) and
      `((id_kill & ~0xFFF) − (id_stop & ~0xFFF)) / 1_000_000_000 ≥ INTERACTIVE_STOP_COMPLETION_TIMEOUT − 0.25`,
      where `id_*` are the broker message IDs of the two envelopes: 64-bit
      hybrid timestamps whose high bits are physical nanoseconds and whose
      low 12 bits are the logical counter (CLAUDE.md §3). Masking the low 12
      bits yields physical nanoseconds; divide once by 1e9 for seconds.
      Do not call any SimpleBroker timestamp helper (they are private);
      use message IDs for ordering only. (Comparing raw IDs with a float
      budget proves only nanoseconds elapsed.) The
      0.25 s tolerance covers the asymmetric pre-write latency of the first
      control call (measured margin 0.13 s); additionally assert, from the
      ladder side, that the KILL rung began no earlier than
      `stop_sent_at + budget − 0.05`: the observable seam is a **delegating
      spy** around the real `cmd_task_kill` installed for case 3 only
      (records `time.monotonic()` at entry, then calls the real function
      unchanged); the spy-side STOP time is recorded the same way around the
      real `cmd_task_stop`. Production records `stop_sent_at` immediately
      *before* calling `self.stop()`, so the spy's STOP entry time is
      slightly later than the ladder's origin; the 0.05 s tolerance (one
      `INTERACTIVE_STOP_POLL_INTERVAL`) absorbs that skew so correct code
      cannot fail the assertion. These two spies are the only permitted
      wrappers of control helpers and they must delegate, never replace (the
      F5 full-budget rule on the live path);
      (b) the scoped child PID is dead
      (runner fallback) and the task host PID is still alive and `stopped`
      (`psutil.Process.status()`); (c) `drive_interactive_session` raised
      `CommandExecutionError` whose message names the TID and the budget —
      the specified exhaustion outcome; a task-authored `killed` cannot occur
      here (§3b) and is **not** an accepted alternative; (d) the worker
      finished within `INTERACTIVE_STOP_GRACE_SECONDS +
      INTERACTIVE_STOP_COMPLETION_TIMEOUT + 3 × CONTROL_SURFACE_WAIT_TIMEOUT +
      5.0` = 21.7 s (cooperative window + STOP budget + `kill_tasks`' two
      observation windows + the KILL-rung wait + 5.0 s slack shared by five
      broker-bound windows; reproduced ≈17.2–17.3 s);
      (e) after the
      adapter raised, no thread named `weft-run-output` is alive
      (`threading.enumerate()`) within `CONTROL_SURFACE_WAIT_TIMEOUT`, and
      `session.close()` was reached (the app-level wiring test below covers
      the `finally`; here assert a second `session.close()` is a no-op and
      that `session.events()` after `close()` raises the typed
      `CommandUsageError("run session is closed")` — see the post-close rule
      added to 4b) — this is the Codex R2-2 termination proof after ladder
      exhaustion. Case 3's `finally` must SIGCONT the task host and must not
      assert on the task's final status (it ends `failed` after SIGCONT).
   - Adapter wiring test (unit, `tests/cli/test_cli_run.py` next to the
     `run_cli` helpers): invoke the `run` command with a `RunSession` double
     whose `request_exit` raises `CommandExecutionError` and record whether
     `close()` ran; assert exit 1, one-line stderr, `close()` called exactly
     once. (This is the one place a session double is allowed: the unit under
     test is the `try/finally` in `weft/cli/app.py`, not the ladder.)
   - What must NOT be mocked: the broker/`WeftContext`, the harness manager
     and its spawned task process, `cmd_task_stop`/`cmd_task_kill` and their
     convergence (case 3 may wrap both in a *delegating* spy that records
     `time.monotonic()` and calls the real function — a replacement or a
     short-circuit is a stop-and-re-evaluate signal), the runner plugin, `_LiveRunSession`,
     `iter_task_realtime_events`, `render_run_result`. Mock only
     `PromptSession` (the TTY boundary) and, in the wiring test only, the
     session.
   - Stop if: case 2 cannot be made red/green without reaching into the
     private classes; the harness manager cannot spawn the script; or the
     mapping's scoped PID is not the command child on the CI platform (then
     case 3's parent resolution is wrong — report the observed mapping
     rather than adapting the fixture).
   - Done when: all cases are written and red for the documented reason
     (cases 2-3: worker join times out; case 1: exit-code and thread
     assertions; wiring test: `close()` not called).

   **4b — Port (atomic B with the [CLI-1.1.1] paragraph, the [PY-2] sentence, and the 14 backlink).**
   - `weft/commands/run.py::_LiveRunSession.request_exit(*, timeout: float = INTERACTIVE_STOP_COMPLETION_TIMEOUT) -> None`,
     `Spec: [CLI-1.1.1], [PY-2]`. Ladder, using only existing helpers.
     `budget = max(0.0, float(timeout))` is captured **at entry** and is the
     only name used in the error text (Codex R2-4). Terminal observation is
     one private helper on the class, `_observe_terminal(deadline) -> bool`,
     which polls the non-consuming `task_status(self.tid, context=self._context)`
     (late import next to `cmd_task_stop`) at `INTERACTIVE_STOP_POLL_INTERVAL`
     — the same cadence the retired `wait_for_completion` used (:694) — and
     returns `True` as soon as `snapshot.status in TERMINAL_TASK_STATUSES`;
     it never touches the outbox. Rungs:
     0. (Input-mode guard, Codex R3-3/R4-1 — piped path, not the ladder.)
        `weft/cli/run.py::read_run_stdin` returns `""` for an empty pipe and
        `None` only for a TTY; `weft/commands/submission.py::_initial_work_payload`
        (`:194-207`) changes its interactive branch from `if stdin_text:` to
        `if stdin_text is not None:` so an empty pipe sends
        `{"stdin": "", "close": True}` (the non-interactive branch keeps its
        truthiness check). Files: `weft/cli/run.py`, `weft/commands/submission.py`,
        `tests/commands/test_submission.py` (or the existing payload test
        module — locate `_initial_work_payload` tests by grep) and
        `tests/cli/test_cli_run.py`. Test: empty-pipe regression asserting
        that exact inbox payload and that no STOP/KILL envelope is written.
     1. `self.close_input()`; `_observe_terminal(now + INTERACTIVE_STOP_GRACE_SECONDS)`;
        on proof → return.
     2. `stop_sent_at = time.monotonic()`; `self.stop()`; catch
        `ControlRejected` (already terminal) → `_observe_terminal(now +
        CONTROL_SURFACE_WAIT_TIMEOUT)`; on proof → return, else fall through
        to rung 4. `TaskNotFound` (task unknown to `stop_task` because the
        manager has not registered the mapping yet, `tasks.py:1665-1672`)
        propagates unchanged: it is a pre-existing edge, renders as one line
        (`_command_exit`), and the rung-1 observation already re-checked
        `task_status` immediately before `stop()`, so no extra call is added
        (Claude R2-3, documented in §9).
     3. `_observe_terminal(stop_sent_at + budget)`; on proof → return.
     4. `cmd_task_kill(self.tid, context=self._context.root)` (late import
        next to `cmd_task_stop`); catch `ControlRejected` the same way;
        `_observe_terminal(now + CONTROL_SURFACE_WAIT_TIMEOUT)`; on proof →
        return.
     5. raise `CommandExecutionError(f"Interactive session {self.tid} did not
        stop after the exit request: STOP and KILL were requested and no
        terminal proof arrived within {budget:.1f}s")` (import from
        `weft._exceptions`; Open Owner Question 4 on the error class).
   - `_LiveRunSession.events()`: create `cancel = threading.Event()`, pass
     `cancel_event=cancel` to `iter_task_realtime_events`, record
     `(stream, cancel)`. `close()`: set every cancel event first, then
     `stream.close()` inside `try/except ValueError` (the CPython
     "generator already executing" case — the iterating thread exits at its
     next ≤0.1 s cancel check and the generator's own `finally`,
     `events.py:531-535`, releases the monitor and queues). `threading` is
     already imported in `run.py` (§3b import list: keep it).
   - `weft/commands/types.py::RunSession` (:79-89): add
     `def request_exit(self, *, timeout: float = ...) -> None: ...`.
   - `weft/cli/run.py::drive_interactive_session` → returns `bool`: `EOFError`
     and `:quit`/`:exit` both call `session.request_exit()` and set the flag.
     The output thread is created with `name="weft-run-output"`. `finally`
     (runs on return and on `CommandExecutionError`):
     `output_thread.join(timeout=CONTROL_SURFACE_WAIT_TIMEOUT)` (drain: after
     terminal proof the follow stream emits `end` on its next 0.1 s poll);
     if the thread is still alive: `session.close()` (cancel) then
     `output_thread.join(timeout=CONTROL_SURFACE_WAIT_TIMEOUT)` again. The
     thread stays a daemon so a pathological stream cannot hold the process.
     Piped-stdin branch unchanged (returns `False`).
   - `weft/cli/app.py:2452-2466`: wrap the interactive drive and
     `consume_run_session` in `try/finally: session.close()` so the session is
     closed on every path (Claude R2-5, Codex R2-2); `quit_requested =
     drive_interactive_session(...) if interactive and wait else False`; pass
     `quit_requested=quit_requested` to `render_run_result`. The consuming
     `session.wait()` inside `consume_run_session` therefore runs **after**
     the bounded drain-join, preserving today's render-before-consume order.
   - Post-close streams (Codex R3-2 / Claude R3-1): `_LiveRunSession.events()`
     checks `_closed` **under the same lock** that `close()` takes to set it
     and to cancel registered streams; after `close()` it raises
     `CommandUsageError("run session is closed")` instead of creating a fresh
     stream with an unset cancel event (a stream created after `_closed`
     would follow forever on a task with no terminal proof). Registration
     and close are serialized by that lock so a concurrent `events()` cannot
     race past `close()`. The race test must be deterministic, not
     two-threads-and-hope: give `_LiveRunSession` a test-only hook called
     **inside** the registration critical section (after the `_closed` check,
     before the stream is appended; default no-op), have the test block on a
     `threading.Event` there while a second thread calls `close()`, assert
     `close()` has not returned while the hook is blocked (the lock holds it),
     release, and assert the registered stream is cancelled by that `close()`
     — that is the **registration-wins** schedule. Add the **close-wins**
     schedule too (Codex R4-5): a second test-only hook called by `events()`
     *before* it acquires the lock (default no-op); the test blocks there,
     lets `close()` run to completion on another thread, releases, and
     asserts `events()` raises the typed closed-session error and registers
     nothing. The two schedules are behavioral proofs but neither
     discriminates every unlocked read (an implementation that reads
     `_closed` unlocked *after* the pre-lock hook passes both — Codex R6-1),
     so add the **structural proof** as a source-shape test in
     `tests/architecture/` (the repo's existing AST-test home, next to
     `test_import_boundaries.py`): parse `weft/commands/run.py`, find
     `class _LiveRunSession`, and assert that every `ast.Attribute` load of
     `_closed` (`ctx` is `ast.Load`) inside the class body is lexically
     enclosed by a `with self._lock:` block (walk parents; the `with`
     item's context expression is `self._lock`). No accessor, no lock
     wrapper, no production instrumentation — production code keeps the
     plain `threading.Lock` and the plain attribute. Then mutation-test the
     exact Codex R6-1 shape once, by hand during task 4b: insert a direct
     `if self._closed:` read after the pre-lock hook and confirm the AST
     test fails, then remove it (record the run in the closeout). Any
     unlocked read of `_closed`, wherever it sits, fails the AST test; the
     two schedules remain the behavioral proofs. The two hooks are private,
     default no-op, and exercised only by the two schedule tests. Adapter change (Codex R4-6): `drive_interactive_session` must call
     `session.events()` on the **main thread before starting the output
     thread** and hand the iterator to `_stream_output`, so a
     `CommandUsageError` from a closed session surfaces in the adapter's
     normal error path instead of escaping a daemon thread to
     `threading.excepthook`; the thread body narrowly catches the
     closed-session error as a clean exit. Cover with the existing
     `thread_exception_guard` helper.
     [PY-2] gains the sentence recorded in the delta below. `wait()` is
     never called after `close()` in the 4b teardown order, so its
     post-close behavior stays undefined and unchanged.
     `ControlRejected`/`CommandExecutionError`/`TaskNotFound` from
     `request_exit` already map through the existing `except (commands.WeftError,
     ValueError)` → `_command_exit` (one line; `_command_error_code()` at
     `weft/cli/app.py:75` maps `TaskNotFound` to exit **2** and the others to
     exit 1).
   - `weft/cli/run.py::render_run_result(..., quit_requested: bool = False)`:
     before the status switch, `if quit_requested and status in {"cancelled", "killed"}:
     status, result_value, error_message = "completed", None, None` — the
     adapter-level form of the retired `outcome()` normalization; JSON and
     text outputs then follow the existing `completed` branch (matches the
     pre-`8cc12299` bytes). Whether the EOF entry sets `quit_requested` is
     Open Owner Question 5; the default implementation sets it for both
     entries and case 2 pins it.
   - Renderer unit test next to `tests/commands/test_run.py:94`:
     `test_run_renderer_quit_requested_normalizes_cancelled_and_killed` —
     parametrize status ∈ {`cancelled`, `killed`} × json ∈ {False, True}; exit 0
     and no error line; and `failed`/`timeout` with `quit_requested=True` keep
     exit 1/124.
   - Session unit test with a real broker (`tests/commands/test_run_public.py`):
     `test_run_session_close_cancels_executing_event_stream` — start a thread
     iterating `session.events()` on a running harness task, call
     `session.close()` from the test thread, assert it does not raise and the
     iterating thread ends within `CONTROL_SURFACE_WAIT_TIMEOUT`.
   - Spec edits in the same commit: [CLI-1.1.1] :295-302 replacement; [PY-2]
     :83-86 replacement; backlinks at 10-CLI :1032 **and 14-PY :246**;
     `Spec:` backlinks in the `request_exit`, `drive_interactive_session`,
     `render_run_result` docstrings.
   - Forbidden: a second ladder in `weft/cli/run.py`; sending control messages
     from the adapter; a new constant; changing `stop()` semantics; normalizing
     the [PY-2] result status inside the command layer; consuming the outbox
     from `request_exit`; a new thread-management helper, lock wrapper, or stream wrapper
     class (a `threading.Event` per stream is the whole mechanism).
   - Stop if: the harness platform's mapping does not scope the command child
     (case 3 parent resolution invalid) — report the observed mapping and
     decision trace rather than loosening the assertion; or `task_status`
     cannot see the log-published `cancelled` state before the task's
     ctrl_out envelope (contradicts probe A) — report.
   - Done when: all 4a cases and the wiring test are green; the empty-pipe
     payload regression (rung 0) is green and `grep -n "if stdin_text:"
     weft/commands/submission.py` no longer matches the interactive branch;
     both race-schedule tests are green;
     `tests/commands/test_run_public.py` green; `tests/specs/` green;
     baseline identifier recorded.

   **4c — Delete the dead stack (atomic B with the [MF-3]/[SB-0.4] mappings).**
   Per §3b: remove `execute_run`'s `wait` param and the `_execute_*` `wait`
   params; collapse `_run_with_managed_execution` to the immediate-return form
   (keep the submission-failure `except` stop, delete the `finally`); delete
   `_wait_for_inline_completion`, `_wait_for_task_completion`,
   `_run_interactive_session`/`_prompt`/`_piped`, `_InteractiveRunLifecycle`;
   delete `weft/commands/interactive.py`; delete
   `_streaming.py::collect_interactive_queue_output` (+ `__all__` entry) and its
   two direct tests; rewrite the `run.py` module docstring; remove now-unused
   imports; empty the seam allowlist and relax the nonempty assertion; delete
   `tests/commands/test_interactive_exit_terminal.py` and
   `tests/commands/test_interactive_client.py`; delete the listed private tests
   and helper classes in `tests/commands/test_run.py`; drop the removed
   `wait=` kwargs at the fourteen inventoried sites and the `test_run_public.py:157`
   assertion; rewrite the persistent guard (:1274-1277) to
   `session_wait_requested` only. Apply the [MF-3] and [SB-0.4] deltas and
   backlinks (05 :1525, 04 :724) in the same commit.
   - Behavior-parity ledger (why each pinned behavior survives): see the
     critical design analysis table below; every "ported" row is green in 4a
     before this commit.
   - Stop if: `InteractiveStreamClient` turns out to have a non-test consumer,
     or `MultiQueueWatcher` is affected; report.
   - Done when: `weft/commands/interactive.py` is gone; the §7 residue checks
     match exactly; `tests/specs/` (incl. the seam guard) green; backstitch or
     equivalent shows no new warning/error on 10/05/04/14; baseline identifier
     recorded.

5. **Docs, CHANGELOG, and traceability reconciliation.**
   - `docs/agent-context/runbooks/testing-patterns.md` (:30): replace the stale
     `-k manager_reuse` example with a real selector (e.g. `-k interactive`
     against `tests/cli/test_cli_run.py`) or delete the line.
   - `CHANGELOG.md` under `## Unreleased`: `### Removed` (add if absent) — the
     env var is removed and now fails fast at `load_config()` (env and override
     forms) with a one-line CLI diagnostic; operators must unset it (and
     embedders `ENGRAM_MANAGER_REUSE_ENABLED`) before upgrade; `weft run` never
     stops a manager on task completion (unchanged in practice since
     2026-08-12); the private synchronous-wait / interactive-client command
     internals were retired. `### Added` — `RunSession.request_exit()` bounded
     operator-exit ladder. `### Fixed` — `weft run --interactive` no longer
     hangs on `:quit`/EOF when the child ignores EOF; an operator-requested
     `:quit`/`:exit` again exits 0 (and, per Open Owner Question 5, EOF at the
     prompt is stated as the new policy it is, not a restoration); the
     session's event stream and broker handles are released on every
     interactive exit path; removed configuration keys render one line
     instead of a traceback.
   - Reconcile any remaining `_Implementation mapping_` / `_Implementation
     snapshot_` notes in the touched specs that still name removed symbols
     (grep 10/05/04/14 for `interactive.py`, `_InteractiveRunLifecycle`,
     `InteractiveStreamClient`, `execute_run(`, `collect_interactive_queue_output`);
     close the deviation log; rerun the full gates (§7).
   - Do not edit completed historical plans (immutable at closure).

### Critical design analysis — does the live path carry every behavior the private tests pin?

| Pinned behavior (private test) | On the live path at `178e3a34`? | Resolution |
|---|---|---|
| Interactive **EOF → exit** (`test_interactive_exit_waits_for_real_eof_ignoring_child`; `_run_interactive_piped` auto-close) | **No** for an EOF-ignoring child: `close_input()` only; task closes stdin only; CLI hangs in `output_thread.join()` | **Ported** (4b): `request_exit` on EOF; proven by 4a case 2 (EOF entry). |
| **STOP budget `INTERACTIVE_STOP_COMPLETION_TIMEOUT` before KILL** (`test_interactive_exit_uses_full_stop_budget_before_escalation` — F5; `test_interactive_quit_escalates_stop_before_kill`) | **No** — live `:quit` sends STOP via `cmd_task_stop` (bounded observation + runner fallback) then waits **without a budget**; no KILL rung | **Ported** (4b): STOP → budget → `cmd_task_kill` → bounded wait → error; proven by 4a cases 2 (STOP suffices) and 3 (KILL rung: `ts(KILL) − ts(STOP) ≥ budget` on the real `ctrl_in`, then the specified exhaustion error). Register row 3 = N. |
| Terminal proof accepted before delayed ack (`test_interactive_exit_uses_terminal_proof_without_waiting_for_ack`) | Yes (task-side + convergence) | `request_exit` exits on non-consuming `task_status` terminal proof, never on acks; 4a records acks only as evidence-if-present. |
| Intentional `:quit`/`:exit` normalized to success (`outcome(quit_requested=True)`, `test_interactive_quit_escalates_stop_before_kill` asserts `("completed", None, None)`) | **No** — `cancelled` → exit 1 since `8cc12299` | **Restored in the adapter** (4b, Open Owner Question 2); proven by the renderer test and 4a cases 1-2. EOF was never normalized (`run.py:916-926`) — its treatment is new policy, Open Owner Question 5. |
| Prompt-mode resource release on exit (`test_interactive_start_failure_closes_owned_resources`; `_InteractiveRunLifecycle.close`) | **Partly** — `consume_run_session` closes on success only; an exception from the drive skips `session.close()`, and `close()` on an executing stream raises | **Fixed** (4b): cancel-event streams, guarded close, `try/finally` in `app.py`; proven by 4a case 3(e), the wiring test, and the session close test. |
| **Result failure / timeout surfaced; wait deadline** (private sync-wait tests) | Yes | `_LiveRunSession.wait()`; covered by `tests/commands/test_run_public.py::test_run_session_wait_propagates_deadline_timeout` and `::test_run_session_wait_preserves_terminal_task_timeout`. |
| **Idempotent `close()`** (`_InteractiveRunLifecycle.close`; start-failure resource-close tests) | Yes | `_LiveRunSession.close()` guarded by `self._closed`; `::test_cmd_run_wait_returns_a_session` closes twice. [PY-2]. |
| **Piped result precedence; monitor-store read failure tolerated** (`test_interactive_piped_result_precedence`, `test_interactive_completion_ignores_unexpected_monitor_store_failure`) | N/A | Private result-collection internals of the deleted class; the live path collects via `await_task_result` and streams via `iter_task_realtime_events`, whose result/monitor fallbacks are covered in `tests/commands/test_result.py`. No specified behavior lost. |
| `_wait_for_task_completion` outbox-after-completion / aggregate / timeout | Yes (same helper) | Thin wrapper over `await_one_shot_result`; covered by `tests/commands/test_result.py::test_await_one_shot_result_*` / `::test_await_single_result_*`. |

Conclusion: four specified or previously-tested behaviors (bounded EOF exit,
STOP-then-KILL budget, `:quit` → success, resource release on every exit
path) are absent or incomplete on the live path today and are ported/restored
by task 4b with live-path proofs in 4a; everything else is already covered on
the live path. No behavior is dropped by reconciliation.

## 6. Testing Plan

- Harness/fixtures: `WeftTestHarness` + `ensure_foreground_manager()` for the
  live-path interactive tests (4a) — the harness manager is the single
  execution owner; `broker_env`/real `Queue` for constants; subprocess
  (`_run_module`) for the CLI boundary tests.
- What must **not** be mocked: the broker, `WeftContext`, the manager and the
  spawned task process, `cmd_task_stop`/`cmd_task_kill` and their convergence,
  `_LiveRunSession`, `iter_task_realtime_events`, `render_run_result`,
  `load_config()`'s real rejection path, `bootstrap.main`. Mock only the
  `PromptSession` TTY boundary (4a) and `os.environ` via `patch.dict` (unit
  constants tests).
- Red-green:
  - Task 3: the four-cell unit test and both subprocess tests are red before
    the constants/bootstrap edits (class and message undefined; traceback
    multi-line), green after.
  - Task 4a → 4b: cases 2-3 are red (worker join times out) before the port,
    green after; case 1 is red only on the exit-code and thread assertions;
    the wiring test is red (`close()` not called); all stay green across the
    4c deletion (characterization).
- Enumerable contracts with a firing test:
  - Env var `WEFT_MANAGER_REUSE_ENABLED` → environment and override rejection
    (unit) and process-env and env-file CLI rendering (subprocess).
  - `RunSession.request_exit` → 4a cases 1-3 (terminal `completed`,
    `cancelled`, and the exhaustion `CommandExecutionError`).
  - Control message types STOP and KILL on the exit ladder → 4a cases 2 (STOP
    only) and 3 (STOP then KILL after the full budget, measured on `ctrl_in`).
  - `weft run --interactive` exit code for operator-requested exit → renderer
    test (cancelled/killed × text/json) + 4a cases 1-2 (both entries).
  - `RunSession` idempotent `close()` and `wait()` deadline → existing
    `test_run_public.py` cases (retained); `close()` against an executing
    stream → new session close test (4b).
  - Session release on every adapter exit path → wiring test (error path) and
    4a case 3(e) (exhaustion path).
- Edge cases in scope: `:quit` after the child already exited (ControlRejected
  path inside `request_exit`) — add a unit test on `_LiveRunSession` with a
  real broker where the task is already terminal (build via the harness, wait
  for `completed`, then call `request_exit()`; assert it returns without
  raising and `wait()` returns the terminal result). Task-side termination of
  a hung child is already proven by
  `tests/tasks/test_task_interactive.py::test_interactive_command_control_unwinds_before_terminal_and_ack`
  (:225) and is not re-proven here.
- Edge cases documented, not tested: `:quit` typed before the manager has
  registered the tid mapping raises `TaskNotFound` from `stop()` (pre-existing;
  one-line exit **2** per `_command_error_code()`, `app.py:75`; the task later
  runs orphaned — §9); a `failed`/`WRAPPER_LOST_ERROR`
  proof authored by the manager after a task process exits mid-unwind renders
  as `failed` (exit 1) even with `quit_requested=True` — truthful by design.
- Edge case deliberately out of scope: parser `ValueError`s for malformed
  values rendering as tracebacks (§9).

## 7. Verification and Gates

Per task (fast):
```bash
. ./.envrc
./.venv/bin/python -m pytest tests/system/test_constants.py tests/cli/test_env_file_bootstrap.py -q
./.venv/bin/python -m pytest tests/commands/test_run.py tests/commands/test_run_public.py tests/commands/test_result.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_run.py -q -k "interactive"
./.venv/bin/python -m pytest tests/specs/ -q
```
Final gates (before claiming done):
```bash
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
```
Exact allowed-residue checks (scoped to `weft/`, `tests/`, `bin/`,
`docs/specifications/`, `docs/agent-context/`, `README.md`, `CHANGELOG.md`;
`docs/plans/` legitimately keeps every name):
```bash
grep -rn "WEFT_MANAGER_REUSE_ENABLED" weft tests bin docs/specifications docs/agent-context README.md CHANGELOG.md
```
Expect exactly: `weft/_constants.py` (the message constant, the removed-env map
entry, the `_WEFT_OVERRIDE_RULES` REMOVED entry), `tests/system/test_constants.py`
(the rejection test), `tests/cli/test_env_file_bootstrap.py` (the two
subprocess tests), `CHANGELOG.md` (the release note). Nothing else.
```bash
grep -rn "reuse_enabled\|InteractiveStreamClient\|_InteractiveRunLifecycle\|_run_interactive_session\|_wait_for_task_completion\|collect_interactive_queue_output" weft tests bin docs/specifications docs/agent-context
```
Expect: zero. And for the constant that keeps a consumer:
```bash
grep -rn "INTERACTIVE_STOP_COMPLETION_TIMEOUT" weft tests docs/specifications
```
Expect exactly: `weft/_constants.py` (definition), `weft/commands/run.py`
(import + `request_exit`), `weft/commands/types.py` (Protocol default, if
imported), `tests/system/test_constants.py` (composition pin),
`docs/specifications/10-CLI_Interface.md` and `14-Python_API_Surfaces.md`
(the promoted text).

Traceability: run backstitch (or the configured equivalent) before task 2 and
after task 5; acceptance is **no new warning or error on the touched surfaces**
(10, 05, 04, 14, 00; `weft/commands/run.py`, `weft/cli/run.py`,
`weft/commands/types.py`). Aggregate baseline debt is not this plan's to clear.

Observable success beyond local tests: `weft run --interactive` streams output,
exits 0 on `:quit`/Ctrl-D with a cooperative child, exits 0 (`:quit`) or per
Open Owner Question 5 (Ctrl-D) with an EOF-ignoring child within
`INTERACTIVE_STOP_GRACE_SECONDS + INTERACTIVE_STOP_COMPLETION_TIMEOUT`, and
returns with an explicit one-line error within
`INTERACTIVE_STOP_GRACE_SECONDS + INTERACTIVE_STOP_COMPLETION_TIMEOUT +
3 × CONTROL_SURFACE_WAIT_TIMEOUT + 5.0` (21.7 s) with a task host process that cannot
be scheduled — releasing its event stream on every path;
`weft run` (wait and no-wait) is unchanged; exporting
`WEFT_MANAGER_REUSE_ENABLED` prints one `weft: …` line, exit 1, no traceback,
no state created. Rollback: the code/spec change is revertable; there is no
persisted-data one-way door. The only irreversible effect is the config
compatibility break, which is intended (no shim).

## 8. Independent Review Loop

External review is run by the top-level agent using a different agent family
(Codex CLI is available on this machine) plus a Claude reviewer, with the
Planning Review Prompt from
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`. Round 1 is
recorded below; this revision re-enters review before task 2. Review again
before each atomic B slice lands (tasks 3, 4b, 4c) and before completion.

Reviewer required reading:
- This plan: §3b live-path facts, the critical design analysis, the Review
  Record, and the Observable-Difference Register.
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1] (:100-125, :283-302),
  [CLI-1.2.3]; 05-Message_Flow_and_State.md [MF-3]; 04-SimpleBroker_Integration.md
  [SB-0.4]; 14-Python_API_Surfaces.md [PY-1], [PY-2]; 03-Manager_Architecture.md
  [MA-1.5] and :555-575; 07-System_Invariants.md [MANAGER.7], [MANAGER.8];
  00-Quick_Reference.md environment table and `## Related Plans`.
- `weft/cli/run.py` (whole file, 190 lines), `weft/cli/app.py:2400-2470`,
  `weft/commands/run.py` (`_run_with_managed_execution` :146-210,
  `_LiveRunSession` :367-441, `_InteractiveRunLifecycle.request_exit` :700-740,
  `outcome` :774-780, `execute_run` :1429, `cmd_run` :1569-1644),
  `weft/commands/types.py:79-89`, `weft/commands/tasks.py` (`task_status` :442,
  `_await_control_surface` :1357, `_stop_via_fallback` :1465, `_kill_via_fallback`
  :1491, `_kill_success_is_proven` :1560, `stop_tasks` :1595,
  `_require_controllable_task` :1665, `stop_task` :1678, `kill_tasks` :1695,
  `_task_control_result` :1982, `cmd_task_stop` :2060, `cmd_task_kill` :2083),
  `weft/commands/events.py` (:52-53, :270-277, :505-535),
  `weft/commands/result.py:719-745`, `weft/commands/submission.py:192-207`,
  `weft/core/runners/host.py:1248-1266`, `weft/helpers/__init__.py:403-436`,
  `weft/core/manager.py` (:3458-3484, :3545-3560, :3586-3600),
  `weft/core/tasks/base.py:2327-2341`, `weft/core/tasks/interactive.py`
  (:185-215, :452-513), `weft/core/tasks/sessions.py:766-830`,
  `weft/bootstrap.py:93-118`, the probes under the session scratchpad
  `plans/0119-rev2/` (`probe-live-stop-kill.txt`, `probe-pids-and-reap.txt`,
  `probe-sigstop-task-process.txt`, `probe-generator-close.txt`),
  `weft/_exceptions.py`, `weft/_constants.py` (:695, :1864, :1880, :1907-1930,
  :2796-2817, :2885-2889, :3106-3145, :3203-3206, :3296-3306).
- Tests: `tests/commands/test_run_public.py`, `tests/commands/test_interactive_exit_terminal.py`,
  `tests/cli/test_env_file_bootstrap.py:190-231`, `tests/cli/test_cli_run.py`
  (:44-62, :2405-2440), `tests/helpers/weft_harness.py` (:198-240, :664-672,
  :1020-1034), `tests/specs/test_command_queue_seam.py`, `tests/system/test_constants.py`
  (:333-340, :770-777, :1307-1323).

Review stance: confirm (a) the flag is truly dead on the public path; (b) the
ported ladder is the specified behavior, bounded at every rung, implemented
once, observes proof without consuming, and reuses only
`cmd_task_stop`/`cmd_task_kill`/`task_status`; (c) 4a's three cases are
implementable with the harness manager as the sole execution owner and prove
STOP/KILL evidence strictly — in particular that case 3's SIGSTOP-on-the-task-
host-process fixture reaches the KILL rung with no doubles and that the
exhaustion error (not `killed`) is the only reachable strict outcome; (d) the
stream cancel → join → close ordering holds on every success and error path;
(e) the exit-code restoration is correctly placed in the adapter and the EOF
policy is correctly labelled new; (f) the boundary-safe exception renders one
line for both input forms without widening the catch; (g) the spec deltas are
exact, correctly labelled, and leave no dangling mapping or missing backlink
(00, 10, 05, 04, 14). Round 3 is a scoped verification of the revision-2
delta (Revision Log). Feedback returns to the author, who dispositions each
point in the Review Record.

## 9. Out of Scope

- Renaming `PARALLEL_MANAGER_REUSE_*` / `*_manager_reuse_*` test symbols
  (descriptive of manager adoption, not the flag).
- Rendering parser `ValueError`s for malformed `WEFT_*` values (e.g.
  `WEFT_MANAGER_LIFETIME_TIMEOUT=bogus`, reproduced as a traceback at
  `178e3a34`) as one-line diagnostics — same boundary, separate change;
  candidate follow-up once `WeftConfigError` exists.
- Removing `INTERACTIVE_STOP_POLL_INTERVAL` (used task-side at
  `weft/core/tasks/interactive.py:476`, and after 4b by
  `_LiveRunSession._observe_terminal`).
- Removing the pre-existing unused `weft/commands/run.py:492 _drain_stream_queue`
  helper (no drive-by).
- Applying the exit ladder to piped-stdin interactive mode. Evidence: the live
  piped branch sends the input with `{"close": true}` in the initial payload
  (`weft/commands/submission.py:192-207`) and then waits without a budget in
  `consume_run_session` → `wait()`; the retired `_run_interactive_piped`
  (`run.py:935-955`) also waited without a budget (`wait_for_completion()`,
  `timeout=None`). Neither path ever had a ladder there, so scoping the
  promoted [CLI-1.1.1] text to prompt mode is a faithful port (Claude R2-4)
  and no diminution; a bounded piped exit is a candidate follow-up.
  **Empty piped stdin is the exception that must be handled in-plan** (Codex
  R3-3): `read_run_stdin()` (`weft/cli/run.py:32-38`) normalizes empty piped
  input to `None`, and `drive_interactive_session()` treats `None` as prompt
  mode, so `printf '' | weft run --interactive …` would run the new EOF
  ladder. 4b must carry the TTY-versus-pipe state separately from the text
  (e.g. `read_run_stdin()` returns `""` for an empty pipe and only `None`
  for a TTY, with the piped branch accepting the empty string), keep empty
  piped input on the piped path, **and** fix the payload builder:
  `weft/commands/submission.py::_initial_work_payload` (`:194-207`) uses
  `if stdin_text:` so an empty string yields `{}` and the interactive child
  waits forever for a close; 4b changes the interactive branch to
  `if stdin_text is not None:` so an empty pipe sends
  `{"stdin": "", "close": True}` (the non-interactive branch keeps its
  truthiness check). Add an empty-pipe regression test asserting that exact
  payload on the inbox and that no STOP/KILL envelope is written.
- Closing the `TaskNotFound` race for `:quit` typed before the manager has
  registered the tid mapping (`tasks.py:1665-1672`; pre-existing on the live
  `stop()` path; documented in §6).
- Any change to `MultiQueueWatcher`, `iter_task_realtime_events`, task-side
  interactive execution, `stop()` semantics, or manager lifecycle rules.
- Adopting a `status-review` plan status.

## 10. Fresh-Eyes Review

Author pass, 2026-09-08 (first draft), findings by severity:

- **(High) Deleting `_InteractiveRunLifecycle` strands a second module.**
  Re-grep showed `weft/commands/interactive.py::InteractiveStreamClient` is
  used in production only by `_InteractiveRunLifecycle`, is named in [SB-0.4],
  and carries a seam-allowlist exception. Added it (and its test, the [SB-0.4]
  deltas, and the seam edit) to §3b/§5.
- **(High) The specified interactive exit behavior is not on the live path.**
  First draft reconciled the spec down to the live behavior. **Superseded in
  revision 1**: both reviewers established the ladder exists only on the dead
  path and the owner's rule forbids diminution; the ladder is now ported (§3b,
  task 4b) and the spec text is strengthened, not weakened.
- **(Medium) Harness env edit must be atomic with the REMOVED rule.** Called
  out as a hard ordering constraint in §3a and task 3.
- **(Medium) Do not over-remove `wait`.** Explicit "do not touch" list in §3b.
- **(Low) Cross-repo break via engram.** Resolved: document, no shim (§3a).

Author pass, 2026-09-08 (revision 1, after round-1 review), findings by severity:

- **(High) Live EOF handling hangs today.** Traced `drive_interactive_session`
  → `close_input()` → task `close_stdin()` only; no terminal event; unbounded
  join. This is a live defect the first draft would have ratified by
  reconciliation. Fixed by the port; pinned by 4a case 2.
- **(High) Removed keys traceback at the CLI boundary.** Reproduced for the
  existing removed-key family via process env and `WEFT_ENV_FILE`. Fixed with
  one exception class at the two shared raise sites; pinned by two subprocess
  tests.
- **(Medium) Where the ladder lives.** Considered the adapter (rejected: control
  policy in presentation, two owners), `stop()` (rejected: changes the graceful
  [PY-2] contract), and a free command function (rejected: needs the session's
  private context). A new session member is the one-owner answer; it is a
  public additive change and is escalated (Open Owner Question 3).
- **(Medium) The KILL rung cannot be exercised with a signal-ignoring child**
  because task-side `terminate()` escalates to `kill()`. Case 3 therefore
  SIGSTOPs the task process; assertions accept either terminal `killed` or the
  explicit error, both within a strict bound. *(Superseded in revision 2: the
  PID that design stopped was the command child, and `killed` is
  unreachable — see the revision-2 pass below.)*
- **(Low) `time` import.** `request_exit` keeps `time.monotonic`, so `time`
  is not removed in 4c; the import list in §3b says "verify".

Author pass, 2026-09-08 (revision 2, after round-2 review), findings by severity:

- **(High) The SIGSTOP fixture as written could not reach the KILL rung.**
  Reproduced: the mapping scopes the command child, not the task host
  process; stopping the child converges through STOP; the runner's own
  `stop()` escalates to SIGKILL. Redesigned around a reproduced real-process
  fixture (SIGSTOP the *parent* of the scoped child) with no doubles; strict
  evidence is the KILL envelope after the full budget plus the specified
  exhaustion error. Corrected a premise carried in the round-2 brief: a
  task-authored `killed` after the STOP rung is impossible by construction.
- **(High) Stream teardown was unordered and leaked on the error path.**
  Reproduced (`generator already executing`; `close()` skipped when the drive
  raises). Fixed with per-stream cancel events, guarded close, fixed
  drain-join → cancel → join → consume order, and `try/finally` in the
  adapter; three proofs added (case 3(e), wiring test, session close test).
- **(Medium) `request_exit` consumed the outbox while the renderer peeked.**
  Observation moved to non-consuming `task_status`; `wait()` is called once,
  after the drain-join; `request_exit` now returns `None`.
- **(Medium) Exit-0 rationale overclaimed EOF.** Corrected to `:quit`/`:exit`;
  EOF is new policy (Open Owner Question 5).
- **(Low) Error text referenced undefined names; missing 14 backlink;
  [MANAGER.8a]; piped-mode scope; `TaskNotFound` race.** All fixed or
  documented (§5 4b, Proposed Spec Delta, §9, §6).

## Open Owner Questions

1. **Settled (revision 2): port the interactive exit ladder rather than
   reconcile the spec.** Both round-2 reviewers agree the owner's
   no-capability-diminution rule decides this; the ladder is ported (task
   4b). Kept as item 1 so the cross-references in the Review Record and §10
   stay valid; no owner action.
2. **Intentional `:quit`/`:exit` exit code (recommended default: restore exit
   0).** Before `8cc12299`, an operator-requested `:quit`/`:exit` ending
   `cancelled`/`killed` rendered as `completed` → exit 0
   (`outcome(quit_requested=True)`, `run.py:774-780`, tested by
   `test_interactive_quit_escalates_stop_before_kill` asserting
   `("completed", None, None)`). Since `8cc12299` the live path exits 1 with
   "Task cancelled". The plan restores exit 0 in the adapter
   (`weft/cli/run.py::render_run_result(quit_requested=...)`, wired from
   `weft/cli/app.py:2452-2466`), keeping the [PY-2] result truthful; tests:
   the renderer case in `tests/commands/test_run.py` and 4a cases 1-2.
   Alternative: keep exit 1 and promote that as the contract (a class-A
   change that must then be recorded as an owner decision). This question
   covers `:quit`/`:exit` only; EOF is question 5.
3. **Add `request_exit()` to the public `RunSession` Protocol (recommended
   default: yes, additive).** `drive_interactive_session` is typed on the
   Protocol (`weft/commands/types.py:79-89`), and [PY-2] enumerates the
   members. Adding one member is additive for consumers (weft returns the
   session; embedders do not implement it). Alternative: keep it private on
   `_LiveRunSession` and `cast` in the adapter — leaves the specified ladder
   off the public surface and couples the adapter to a private class; not
   recommended.
4. **Error class when the ladder is exhausted (recommended default:
   `CommandExecutionError`, CLI exit 1).** The dead path raised
   `RuntimeError("Interactive session did not stop after :quit")`.
   `CommandExecutionError(CommandError, RuntimeError)` keeps that lineage and
   maps to exit 1 via `_command_exit`. Alternative: `CommandTimeoutError`
   (exit 124) — rejected as a default because 124 is the task-timeout code
   and this is a control failure, not a task timeout.
5. **EOF at the prompt (Ctrl-D) that ends `cancelled`/`killed`: exit 0 or
   exit 1? (recommended default: exit 0, the same as `:quit`, stated as new
   policy.)** This is not a restoration: the retired prompt loop set
   `quit_requested` only for `:quit`/`:exit` (`run.py:922-926`) and on
   `EOFError` only closed input (:916-918); an EOF-ignoring child then hung
   the old path and hangs the live path today, so no prior exit code exists
   for this case. With the ladder ported, EOF becomes an operator-requested
   bounded exit exactly like `:quit`; rendering it as success keeps the two
   operator gestures consistent and matches the cooperative-child case, where
   EOF already ends `completed` → exit 0. Alternative: keep `cancelled` →
   exit 1 for EOF only (truthful about the mechanism, but the same gesture
   would then exit 0 or 1 depending on whether the child honours EOF). The
   default implementation sets `quit_requested=True` for both entries; 4a
   case 2's EOF cell pins whichever answer the owner gives.

Resolved (no owner action): engram's `ENGRAM_MANAGER_REUSE_ENABLED` mapping —
document in CHANGELOG, no shim, no coordinated release (owner no-shim rule;
both reviewers concurred). `InteractiveStreamClient` and
`collect_interactive_queue_output` are deleted (private, consumerless after
the port; both reviewers recommended deletion of consumerless private
artifacts). `INTERACTIVE_STOP_COMPLETION_TIMEOUT` is kept because the port
gives it a live consumer.

## Revision Log

- **2026-09-08 — revision 7 (after scoped round-7 review; non-material).**
  Structural `_closed` proof simplified to an AST source-shape test in
  `tests/architecture/`; accessor and owned-lock wrapper dropped.

- **2026-09-08 — revision 6 (after scoped round-6 review; non-material).**
  Structural lock-ownership proof for `_closed` reads added alongside the
  two race schedules.

- **2026-09-08 — revision 5 (after scoped round-5 review; non-material).**
  Payload-builder edit integrated into task 4b (rung 0) and its done gate;
  case-3 wrapper prohibition reconciled with the delegating spies plus a
  0.05 s clock-origin tolerance; close-wins race schedule added.

- **2026-09-08 — revision 4 (after scoped round-4 review; non-material).**
  Empty-pipe payload builder fixed (`is not None`), slack applied to both
  bounds, exact masked-ID timing expression, delegating-spy timing seam,
  deterministic registration/close race test, stream registered on the main
  thread before the output thread starts. Scoped round-5 verification
  requested.

- **2026-09-08 — revision 3 (after scoped round-3 review; non-material).**
  Timing assertion normalized from hybrid message IDs to seconds with a
  0.25 s tolerance; post-close `events()` defined as a typed error with
  registration/close serialized under one lock ([PY-2] sentence added);
  empty piped stdin pinned to piped mode with a regression; `TaskNotFound`
  exit code corrected to 2; slack and wording nits. Scoped round-4
  verification requested.

- **2026-09-08 — revision 1 (after round-1 review; re-enters review).**
  Material changes: (1) the interactive exit ladder is **ported** to the live
  path (`_LiveRunSession.request_exit`, bounded at every rung, reusing
  `cmd_task_stop`/`cmd_task_kill`) instead of reconciling [CLI-1.1.1] down to
  live behavior — Register row 3 moves from A to N; (2) intentional
  `:quit`/EOF **exit code restored to 0** in the adapter (default; Open Owner
  Question 2); (3) retired key fails at the CLI boundary through a new
  `WeftConfigError` rendered as one line (fixes the reproduced traceback for
  the existing removed-key family too); (4) spec delta no longer claims idle
  timeout / explicit stop are the only manager end-of-life paths; (5) 4a
  rewritten around the harness foreground manager as sole execution owner,
  with a readiness barrier, strict STOP/KILL evidence, and EOF-ignoring and
  non-responsive cases; (6) gates replaced with exact allowed-residue checks;
  full `wait=` caller inventory; empty seam allowlist; orphaned helper and
  tests deleted; (7) delta completed (10-CLI :107-108, `run.py` docstring,
  Quick Reference backlink, A/B/D labels corrected, path-qualified mapping
  symbols, traceability acceptance defined); (8) anchors corrected; (9)
  Review Record populated; promotion-identifier gating stated; engram note
  resolved as document/no shim. Reviewed baseline for this revision: the
  round-1 text at `178e3a34` (untracked plan file); the reviewer's unit is the
  delta above.
- **2026-09-08 — revision 2 (after round-2 review; requests a scoped round-3
  verification).** Every round-2 claim was reproduced against `178e3a34`
  before disposition (probes under the session scratchpad `plans/0119-rev2/`).
  Material changes: (1) **4a case 3 fixture redesigned** — the mapping scopes
  the command child, so the old SIGSTOP-on-`host_pids[0]` design converged
  through STOP; the new fixture SIGSTOPs the *task host process* (parent of
  the scoped child) with no monkeypatching, reaches the KILL rung
  deterministically, and pins the F5 full-budget rule as
  `ts(KILL) − ts(STOP) ≥ INTERACTIVE_STOP_COMPLETION_TIMEOUT` on the real
  `ctrl_in`; the strict outcome is the specified exhaustion
  `CommandExecutionError` — a task-authored `killed` after STOP is impossible
  by construction and is no longer an accepted alternative; case 2's proof is
  terminal `cancelled` + no KILL envelope, acks evidence-if-present; (2)
  **stream cancellation and ordering specified** — per-stream `cancel_event`
  into `iter_task_realtime_events`, `close()` sets cancel events then closes
  with the executing-generator guard, adapter order drain-join → cancel → join
  → single consuming `wait()` → idempotent `close()`, `try/finally` in
  `app.py`; `request_exit` now observes proof through non-consuming
  `task_status` and returns `None`; three new proofs (case 3(e), adapter
  wiring test, session close test); (3) **EOF exit-code policy separated**
  from the `:quit`/`:exit` restoration and escalated as Open Owner Question
  5 (default: same as `:quit`, stated as new policy); Open Owner Question 1
  marked settled; (4) 14-PY `## Related Plans` backlink added to the delta
  and to 4b; exhaustion error text uses `self.tid` and a budget named at
  entry; [MANAGER.8]/[MANAGER.8a] in item 7; [CLI-1.1.1] text scoped to
  prompt mode with piped mode recorded as unchanged (§9); `TaskNotFound`
  race documented (§6, §9); import keep-list corrected. **Round-3 scope
  requested:** verify only this delta — the case-3 fixture and its
  assertions against `tasks.py:1595-1655`/`:1695-1785`, the teardown ordering
  in 4b against `events.py:270-277`/`:531-535` and `app.py:2452-2466`, the
  [CLI-1.1.1]/[PY-2] replacement texts, and the Open Owner Question 5
  framing. Rounds 1-2 findings outside this delta are closed.

## Observable-Difference Register

| # | Observable change | Class | Verification against shipping default |
|---|-------------------|-------|----------------------------------------|
| 1 | `WEFT_MANAGER_REUSE_ENABLED` env/override now rejected at `load_config()` with a migration message | A (owner-decided removal) | Default was `reuse=True` (manager stays alive); runtime manager lifetime under defaults is unchanged. Only the (dead) opt-out disappears. Firing tests: task 3 unit cells + subprocess tests. |
| 2 | Removed configuration keys render one `weft: …` line, exit 1, instead of a traceback | C (no-loss change) | Same exit code, same message, no traceback, no state created; applies to the existing four removed keys too. Subprocess tests (task 3). |
| 3 | Prompt-mode `:quit`/EOF exit ladder: STOP within `INTERACTIVE_STOP_COMPLETION_TIMEOUT`, then KILL, then bounded wait, then explicit error — now on the live path | N (none vs the specified contract; a fix vs live code) | The specified [CLI-1.1.1] behavior was unreachable since `8cc12299`; the port restores it on the shipping path. 4a cases 2-3 (case 3 measures the budget on the real `ctrl_in`). |
| 4 | EOF with an EOF-ignoring child no longer hangs the CLI | C (no-loss change; defect fix) | Today: indefinite hang in `output_thread.join()`. After: bounded exit. 4a case 2 (EOF entry). |
| 5a | Operator-requested `:quit`/`:exit` ending `cancelled`/`killed` renders as success (exit 0) | C (restores pre-`8cc12299` behavior; reverses an unreviewed D) | Renderer test + 4a cases 1-2 (`:quit` cells). Open Owner Question 2. |
| 5b | EOF at the prompt ending `cancelled`/`killed` renders as success (exit 0) | C (new policy for a case that hangs today; no prior exit code existed) | 4a case 2 (EOF cell). Open Owner Question 5 — flips to exit 1 if the owner chooses the alternative. |
| 5c | Piped-stdin interactive mode | N (none) | Unchanged on both the retired and the live path (§9); [CLI-1.1.1] text scoped to prompt mode. |
| 6 | `RunSession` gains `request_exit()` ([PY-2]); `close()` now cooperatively cancels an executing event stream instead of raising | C (additive public change; close semantics per [PY-2] unchanged — releases owned resources, does not cancel the task) | Existing members unchanged; `test_run_public.py` retained + session close test. Open Owner Question 3. |
| 6a | Interactive session resources are released on every adapter exit path (including ladder exhaustion) | C (no-loss change; defect fix) | Today an exception from the drive skips `session.close()`. Wiring test + 4a case 3(e). |
| 7 | `execute_run(wait=True)` synchronous completion path removed | N (none) | Production already routes through `cmd_run` → `_LiveRunSession`; the removed path had no production caller. |
| 8 | Quick Reference env row + README bullet removed; [CLI-1.1.1]/[PY-2]/[MF-3]/[SB-0.4] text updated | A (row) / B-D (text) | Docs match code; `tests/specs/` guards pass; residue checks exact. |
| 9 | `weft/commands/interactive.py`, `collect_interactive_queue_output`, and their direct-only tests deleted | N (none, private internals) | Private per [PY-1]; zero production consumers after the port; no `../engram` use. |
| 10 | `weft run` never stops a manager on task completion (now stated in [CLI-1.1.1]) | N (none) | Already true in production since `8cc12299`; manager lifecycle rules unchanged. |

## Review Record (append-only)

**2026-09-08 — round 1, review of the first draft at `178e3a34` (Codex
cross-family: BLOCKED, 8 findings; Claude: PASS, 5 findings + escalation
verification).** Every finding was reproduced against the code before
disposition. Dispositions:

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex 1 (Critical) — retired key would traceback; bootstrap catches only `InvalidConfigError` | **Accepted** (reproduced: process env and `WEFT_ENV_FILE`, `weft status`/`--version`, full traceback exit 1) | §3a boundary-safe exception; task 3 subprocess tests; Register row 2 |
| Codex 2 (High) — delta claims idle timeout / explicit stop are the only end-of-life paths, contradicting [MANAGER.7]/[MANAGER.8] | **Accepted** | §1 closing paragraph; §4 invariant; [CLI-1.1.1] item 7 text; migration message |
| Codex 3 (High) — 4a has two execution owners, no readiness barrier, weak STOP evidence, drops the EOF-ignoring case | **Accepted** | Task 4a rewritten (harness manager sole owner; `running` barrier; strict ack/envelope assertions; cases 2-3) |
| Codex 4 (High) — gates contradict the implementation (zero-match grep, nonempty seam allowlist, surviving `wait=` callers, `test_run_public.py:157`, orphaned helper test) | **Accepted** (AST-verified the 14 surviving `wait=` sites) | §7 exact residue checks; §3b inventory; seam test relaxation; orphan deletion |
| Codex 5 (High) — delta incomplete (10-CLI :107-108, `run.py` docstring, Quick Reference backlink), wrong A/D labels, bare mapping symbols, traceability acceptance undefined | **Accepted** | Class line; Proposed Spec Delta table + texts; §3b docstring; §4 review gates |
| Codex 6 (High, human review) — `:quit` exit semantics undecided; unbounded join; EOF-ignoring child hangs; engram coordination | **Accepted**; escalated as Open Owner Questions 1-2 with defaults (port; restore exit 0); engram resolved document/no shim | §1 item 3; task 4b; Open Owner Questions; §3a |
| Codex 7 (Medium) — consumerless private artifacts kept only because tests exist | **Accepted in part**: `collect_interactive_queue_output` deleted with its two tests; `INTERACTIVE_STOP_COMPLETION_TIMEOUT` **kept** because the port gives it a live consumer | §3b; §9; Register row 9 |
| Codex 8 (Low) — three wrong anchors ([PY-1], persistent guard, CLI paragraph) | **Accepted** (verified :15-17, :1274-1277, :295-302) | §2, §3b |
| Claude 1 (Medium) — unbounded client wait after `stop_tasks` must be named or bounded | **Accepted** — the port bounds it; the promoted text says "never waits without a budget" | [CLI-1.1.1] delta; task 4b |
| Claude 2 (Medium) — task 1 cites review rounds absent from the Review Record | **Accepted** | Task 1; this record |
| Claude 3 (Low) — `:quit` on an already-terminal task raises `ControlRejected` (`stop_task` :1689-1690) | **Accepted** — `request_exit` catches it; case 1 child stays alive until the exit request; edge-case unit test added | Task 4b step 2; §6 |
| Claude 4 (Low) — anchor nits ([PY-1] :17; `close_input` ends :409) | **Accepted** | §2, §3b |
| Claude 5 (Info) — state that tasks 3-4 are gated on the promotion-identifier being recorded | **Accepted** | Spec Baseline |
| Claude escalation verification (F5 applied to the dead path only; live path has no KILL and no bound; "not a diminution because unreachable") | **Accepted as evidence; ruling not adopted**: the owner's rule treats a specified-but-undelivered bound as capability to keep, so the plan ports rather than reconciles | §1 item 2; Open Owner Question 1 |

**2026-09-08 — round 2, review of revision 1 at `178e3a34` (Codex
cross-family: FAIL, 6 findings; Claude: PASS, 5 findings).** Every finding
was reproduced against the code (and, for R2-1/R2-2, against live probes in
the session scratchpad `plans/0119-rev2/`) before disposition. Dispositions:

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R2-1 (P1) — SIGSTOP fixture cannot prove the KILL rung (runner `stop()` SIGKILLs survivors; wrong PID; manager `failed` proof; no Windows skip) | **Accepted** (reproduced: mapping scopes the command child; SIGSTOP on it converges through STOP; SIGSTOP on the task host process reaches KILL with no doubles and ends in the exhaustion error). Premise correction: a task-authored `killed` after STOP is impossible — strict evidence is KILL envelope after the full budget + `accept_dead_runtime` + `CommandExecutionError` | §3b facts; 4a case 3; comprehension check 3; §8 stance; §10 |
| Codex R2-2 (P1) — timed join does not cancel the stream; `close()` skipped on error; `generator already executing` | **Accepted** (both reproduced) | §3b facts; §4 ordered-teardown invariant; 4b (`cancel_event`, guarded close, join order, `try/finally`); 4a case 3(e), wiring test, session close test; Register rows 6/6a |
| Codex R2-3 (P2) — 14-PY `## Related Plans` backlink missing | **Accepted** | Delta table; [PY-2] delta; 4b spec edits; §2 |
| Codex R2-4 (P2) — exhaustion error formats undefined `tid`/`budget` | **Accepted** | 4b rung 5 (`self.tid`, `budget` named at entry) |
| Codex R2-5 (P3) — exit 0 is a restoration for `:quit`/`:exit` only; EOF → exit 0 is new policy | **Accepted** (reproduced: `run.py:916-926`) | §1 item 3; Open Owner Question 2 narrowed; Open Owner Question 5 added; Register row 5 split; [CLI-1.1.1] delta; CHANGELOG note |
| Codex R2-6 (nit) — cite [MANAGER.8a] for proactive supersession | **Accepted** | [CLI-1.1.1] item 7 delta |
| Claude R2-1 (P2) — "exactly one STOP ack" flaky: runner-stop fallback SIGKILLs the task 0.2 s after `cancelled` | **Accepted** (reproduced 3/3: `cancelled`, zero acks) | 4a case 2 (terminal `cancelled` + no KILL envelope; acks evidence-if-present); §4 |
| Claude R2-2 (P3) — consuming `wait(timeout)` races the peeking output thread | **Accepted** | 4b (`_observe_terminal` via `task_status`; one consuming `wait()` after the drain-join; `request_exit -> None`); [PY-2] delta; comprehension check 5 |
| Claude R2-3 (P3) — `TaskNotFound` when `:quit` precedes the mapping | **Accepted as documented**; the rung-1 observation is already the cheap re-check, no extra call | 4b rung 2; §6; §9 |
| Claude R2-4 (P3) — "end-of-input" wording covers piped mode, which never runs the ladder | **Accepted — scoped to prompt mode** (evidence: piped auto-close at `submission.py:192-207`; both retired and live piped paths unbounded; applying the ladder there is new behavior) | [CLI-1.1.1] delta; §4; §9; Register row 5c |
| Claude R2-5 (nit) — `session.close()` never runs if `request_exit` raises | **Accepted** (same fix as Codex R2-2) | 4b `app.py` `try/finally`; wiring test |
| Both reviewers — Open Owner Question 1 (port vs reconcile) is settled by the no-diminution rule | **Accepted** | Open Owner Questions (item 1 marked settled) |

**2026-09-08 — round 3 (scoped), review of revision 2 at `178e3a34`
(Codex cross-family: FAIL, 4 findings; Claude: PASS, 1 Medium + 2 Low +
2 Info).** Claude reproduced the KILL-rung fixture with a live probe
(`plans/reviews/r3_probe_sigstop.py`: mapping scopes the command child;
SIGSTOP on the task host → STOP accepted in 4.27 s with the child dead and
the host `stopped`; KILL accepted after the budget via `_accepts_dead_runtime`;
`ctrl_in` = `['STOP','KILL']`; `ctrl_out` empty). Codex confirmed the same
process claims. Dispositions (applied as revision 3):

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R3-1 (P1) — `ts(KILL) − ts(STOP) ≥ 8.7` compares raw hybrid message IDs (ns magnitude, low 12 bits logical) with a float, proving only nanoseconds | **Accepted** — normalize to seconds (mask the low 12 bits), tolerance 0.25 s, plus a ladder-side monotonic assertion | 4a case 3(a) |
| Codex R3-2 (P1) / Claude R3-1 (Medium) — post-close `session.events()` creates a fresh stream with an unset cancel event and would follow forever; registration can race `close()` | **Accepted** — `events()` after `close()` raises typed `CommandUsageError`; registration and close serialized under one lock; race test added; [PY-2] sentence | 4b (post-close rule), 4a case 3(e), [PY-2] delta |
| Codex R3-3 (P2) — "piped mode unchanged" is false for an empty pipe: `read_run_stdin()` normalizes `""` to `None` → prompt mode → the EOF ladder would run | **Accepted** (reproduced at `weft/cli/run.py:32-38`, `:152`) — carry TTY-vs-pipe state separately; empty pipe stays piped; regression test | §9 (now in-scope note), 4b, [CLI-1.1.1] delta |
| Codex R3-4 (P2) — `TaskNotFound` is exit 2 (`_command_error_code()`, `app.py:75`), not exit 1 | **Accepted** — both claims corrected | 4b, §6 |
| Claude R3-2 (Low) — timing margin near zero | **Accepted** (same fix as Codex R3-1) | 4a case 3(a) |
| Claude R3-3 (Low) — 18.7 s bound has 1.4 s slack | **Accepted** — slack 5.0 s | 4a case 3(d) |
| Claude R3-4 (Info) — [PY-2] silent on `wait()` after `close()` | **Accepted as documented**: never called after `close()` in the 4b order; no spec sentence | 4b note |
| Claude R3-5 (Info) — "non-persistent" wording | **Accepted** — clarified | 4a evidence helpers |

Revision 3 is non-material (test arithmetic, one typed post-close rule,
one input-mode guard, exit-code corrections). Scoped round-4 verification
requested.

**2026-09-08 — round 4 (scoped), review of revision 3 at `178e3a34`
(Codex cross-family: FAIL, 1 P1 + 5 P2).** Dispositions (applied as
revision 4):

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R4-1 (P1) — empty-pipe fix incomplete: `_initial_work_payload` (`submission.py:194-207`) uses `if stdin_text`, so `""` yields `{}` and the child can wait forever | **Accepted** (reproduced) — interactive branch uses `is not None`; exact `{"stdin": "", "close": True}` payload asserted | §9 note, 4b |
| Codex R4-2 (P2) — accepted 5.0 s slack not applied (both formulas still `+ 2.0`) | **Accepted** — both addends 5.0 (21.7 s) | 4a case 3(d), §7 observable success |
| Codex R4-3 (P2) — "normalized to seconds" wording wrong; masking yields nanoseconds; broker helper is private | **Accepted** — exact masked expression, single division by 1e9, no private helper | 4a case 3(a) |
| Codex R4-4 (P2) — ladder-side monotonic assertion had no observable seam under the mock prohibition | **Accepted** — delegating spy around the real `cmd_task_stop`/`cmd_task_kill` for case 3 only; prohibition list amended | 4a case 3(a), what-not-to-mock |
| Codex R4-5 (P2) — two-thread race test can pass with a broken implementation | **Accepted** — deterministic in-critical-section hook + `threading.Event` | 4b post-close rule |
| Codex R4-6 (P2) — new defect: `events()` still called inside the daemon thread; a closed-session `CommandUsageError` would reach `threading.excepthook` | **Accepted** — register on the main thread before starting the thread; narrow catch in the thread body; `thread_exception_guard` | 4b post-close rule |

Revision 4 is non-material (test seams, one payload-builder line, one
adapter ordering). Scoped round-5 verification requested.

**2026-09-08 — round 5 (scoped), review of revision 4 at `178e3a34`
(Codex cross-family: FAIL, 1 P1 + 2 P2; R4-2, R4-3, R4-6 closed as
verified).** Dispositions (applied as revision 5):

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R5/R4-1 (P1) — `submission.py` edit specified in §9 but absent from task 4b's files, steps, and done gate | **Accepted** — rung 0 added to 4b naming files, edit, and regression; done gate greps the old truthiness branch | 4b |
| Codex R5/R4-4 (P2) — case-3 prohibition still forbids all wrappers while (a) requires delegating spies; spy-side STOP time is later than the ladder's `stop_sent_at` | **Accepted** — prohibition amended to permit exactly the two delegating spies; assertion carries a 0.05 s tolerance (one poll interval) | 4a case 3 fixture text and (a) |
| Codex R5/R4-5 (P2) — registration-wins schedule alone lets a check-outside-lock implementation pass | **Accepted** — close-wins schedule added via a second pre-lock hook | 4b post-close rule |

Revision 5 is non-material. Scoped round-6 verification requested.

**2026-09-08 — round 6 (scoped), review of revision 5 at `178e3a34`
(Codex cross-family: FAIL, 1 P2; R4-1 and R4-4 verified clean).**
Disposition (applied as revision 6):

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R6-1 (P2) — close-wins schedule still passes `pre_lock_hook(); if self._closed: raise; with lock: register` | **Accepted** — structural proof added: `_closed` read only via one accessor; owned-lock wrapper records the owning thread; a third test asserts every read happened under the caller-held lock across both schedules | 4b post-close rule |

Revision 6 is non-material. Scoped round-7 verification requested.

**2026-09-08 — round 7 (scoped), review of revision 6 at `178e3a34`
(Codex cross-family: FAIL, 3 P2).** Dispositions (applied as revision 7):

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| Codex R7-1 (P2) — accessor-recording test misses a direct `self._closed` read after the pre-lock hook | **Accepted** — replaced by an AST source-shape test in `tests/architecture/` requiring every `_closed` load in `_LiveRunSession` to sit inside `with self._lock:`; the R6-1 mutation is checked by hand once during 4b | 4b post-close rule |
| Codex R7-2 (P2) — accessor described as no-op/test-only but serving production reads | **Accepted** — accessor removed entirely (the AST test needs none) | 4b |
| Codex R7-3 (P2) — mandated owned-lock wrapper contradicts the forbidden-inventions list | **Accepted** — wrapper removed; forbidden list now names lock wrappers explicitly | 4b, forbidden list |

Revision 7 is non-material. Scoped round-8 verification requested.

**2026-09-08 — round 8 (scoped), review of revision 7 at `178e3a34`
(Codex cross-family: PASS; R7-1..R7-3 verified by a read-only AST probe,
no new defect).** Together with the Claude round-3 PASS against revision 2
(whose findings were applied in revision 3), independent review of the plan
and its Proposed Spec Delta is complete from two agent families. The plan
is review-clean pending the Open Owner Questions (EOF exit code,
`request_exit` on the public Protocol, exhaustion error class).
