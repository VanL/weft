# Implementation Roadmap Planned

This companion document tracks residual roadmap work only. The current
implementation boundary lives in
[09-Implementation_Plan.md](09-Implementation_Plan.md). Anything already
shipped, or already covered by the canonical specs, is intentionally omitted
here.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as the implementation
boundary.

Current surfaces that are out of scope for this planned ledger include:

- manager bootstrap, lifecycle, autostart, and pipeline autostart behavior
- explicit spec resolution, builtin inventory, and submission-time spec
  materialization
- the current `status`, `task`, `queue`, and manager discovery surfaces
- the current testing split and dev-only benchmark scripts

## 0. Manager And Runtime Follow-Ups [IP-A0]

Future-only work in this area would add or refine:

- manager specialization and capability-aware routing
- task dispatch across multiple managers or pools
- cross-manager load balancing and recovery coordination

Boundary:

- keep the canonical manager path as the default
- do not introduce a second control plane or hidden routing layer
- preserve TID, control, and queue ownership invariants

## 1. Operator Observability Follow-Ups [IP-A1]

Future-only work in this area would add or refine:

- extra operator-facing discovery helpers beyond the current command surface
- an explicit public TID lookup or resolution surface if operators need one
- deeper OS-level process integration when the current helper layer is not
  enough

Boundary:

- keep these as thin wrappers over the existing runtime truth
- do not turn them into a new monitoring backend or service registry

## 2. Spec Authoring Follow-Ups [IP-A2]

Future-only work in this area would add or refine:

- higher-level authoring scaffolds for bulk or templated spec creation
- reusable authoring catalogs if the explicit file-based model stops being
  enough
- other ergonomics that sit on top of the current `weft spec` and
  `weft run --spec` flow rather than replacing it

Boundary:

- keep explicit file-backed specs as the base model
- do not add a second hidden spec system
- do not duplicate behavior already covered by the current builtin, pipeline,
  or submission-time spec surfaces

## 3. Additional Quality Gates [IP-A3]

Future-only work in this area would add or refine:

- explicit benchmark suites or gated regressions if performance needs to
  become part of the contract
- a dedicated integration or performance split only if the current canonical
  suites stop being enough
- broader platform-specific validation when a supported platform or runtime
  actually needs it

Boundary:

- keep benchmarks opt-in until the testing spec says otherwise
- do not convert dev-only measurement scripts into default contract by
  accident
- keep canonical test coverage in [08-Testing_Strategy.md](08-Testing_Strategy.md)

## Backlink

Canonical implementation boundaries live in
[09-Implementation_Plan.md](09-Implementation_Plan.md).
