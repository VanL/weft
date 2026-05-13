# Early Env File Bootstrap Plan

Status: completed
Source specs: docs/specifications/10-CLI_Interface.md [CLI-0.3], [CLI-1.1.1], [CLI-1.1.2], [CLI-5]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4], Current Context API; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/02-TaskSpec.md [TS-1.4]; docs/specifications/07-System_Invariants.md [IMMUT.1], [IMMUT.2], [STATE.1], [QUEUE.1], [OBS.1]
Superseded by: none

## 1. Goal

Add one early `WEFT_ENV_FILE` bootstrap path that loads a small dotenv-style
file into `os.environ` before the full Weft CLI imports. This lets cron,
systemd, and other sparse-environment launchers supply broker credentials,
`PATH`, and Weft defaults without creating manager-only special cases or
duplicating configuration resolution outside `load_config()` and
`build_context()`.

## 2. Source Documents

Primary specs:

- `docs/specifications/10-CLI_Interface.md` [CLI-0.3], [CLI-1.1.1],
  [CLI-1.1.2], [CLI-5]: root CLI options, `weft run`, `manager serve`, and
  configuration ownership.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] and Current
  Context API: `build_context()` owns broker target resolution and consumes
  `load_config()`.
- `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-3]: manager
  bootstrap, foreground serve, and detached manager startup.
- `docs/specifications/02-TaskSpec.md` [TS-1.4]: `spec.env` is task-process
  environment overlay, not Weft process bootstrap.
- `docs/specifications/07-System_Invariants.md` [IMMUT.1], [IMMUT.2],
  [STATE.1], [QUEUE.1], [OBS.1]: this slice must not move TaskSpec,
  lifecycle, queue, or observability contracts.

Local guidance:

- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/lessons.md`

Existing plans to align with:

- `docs/plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md`
- `docs/plans/2026-04-09-weft-serve-supervised-manager-plan.md`
- `docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`
- `docs/plans/2026-05-13-manager-replace-start-serve-plan.md`

## 3. Context And Key Files

Files to modify:

- `pyproject.toml`
- `weft/bootstrap.py` (new)
- `weft/cli/bootstrap.py` (compatibility re-export only if needed)
- `weft/__main__.py`
- `weft/cli/__main__.py`
- `weft/_constants.py` only if adding a public constant for `WEFT_ENV_FILE`
- `tests/cli/test_env_file_bootstrap.py` (new or equivalent)
- `tests/system/test_constants.py` if `WEFT_ENV_FILE` becomes a constant
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `README.md`
- `docs/plans/README.md`

Read first:

- `weft/cli/app.py`: importing this module builds the Typer app and imports
  command modules. Treat this as too late for env-file bootstrap.
- `weft/cli/__init__.py`: currently exposes `app`; do not use it as the
  console-script entry point for this feature because importing a package
  submodule executes this package first. Make this package export the app
  lazily so `python -m weft.cli` can reach the bootstrap before full CLI import.
- `weft/__main__.py` and `weft/cli/__main__.py`: direct `python -m` entry
  points must share the same bootstrap path.
- `weft/_constants.py`: `load_config()` and `get_weft_directory_name()` read
  process env and compile Weft plus SimpleBroker config.
- `weft/context.py`: `build_context()` is the canonical context and broker
  target resolution path.
- `weft/helpers/__init__.py`: caches `_config = load_config()` at import time,
  which is why bootstrap must happen before importing modules that may import
  helpers.
- `weft/core/manager_runtime.py`: detached manager startup serializes the
  resolved broker target and `context.config` into `manager_process` argv.
- `weft/manager_process.py` and `weft/manager_detached_launcher.py`: current
  manager child-process entry points for detached startup.

Current structure:

- Console entry in `pyproject.toml` was `weft = "weft.cli:app"`, which imports
  `weft/cli/__init__.py`, then `weft/cli/app.py`, before any command callback
  can run.
- `weft/cli/app.py` computes `default_export_path_help` from
  `get_weft_directory_name()` at import time.
- Some command modules import `weft.helpers`, which caches `load_config()` at
  import time.
- `build_context()` already accepts a preloaded config, but ordinary CLI
  commands call it after import through command handlers.
- `spec.env` and CLI `--env` affect task execution, not Weft's own process
  bootstrap or broker resolution.
- `weft manager serve` runs the manager in the current process, so a correctly
  loaded bootstrap env is available without a detached subprocess protocol.
- `weft manager start` and manager auto-start from `weft run` go through
  detached manager launch and currently pass config and broker target data in
  process arguments.

Comprehension questions before editing:

1. Which imports currently happen before a Typer command callback can run, and
   which of those may read configuration?
2. Why is `--env`, `spec.env`, or `weft manager serve --env-file` insufficient
   for broker target resolution in `weft run`, `result`, `status`, and queue
   commands?
3. Which detached manager startup arguments may contain secret-bearing broker
   config today?

## 4. Invariants And Constraints

Must preserve:

- `load_config()` remains the canonical typed config compiler.
- `build_context()` remains the canonical project and broker target resolver.
- No command-specific env-file path. `WEFT_ENV_FILE` is a process bootstrap
  input for all Weft CLI entry points.
- No new TaskSpec field and no change to `spec.env` semantics.
- No change to queue names, TID generation, lifecycle state transitions,
  reserved queue policy, or task-log contracts.
- No new dependency for dotenv parsing unless a separate plan approves it.
- Real process env values win over env-file values. The env file fills missing
  keys; it does not overwrite the supervisor's explicit environment.
- `WEFT_ENV_FILE` is read once from the original process environment. Values
  inside the env file must not trigger recursive env-file loading.
- Parse and read failures for an explicitly set `WEFT_ENV_FILE` are fatal and
  must happen before importing the full CLI.
- Error messages must name the env-file path and parse location, but must not
  echo secret values.
- The implementation must not add shell evaluation, command substitution,
  interpolation, includes, or variable expansion.

Security constraint:

- This bootstrap loads secrets into the process environment. It is not a
  secret manager.
- Detached manager startup currently serializes `context.config` and broker
  target data into child process arguments. If the env file contains
  `WEFT_BACKEND_PASSWORD`, `BROKER_BACKEND_PASSWORD`, or a password-bearing DSN,
  `weft manager start` and manager auto-start from `weft run` may expose that
  material through process argv, even if base64 encoded. The implementation may
  still land bootstrap support, but docs must not present it as secret-safe for
  detached manager startup until that separate manager-process protocol issue
  is fixed.
- `weft manager serve` is the recommended supervisor mode for secret-bearing
  env-file deployments in this slice because it avoids the detached manager
  argv path.

Stop and re-plan if:

- the implementation wants a root `--env-file` flag. A normal Typer option is
  too late for the import-time problem unless the CLI entry point grows a
  custom argv pre-parser, which is out of scope for this slice.
- the implementation starts moving broker resolution out of `build_context()`.
- the implementation wants to alter `manager_process` invocation to remove
  secret-bearing argv. That is a valid follow-up, but it crosses a larger
  manager bootstrap protocol boundary and needs its own plan or an explicit
  expansion of this one.
- tests mock away import order instead of proving subprocess entry behavior.

## 5. Tasks

1. Add the bootstrap module and narrow env-file parser.
   - Outcome: a small import-light function loads `WEFT_ENV_FILE` before the
     full CLI imports.
   - Files to touch: `weft/bootstrap.py`; optionally `weft/_constants.py`
     for a `WEFT_ENV_FILE` constant.
   - Read first: `weft/_constants.py`, `weft/cli/app.py`,
     `weft/helpers/__init__.py`.
   - Implementation shape:
     - `main()` reads `os.environ.get("WEFT_ENV_FILE")`.
     - If unset or blank, import `weft.cli.app.app` and invoke it normally.
     - If set, expand `~`, resolve relative paths against the current working
       directory, read UTF-8 text, parse it, apply missing keys to
       `os.environ`, then import and invoke `weft.cli.app.app`.
     - Parser accepts blank lines, full-line `#` comments, optional `export `,
       `KEY=VALUE`, unquoted values, single-quoted values, and double-quoted
       values.
     - Keys must match `[A-Za-z_][A-Za-z0-9_]*`.
     - Empty values are allowed.
     - No interpolation, command substitution, includes, multiline values, or
       inline shell evaluation.
     - Existing `os.environ` values are left unchanged.
   - Do not import `weft.cli.app`, `weft.context`, `weft.helpers`, or
     `weft._constants.load_config()` before env-file application.
   - Tests:
     - parser unit tests for comments, quotes, `export`, empty values, invalid
       keys, missing `=`, and process-env-wins behavior.
   - Done when: the parser can be tested without importing `weft.cli.app`, and
     invalid files fail without printing values.

2. Route every CLI entry point through the bootstrap.
   - Outcome: installed `weft`, `python -m weft`, and `python -m weft.cli` all
     load `WEFT_ENV_FILE` before the full CLI app imports.
   - Files to touch: `pyproject.toml`, `weft/__main__.py`,
     `weft/cli/__main__.py`.
   - Read first: `weft/cli/__init__.py`, `weft/cli/app.py`.
   - Implementation shape:
     - Change the console script to `weft = "weft.bootstrap:main"`.
     - `weft/__main__.py` imports and calls `weft.bootstrap.main`.
     - `weft/cli/__main__.py` imports and calls `weft.bootstrap.main`.
     - Preserve `from weft.cli import app` for existing import users through a
       lazy package export instead of eager app import.
   - Tests:
     - subprocess test for `python -m weft --version` with `WEFT_ENV_FILE`.
     - subprocess test for `python -m weft.cli --version` with `WEFT_ENV_FILE`.
     - console-script behavior can be covered by invoking the installed script
       through the repo-managed environment if the existing CLI harness has a
       stable helper; otherwise use module entry points as the import-order
       proof and add a packaging smoke check.
   - Stop if: `weft/cli/__init__.py` needs to import `weft.cli.app` eagerly
     again. That would make `python -m weft.cli` too late for bootstrap.
   - Done when: the env-file value affects import-time help/default behavior
     or an import-time sentinel without manual reloads.

3. Prove config resolution observes env-file values before context build.
   - Outcome: broker and Weft config loaded from the env file participates in
     normal `load_config()` and `build_context()` paths.
   - Files to touch: `tests/cli/test_env_file_bootstrap.py` and, if needed,
     existing CLI/context test helpers.
   - Read first: `tests/helpers/weft_harness.py`,
     `tests/context/test_context.py`, existing CLI subprocess helpers.
   - Preferred proof:
     - Run a subprocess with a temp env file containing
       `WEFT_DIRECTORY_NAME=.weft-envfile-test` and a minimal command whose
       output or side effect proves the configured directory name was observed
       before import-time defaults.
     - Add a second subprocess proof with env-file broker aliases such as
       `WEFT_DEFAULT_DB_LOCATION=<tmp>` and `WEFT_DEFAULT_DB_NAME=envfile.db`,
       then run a stateful command that builds context and verify the broker
       file appears in the env-file-selected location.
   - Keep real: subprocess import boundary and filesystem side effects. Do not
     replace this with only direct parser unit tests.
   - Done when: a sparse process env plus `WEFT_ENV_FILE` can build the
     expected Weft context without any command-specific env-file flag.

4. Cover failure and precedence semantics.
   - Outcome: operators get predictable failure behavior and supervisor-set
     env is not silently overwritten.
   - Files to touch: `tests/cli/test_env_file_bootstrap.py`.
   - Cases:
     - missing env file exits non-zero before CLI import and names the missing
       path.
     - unreadable or malformed env file exits non-zero and reports the line
       number without printing the value.
     - process env wins over file env for the same key.
     - `WEFT_ENV_FILE` inside the file does not trigger recursive loading.
   - Suggested exit code: `2`, matching command-line usage/config errors,
     unless existing CLI error conventions point to a better code.
   - Done when: failures are deterministic and safe to show in supervisor logs.

5. Update docs and warnings.
   - Outcome: specs and README describe the new bootstrap contract without
     overstating secret safety.
   - Files to touch: `docs/specifications/10-CLI_Interface.md`,
     `docs/specifications/04-SimpleBroker_Integration.md`, `README.md`.
   - Required doc points:
     - `WEFT_ENV_FILE` is bootstrap-only and is loaded before the full CLI
       imports.
     - Process env wins over env-file values.
     - The file format is intentionally narrow and not shell-evaluated.
     - `--env` and `spec.env` are task execution overlays, not Weft process
       bootstrap.
     - For systemd with secrets, prefer `weft manager serve` over detached
       `manager start` until the manager detached-start argv limitation is
       addressed.
     - File permissions are the operator's responsibility; Weft should not try
       to chmod or own arbitrary env files in this slice.
   - Done when: `docs/specifications/10-CLI_Interface.md` [CLI-5] and
     `docs/specifications/04-SimpleBroker_Integration.md` Current Context API
     explain the steady-state contract.

6. Run independent review before implementation is called ready.
   - Outcome: a reviewer checks the plan and then the completed slice for
     import-order mistakes, config precedence drift, and security overclaims.
   - Reviewer prompt:
     "Read `docs/plans/2026-05-13-early-env-file-bootstrap-plan.md`, the
     touched specs, and the intended files. Look for errors, bad ideas, and
     latent ambiguities. Do not implement anything. Could you implement this
     confidently and correctly if asked?"
   - Prefer a non-author agent family if available. If only Codex review is
     available, record that limitation in review notes.

## 6. Testing Plan

Use both direct parser tests and real subprocess tests.

Parser tests may call the parser directly because the parser's grammar is a
local contract. They should not import the full CLI.

Subprocess tests are required for the load-bearing behavior:

- `python -m weft --version` with `WEFT_ENV_FILE` set
- `python -m weft.cli --version` with `WEFT_ENV_FILE` set
- one stateful command that proves `build_context()` sees env-file broker or
  metadata-directory settings
- one malformed env-file case that proves failure happens before the full CLI
  imports and does not echo values

Do not mock:

- process startup/import ordering
- filesystem env-file read behavior
- `load_config()` or `build_context()` for the integration proof

Mocking is acceptable only for a narrow unit test around parser error
formatting if the real filesystem case is also covered.

## 7. Verification And Gates

Per-task checks:

```bash
uv run pytest tests/cli/test_env_file_bootstrap.py -q
uv run pytest tests/system/test_constants.py -q
```

Final gates:

```bash
uv run pytest tests/cli/test_env_file_bootstrap.py tests/context/test_context.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft
```

Runtime observation after implementation:

- A sparse shell can run `WEFT_ENV_FILE=/path/to/file python -m weft status`
  and resolve the env-file-selected context.
- A systemd-style foreground command can run
  `WEFT_ENV_FILE=/path/to/file python -m weft manager serve --context /project`
  without additional exported broker password variables.

## 8. Rollout And Rollback

Rollout:

- Ship parser and bootstrap first with docs that describe the exact format and
  precedence.
- Keep old behavior unchanged when `WEFT_ENV_FILE` is unset.
- Do not require broker migration, queue migration, TaskSpec migration, or
  project metadata migration.

Rollback:

- Revert `pyproject.toml`, `weft/__main__.py`, `weft/cli/__main__.py`, and the
  new bootstrap module plus any compatibility re-export.
- Remove the docs entries.
- Existing environments that do not set `WEFT_ENV_FILE` must behave exactly as
  before throughout rollout and rollback.

One-way doors:

- None intended. If the implementation starts changing detached manager
  invocation payload format, that is a separate protocol change and this plan
  must be expanded before continuing.

## 9. Out Of Scope

- A root `--env-file` CLI flag.
- Manager-only `--env-file` behavior.
- Shell-compatible dotenv parsing.
- Recursive env-file loading.
- Secret-manager integrations.
- Automatic file permission changes.
- Changing `spec.env` or CLI `--env`.
- Fixing detached manager secret-bearing argv. This is important but separate;
  do not silently fold it into this slice.
- Changing SimpleBroker config precedence.
- Changing provider CLI login or token behavior.

## 10. Fresh-Eyes Checklist

Before implementation starts, confirm:

- the implementer can explain why bootstrap must happen before importing
  `weft.cli.app`;
- all entry points route through one helper;
- `load_config()` and `build_context()` remain the config/context owners;
- parser semantics are narrow enough to implement without a shell;
- tests include real subprocess entry behavior;
- docs warn about the detached-manager argv limitation for secrets;
- no manager-specific or command-specific env-file path has appeared.
