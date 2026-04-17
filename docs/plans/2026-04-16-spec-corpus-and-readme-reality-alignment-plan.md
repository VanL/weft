# Spec Corpus And README Reality Alignment

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Refresh every document under `docs/specifications/` plus `README.md` so the
doc corpus matches current code reality, current CLI behavior, and current
spec-to-code ownership. This slice is documentation-only. The work should
improve factual accuracy, fix stale implementation mappings, tighten
traceability, and remove drift between canonical, planned, and exploratory
documents.

## 2. Source Documents

Source specs:
- `docs/specifications/README.md`
- every spec file under `docs/specifications/`

Source-of-truth code and behavior:
- `README.md`
- `weft/cli.py`
- `weft/commands/*.py`
- `weft/core/**/*.py`
- `weft/builtins/**/*.py`
- closest tests in `tests/`

Local guidance:
- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`

Related plans:
- `docs/plans/2026-04-15-docs-audit-and-alignment-plan.md`

## 3. Context And Key Files

Files to modify:
- `README.md`
- every `.md` file directly under `docs/specifications/`

Read first:
- `docs/specifications/README.md`
- `README.md`
- `weft/cli.py`
- `weft/commands/run.py`
- `weft/commands/specs.py`
- `weft/core/taskspec.py`

Current state:
- The repo already has doc-only edits in:
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/04-SimpleBroker_Integration.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/13-Agent_Runtime.md`
- There are also unrelated untracked JS assets in the worktree. Do not touch
  or revert them.

Parallel ownership model:
- one worker owns exactly one document
- workers may read any code or tests they need
- workers must only edit their assigned document
- cross-doc consistency, wording normalization, and any multi-file follow-up
  stay with the main rollout after worker results land

## 4. Invariants And Constraints

- No code changes. This slice is docs only.
- Specs remain the source of truth for behavior.
- Do not invent behavior that is not present in code or current CLI help.
- Keep canonical vs planned vs exploratory boundaries explicit.
- Preserve the current durable spine and task model in the docs:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- Fix implementation mappings when wrong, but do not add vague mappings that
  are less accurate than the current text.
- Prefer concrete file and function references over broad module claims.
- Do not revert unrelated user changes already present in the worktree.

## 5. Tasks

1. Review each document against current code reality.
   - Outcome: every worker compares one owned doc against the relevant code,
     CLI help, and tests, then patches factual drift in that file only.
   - Scope per worker:
     - current behavior statements
     - implementation mappings
     - command names and flags
     - queue names and state terminology
     - current-vs-planned framing
     - stale examples or removed surfaces

2. Reconcile cross-document consistency locally.
   - Outcome: after workers finish, the main rollout aligns terminology and
     shared claims across the corpus.
   - Focus:
     - `README.md` vs canonical specs
     - canonical specs vs companion `A` docs
     - `10B` and `13B` boundary wording
     - spec index and reading order
     - repeated terms such as agent settings, delegated runtimes, pipelines,
       autostart, and manager lifecycle

3. Verify references and examples.
   - Outcome: the final corpus uses current file/module names and current CLI
     surfaces.
   - Minimum proof:
     - `python -m weft.cli --help`
     - `python -m weft.cli run --help`
     - `python -m weft.cli spec --help`
     - targeted `rg` checks for removed or renamed surfaces

## 6. Verification

Run:
- `./.venv/bin/python -m weft.cli --help`
- `./.venv/bin/python -m weft.cli run --help`
- `./.venv/bin/python -m weft.cli spec --help`
- `./.venv/bin/python -m weft.cli manager --help`
- `rg -n "weft serve|delegated-agent launch config|not yet implemented" README.md docs/specifications`

Manual proof:
- spot-check the most load-bearing docs against the code:
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/03-Manager_Architecture.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/12-Pipeline_Composition_and_UX.md`
  - `docs/specifications/13-Agent_Runtime.md`

## 7. Rollback

If a worker update introduces uncertainty on a behavior claim, keep the
document narrower and defer the claim rather than speculating. Accuracy matters
more than breadth in this slice.
