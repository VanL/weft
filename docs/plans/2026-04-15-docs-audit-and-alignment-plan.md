# Documentation Audit And Alignment

## 1. Goal

Bring the high-traffic docs back into clear alignment with the current Weft
surface. This slice is documentation-only: fix proven drift in the canonical
spec index, root README, tutorial flow, and agent entry-point quick reference
so a zero-context reader gets current commands, current outputs, and the right
mental model on the first pass.

## 2. Source Documents

Source specs:
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1], [CLI-1.4]
- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/12-Pipeline_Composition_and_UX.md`

Source-of-truth behavior:
- `README.md`
- `AGENTS.md`
- `docs/tutorials/first-task.md`
- `weft/cli.py`
- `weft/commands/specs.py`

Local guidance:
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`

## 3. Context and Key Files

Files to modify:
- `docs/specifications/README.md`
- `README.md`
- `docs/tutorials/first-task.md`
- `AGENTS.md`

Read first:
- `docs/specifications/README.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/12-Pipeline_Composition_and_UX.md`
- `weft/commands/specs.py`

Shared paths to reuse:
- `./.venv/bin/python -m weft.cli ...` help output as the CLI truth source
- `weft/commands/specs.py` for current `spec create`, `spec generate`, and
  spec-resolution behavior

Current drift already proven:
- `docs/specifications/README.md` omits
  `12-Pipeline_Composition_and_UX.md` from the explicit canonical-doc list even
  though the overview spec treats it as current contract.
- `docs/tutorials/first-task.md` still teaches an older `weft spec create`
  flow and older task-list output shape.
- `README.md` shows an outdated `weft result` example shape.
- `AGENTS.md` quick reference omits current shipped commands
  `weft spec generate` and `weft system builtins`.

## 4. Invariants and Constraints

- No code or CLI behavior changes. This slice is docs only.
- Specs remain the source of truth for behavior.
- Do not invent commands or outputs that are not backed by the live CLI.
- Keep examples stable where possible. Prefer examples that show supported
  flows without overfitting to fragile formatting.
- Preserve the canonical/planned split for numbered specs. The fix is to make
  that split clearer, not to promote planned docs casually.
- No drive-by rewrites outside the touched docs.

## 5. Tasks

1. Fix the canonical spec index.
   - Files to touch:
     - `docs/specifications/README.md`
   - Outcome:
     - explicitly list `12-Pipeline_Composition_and_UX.md` as canonical current
       contract
     - make the `12` split legible so readers do not infer that only
       `12-Future_Ideas.md` owns the `12` slot
     - add a backlink to this plan

2. Correct the root README examples and authoring guidance.
   - Files to touch:
     - `README.md`
   - Outcome:
     - replace the stale `weft result` example with the current output shape
     - add or tighten stored-spec authoring guidance so it matches the current
       `spec generate` plus `spec create --file` workflow
   - Verification:
     - back every command example with the live CLI help or a throwaway-context
       run

3. Rewrite the first-task tutorial around the current CLI.
   - Files to touch:
     - `docs/tutorials/first-task.md`
   - Outcome:
     - replace the obsolete `spec create --target` flow
     - show the current `spec generate` then `spec create --file` path
     - avoid brittle old table output for task listing and status
     - explain the difference between fixed stored args and parameterized specs

4. Refresh the agent entry-point quick reference.
   - Files to touch:
     - `AGENTS.md`
   - Outcome:
     - include `weft spec generate` and `weft system builtins` in the quick
       reference so zero-context agents are not pointed at an incomplete CLI
       summary

## 6. Verification

Run:
- `./.venv/bin/python -m weft.cli --help`
- `./.venv/bin/python -m weft.cli run --help`
- `./.venv/bin/python -m weft.cli spec create --help`
- `./.venv/bin/python -m weft.cli spec generate --type task`
- `./.venv/bin/python -m weft.cli system builtins --json`

Manual proof in a throwaway context:
- `./.venv/bin/python -m weft.cli init`
- `./.venv/bin/python -m weft.cli run echo "hello from weft"`
- `./.venv/bin/python -m weft.cli run --function processor:greet --arg world`
- `./.venv/bin/python -m weft.cli spec generate --type task > my-greeter.json`
- `./.venv/bin/python -m weft.cli spec create my-greeter --file my-greeter.json`
- `./.venv/bin/python -m weft.cli run --spec my-greeter`

## 7. Rollback

If any doc change turns out to describe unshipped behavior, drop that change
and keep the documentation narrower. Accuracy matters more than breadth in this
slice.
