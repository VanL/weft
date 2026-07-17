# Designing Agent-Facing Interfaces

Principles for designing any surface an agent consumes: REST and MCP
APIs, CLIs, and structured documentation. Adopted into weft via
`docs/plans/2026-07-17-agent-guidance-propagation-plan.md` from
agent-guidance @ `b248e1c`. In the hub these were distilled as the first
role-symmetric cross-repo fold-up from the mm repository's agent API
design (validated by its shipped `apps/external_api` and documented in
its `implementation/53-External-API-and-MCP.md`); two principles converge
independently with the shared guidance doctrine, the rest generalize mm's
design. The original fold-up lineage accounting lives in agent-guidance's
"agent-facing-interfaces-runbook" plan (foreign — not a weft path).

Owner: whoever designs or reviews an agent-facing surface. Boundary:
interface *design*; the probe floors for agent-built tools live in
`adversarial-acceptance-probes.md`, and repo-specific contracts live in
`docs/specifications/`. Verification: review agent-facing changes against
the principles below, plus the Related Gates section's owners for
enumerable elements. Required action: when a design departs from a
principle, state the departure and its reason in the governing spec —
silence is the only violation.

## The Principles

1. **Context is the scarcest resource.** Writes return confirmations,
   identifiers, and a freshness token (hash, ETag, version id) — never
   the full state. Orientation is an explicit, paginated read the
   agent requests when it needs it. The same economy governs CLI
   output and documentation kernels. *(Validated: mm's compact
   mutation responses.)*

2. **Progressive disclosure.** Teaching surfaces first (reference
   endpoints, `--help`, read-order indexes), orientation second
   (snapshot), detail last (per-item lookups). An agent should be able
   to become competent through the interface itself, without
   out-of-band training material. *(Validated: mm's reference →
   snapshot → node-detail ladder; the read-order model in
   `docs/agent-context/`.)*

3. **Self-explanatory names; no lookup tables.** Every endpoint,
   field, flag, and file name reads naturally at the point of use.
   Any name that requires a mapping document is an invisible wall —
   agents miss at exactly such boundaries. *(Validated: mm's design
   principle 1; the invisible-walls lesson in its lazy-import
   incidents.)*

4. **One identity per thing.** Never expose parallel naming systems
   for the same object; resolve internal identity indirection
   server-side. Where identifiers must be minted, let the client
   propose them when scope makes bad proposals harmless — it removes
   round-trips agents fumble. *(Validated: mm's single
   `instance_uuid`; mixing identity levels was its dominant class of
   agent mistakes.)*

5. **Derive what is derivable.** Never ask the agent to supply a value
   the interface can compute from what it already has (mm: edge types
   derived from endpoint kinds). Every derivable-but-requested field
   is an opportunity to be wrong.

6. **No hidden session setup.** The full address travels in every
   call — no session handles and no multi-step context-setup sequence
   the agent must remember. Ambient context that is legitimate for
   the surface (a CLI's working directory, an environment variable)
   is fine when it is inspectable and documented; the failure mode is
   state the agent must have *established earlier* and cannot see
   now. Self-describing calls are also self-debugging calls.
   *(Validated: mm's stateless URL routing.)*

7. **Teach, don't reject.** Canonicalize near-miss input at the
   boundary and report the normalization in-band ("normalized X to Y —
   use Y next time"); preserve unknown-but-safe values instead of
   422-ing them. Reserve rejection for true conflicts and unsafe
   writes. Rejection teaches nothing; a normalization note improves
   the agent's next call. *(Validated: mm's tag/PURL normalization
   guidance; the write-boundary application of the canonical-forms
   principle — engineering-principles §3, Canonicalize at Boundaries,
   in this repo. Distinct from a ban on read-time fallbacks:
   canonicalization here is explicit, reported, and at the write
   boundary.)*

8. **Every message carries its action.** Success and failure both
   return structured guidance; each entry has a mandatory, actionable
   next step — never a bare complaint, never a raw stack trace. This
   is the interface form of the guidance-doc rule that owner,
   boundary, verification, and required action be explicit.
   *(Validated: mm's `guidance` array with its always-present
   `action` field.)*

9. **Atomic writes with a recovery path on conflict.** The agent's
   payload commits all-or-nothing, and a rejected write returns the
   recovery sequence (re-fetch, rebase, retry), never a bare refusal.
   *Where the surface admits concurrent writers*, prefer merging
   non-overlapping changes over rejecting on any change, with an
   in-band report of what else merged — but do not force
   merge machinery onto single-writer surfaces (most CLIs).
   *(Validated: mm's atomic sync, merge-oriented concurrency, and
   `concurrent_merge` guidance.)*

10. **Draw the trust boundary in the interface.** Give agents the
    build/read surfaces; keep judgment surfaces (evaluation, approval,
    publication) where humans review — and state the split explicitly
    so a missing capability reads as a decision, not a gap.
    *(Validated: mm's deliberate absence of an analysis endpoint.)*

11. **Wire format matches the agent's mental model, not the storage
    model.** Accept objects shaped the way an agent naturally
    discovers them; decompose internally. Every internal structure the
    agent must pre-assemble is a place it can assemble wrongly.
    *(Validated: mm's fat nodes.)*

## Related Gates (owned elsewhere — cited, not restated)

An interface's error codes, guidance types, taxonomies, and flag sets
are enumerable contracts governed by engineering-principles §9
(Enumerable Contracts Get Executable Gates), and any agent-facing tool
must pass the black-box floors in `adversarial-acceptance-probes.md`
before it is called integration-ready. Those rules live there; this
runbook only reminds reviewers to apply them to interface surfaces.

## Review Use

When reviewing an agent-facing surface, walk the eleven principles as
a checklist and require a stated reason for each departure; apply the
Related Gates to every enumerable element. Not every principle
applies to every surface kind — the concurrent-merge clause of #9 is
for multi-writer surfaces, and #6's ambient-context allowance is for
CLIs — a stated "not applicable: single-writer CLI" is a valid
answer, silence is not. The
compound-engineering suite's `cli-agent-readiness-reviewer` persona is
a compatible external lens for CLIs (see `external-skill-suites.md`
for precedence). `skills/interface-review/SKILL.md` is the repeatable
procedure that operationalizes this walk — its checklist, evidence bar,
and findings-table output contract.

## Worked Example

mm's external API: principles in its
`implementation/53-External-API-and-MCP.md` "Why This Layer Looks This
Way"; live behavior in `apps/external_api` (guidance catalog,
reference endpoints, merge-oriented sync).
