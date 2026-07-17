# Interface Review

Status: Active — adopted into weft via
`docs/plans/2026-07-17-agent-guidance-propagation-plan.md` from
agent-guidance @ `b248e1c`. In the hub this skill was promoted at three
distinct citations of the workflow theme (taut MCP plan §16.3–§16.4; mm
`implementation/41` RiskEvaluationApi contract; mm external-API/MCP
contract plan §4/§5/§9); that promotion provenance lives in
agent-guidance's "interface-review-skill-promotion" plan (foreign — not a
weft path).

**This skill is the repeatable procedure for reviewing an agent-facing
surface against `runbooks/designing-agent-facing-interfaces.md`. The
runbook owns the eleven principles and their rationale; this skill owns
the walk, the evidence bar, and the output contract. Never restate a
principle's content here — cite it by name and number and let the
runbook be the source.**

## Purpose

Produce a disciplined, evidence-backed review of any agent-facing
surface — REST, MCP, CLI, or structured documentation — that walks the
eleven interface principles as a checklist, verifies enumerable
contracts against code, and emits a findings table a downstream author
can disposition without re-deriving the surface.

## When To Use

- **A new or changed agent-facing surface reaches design review** — an
  MCP tool set, a REST endpoint contract, a CLI, a structured doc
  kernel — before it is called integration-ready.
- **A surface is up for pre-promotion review** — its schemas/descriptions
  are about to be frozen (MCP `tools/list` snapshot tests, an OpenAPI
  freeze, a doc that becomes normative).
- **A shipped surface needs contract recovery** — the wire contract has
  drifted from the docs and you are re-pinning it from the code
  (the mm RiskEvaluationApi walk is the exemplar: field tables with
  file:line, enums enumerated, honest untested-field list).
- Do NOT use this to review internal (non-agent-facing) APIs, or to
  re-review source content already reviewed in a propagation — that is
  `propagate-guidance`'s scoped-review step, not this.

## Governing Spec References

- `docs/agent-context/runbooks/designing-agent-facing-interfaces.md` —
  the eleven principles and the Related Gates (this skill's spine)
- `docs/agent-context/engineering-principles.md` §9 (Enumerable
  Contracts Get Executable Gates) — the gate every enumerable element owes
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md` — the
  black-box floors an agent-facing tool passes before "integration-ready"
- `AGENTS.md` Definition of Done (verification-evidence bar) and
  `docs/coalescing.md` (promotion provenance)

## Read First

- The runbook above — walk it open beside the surface; do not review
  from memory of it.
- The surface's **contract artifacts**: its spec/section, schema
  (`tools/list` output, OpenAPI, `--help`), and its published
  descriptions.
- The surface's **code**: the handlers, serializers, validators, and
  enum definitions — the code is the source of truth, the docs are the
  claim under test.
- For MCP surfaces: the versioned official MCP `ToolAnnotations`
  semantics (the runbook is the review lens, not the annotation-schema
  authority) and `docs/agent-context/runbooks/external-skill-suites.md`
  for the `cli-agent-readiness-reviewer` precedence on CLIs.

## Blast Radius

- The reviewed surface's governing spec/plan section (where findings and
  the ratified-judgments row land).
- The owning repo's enumerable-contract gates (error codes, guidance
  types, taxonomies, flag sets) — every enumerable element the review
  touches owes a firing test.
- `runbooks/designing-agent-facing-interfaces.md` — every review feeds
  it: extension candidates surfaced go on the runbook-feedback line.

## Workflow

### 1. Frame the surface and gather artifacts

- **Name the surface kind** (REST / MCP / CLI / structured doc) — it
  decides which principle clauses are in scope, per the runbook's
  Review Use (e.g. #9's merge clause is multi-writer only).
- **Collect the contract artifacts and the code** before writing a word.
  A review without the handler open produces claims you cannot
  substantiate at file:line.
- **State the review scope and baseline** — which delta, at which
  commit — so findings read as claims against a fixed target, not a
  moving one.

### 2. Walk the eleven principles as a checklist

Walk `designing-agent-facing-interfaces.md` §"The Principles" in order.
For **every** principle, record one of: *met* (with the file:line that
proves it), *departs — <reason>*, or *not applicable: <surface kind>*.
**Silence is the only violation.** The checklist skeleton (names only —
content lives in the runbook):

1. Context is the scarcest resource
2. Progressive disclosure
3. Self-explanatory names; no lookup tables
4. One identity per thing
5. Derive what is derivable
6. No hidden session setup
7. Teach, don't reject
8. Every message carries its action
9. Atomic writes with a recovery path on conflict
10. Draw the trust boundary in the interface
11. Wire format matches the agent's mental model, not the storage model

A stated "not applicable: single-writer CLI" against #9's merge clause
is a valid answer; leaving it blank is not.

### 3. Apply the Related Gates to every enumerable element

- Enumerate each error-code set, guidance-type set, status enum,
  taxonomy, and flag set the surface exposes, **from the code** — then
  confirm each has (or admits) an executable list per
  engineering-principles §9.
- **The one enumerable-looking field with no enum and no test is the
  finding** — mm's `action_priority` is the exemplar (free-form
  `CharField` on the model; the view writes any string with no enum or
  gate): the walk's job is to surface it, not paper over it.
- Confirm the surface passes the `adversarial-acceptance-probes.md`
  floors before any "integration-ready" claim.

### 4. MCP surfaces: annotations and descriptions are normative contract

- **Verify all four annotation hints are explicit on every tool**
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
  **Run the hostile-semantic-defaults check**: omitted hints default to
  the dangerous reading (destructive, open-world) — an omitted hint is a
  finding, not a style nit. Verify each declared value against the
  handler's real write seams (a "read" that advances a cursor or
  refreshes persisted derived state is not `readOnlyHint: true`).
- **Treat schema property `description` fields as the teaching surface** —
  addressing grammar, id/selector forms, bounds, and state effects live
  there or the zero-context agent never sees them. If descriptions are
  about to be snapshot-frozen, the review is the last cheap moment to fix
  them.

### 5. Hold the evidence bar

- **Every present-tense claim about the surface carries a file:line.**
  "`rpn` is derived server-side" is not a finding until it reads "`rpn`
  is recomputed server-side (`risk_api.py:274`)".
- **Verify against code, not docs.** A doc that says X is the claim; the
  handler that does Y is the truth. Contract-recovery reviews exist
  precisely because the two diverge.
- **List the untested fields honestly.** A field with no firing assertion
  is documented as such, never presented as verified.

## Output Standard

The review produces, in the surface's governing spec/plan section:

- **A findings table** with one row per finding:
  `ID | Severity | Location (file:line) | Finding | Suggested disposition`.
  Severity is the repo's P-scale (P1 blocker … P3/nit).
- **A ratified-judgments row (or rows)** — design calls that were
  challenged and upheld, recorded so they read as decisions, not gaps,
  and are not re-litigated next round (taut's "Ratified judgment calls
  (challenged, upheld)"; mm's owner-discussion fold-in).
- **A verdict line** — the exemplar form: `no blocker` or
  `blocker: F<ids>`, naming the finding(s) that gate promotion (and,
  where useful, which findings must resolve before which milestone).
- **A runbook-feedback line** — the extension candidates this review
  surfaced for `designing-agent-facing-interfaces.md` (taut's r2 named
  the presence-side-channel class and the reassure-what-it-does-NOT-do
  description pattern). Every review feeds the runbook; an empty line is
  a stated "no new runbook candidates", not silence.

All four survive a spot-check: every file:line resolves, every
enumerable claim points at a list, the verdict matches the findings.

## Maintenance Notes

- When a review surfaces a genuinely reusable interface pattern (not a
  local contract quirk), promote it to the runbook via the
  runbook-feedback line — do not let it accrete only in plans. Verify
  reusability against a second surface before landing it as runbook text
  (taut r2 holds its response-guidance pattern local until
  implementation evidence shows it reusable — pending a second surface).
- If two consecutive reviews of the same surface kind produce no
  departures and no new gates, the checklist has converged for that kind —
  consider a kind-specific quick-reference rather than growing this skill.
- If the eleven-principle walk keeps missing a class of defect, the gap
  is upstream in the runbook, not here — fix the runbook.
