# Plan Corpus

This directory holds non-normative implementation plans, roadmaps, and audit artifacts.
Specs in `docs/specifications/` remain the source of truth for behavior.

There are currently 60 tracked plan files in this directory.

## Status Taxonomy

- `active`: current implementation slice or umbrella plan that is still driving work.
- `proposed`: a plan that is still useful guidance but is not explicitly closed yet.
- `completed`: the planned slice landed, even if later plans extended or corrected it.
- `roadmap`: broad direction-setting document rather than a narrow execution slice.
- `audit-log`: raw investigation log captured during an audit pass.
- `findings`: interpreted audit output or conclusions captured from an audit pass.

## Usage Rules

- Keep plan filenames date-prefixed: `YYYY-MM-DD-description.md`.
- Put a metadata block directly under the title with `Status`, `Source specs`, and `Superseded by`.
- Keep exact spec citations in the plan body. The top metadata block is a navigation aid, not a replacement for the detailed source section.
- When a plan is replaced, update its `Superseded by` field instead of leaving the relationship implicit.
- If a plan is only partially landed, keep it `proposed` until a follow-up plan or notes make closure explicit.

## Current Focus

- [`2026-04-16-manager-and-basetask-exception-hardening-plan.md`](./2026-04-16-manager-and-basetask-exception-hardening-plan.md): completed slice for narrowing broad `except Exception` catches in `Manager` and `BaseTask`.
- [`2026-04-15-maintainability-and-boundary-remediation-plan.md`](./2026-04-15-maintainability-and-boundary-remediation-plan.md): umbrella remediation plan for maintainability, seams, testing, and lifecycle policy.
- [`2026-04-15-docs-audit-and-alignment-plan.md`](./2026-04-15-docs-audit-and-alignment-plan.md): current docs-alignment slice for high-traffic entry points.
- [`2026-04-14-weft-road-to-excellent-plan.md`](./2026-04-14-weft-road-to-excellent-plan.md): broader roadmap for making the shipped surface boring, legible, and hard to drift.

## Index

| File | Title | Status | Superseded by |
| --- | --- | --- | --- |
| [`2026-04-16-manager-and-basetask-exception-hardening-plan.md`](./2026-04-16-manager-and-basetask-exception-hardening-plan.md) | Manager And BaseTask Exception Hardening | `completed` | none |
| [`2026-04-15-docs-audit-and-alignment-plan.md`](./2026-04-15-docs-audit-and-alignment-plan.md) | Documentation Audit And Alignment | `active` | none |
| [`2026-04-15-maintainability-and-boundary-remediation-plan.md`](./2026-04-15-maintainability-and-boundary-remediation-plan.md) | Maintainability And Boundary Remediation | `active` | none |
| [`2026-04-13-good-to-excellent.md`](./2026-04-13-good-to-excellent.md) | Good-to-Excellent: Specification, UX, and Engineering Polish | `roadmap` | none |
| [`2026-04-14-weft-road-to-excellent-plan.md`](./2026-04-14-weft-road-to-excellent-plan.md) | Weft Road To Excellent Plan | `roadmap` | none |
| [`2026-04-06-persistent-agent-runtime-implementation-plan.md`](./2026-04-06-persistent-agent-runtime-implementation-plan.md) | Persistent Agent Runtime Implementation Plan | `proposed` | none |
| [`2026-04-06-piped-input-support-plan.md`](./2026-04-06-piped-input-support-plan.md) | Piped Input Support Plan | `proposed` | none |
| [`2026-04-06-postgres-backend-audit-and-shared-test-surface-plan.md`](./2026-04-06-postgres-backend-audit-and-shared-test-surface-plan.md) | Postgres Backend Audit and Shared Test Surface Plan | `proposed` | none |
| [`2026-04-06-postgres-extra-and-runtime-hint-plan.md`](./2026-04-06-postgres-extra-and-runtime-hint-plan.md) | Postgres Extra And Runtime Hint Plan | `proposed` | none |
| [`2026-04-06-release-gated-tag-workflow-plan.md`](./2026-04-06-release-gated-tag-workflow-plan.md) | Release-Gated Tag Workflow Plan | `proposed` | none |
| [`2026-04-06-release-helper-plan.md`](./2026-04-06-release-helper-plan.md) | Release Helper Plan | `proposed` | none |
| [`2026-04-06-release-helper-retag-plan.md`](./2026-04-06-release-helper-retag-plan.md) | Release Helper Retag Plan | `proposed` | none |
| [`2026-04-06-release-helper-unpublished-version-reuse-plan.md`](./2026-04-06-release-helper-unpublished-version-reuse-plan.md) | Release Helper Unpublished Version Reuse Plan | `proposed` | none |
| [`2026-04-06-runner-extension-point-plan.md`](./2026-04-06-runner-extension-point-plan.md) | Runner Extension Point Plan | `proposed` | none |
| [`2026-04-06-simplebroker-backend-generalization-plan.md`](./2026-04-06-simplebroker-backend-generalization-plan.md) | SimpleBroker Backend Generalization Plan | `proposed` | none |
| [`2026-04-06-taskspec-clean-design-plan.md`](./2026-04-06-taskspec-clean-design-plan.md) | TaskSpec Clean Design Plan | `proposed` | none |
| [`2026-04-06-weft-backend-neutrality-plan.md`](./2026-04-06-weft-backend-neutrality-plan.md) | Weft Backend Neutrality Plan | `proposed` | none |
| [`2026-04-07-active-control-main-thread-plan.md`](./2026-04-07-active-control-main-thread-plan.md) | Active Control Main-Thread Ownership Plan | `proposed` | none |
| [`2026-04-07-agent-context-hardening-uplift.md`](./2026-04-07-agent-context-hardening-uplift.md) | Agent Context Hardening Uplift | `proposed` | none |
| [`2026-04-07-spec-plan-code-traceability-plan.md`](./2026-04-07-spec-plan-code-traceability-plan.md) | Plan-Writing Guidance Hardening Plan | `proposed` | none |
| [`2026-04-08-release-gate-failure-audit-plan.md`](./2026-04-08-release-gate-failure-audit-plan.md) | Release-Gate Failure Audit Plan | `proposed` | none |
| [`2026-04-08-task-lifecycle-stop-drain-audit-plan.md`](./2026-04-08-task-lifecycle-stop-drain-audit-plan.md) | Task Lifecycle Stop/Drain Audit Plan | `proposed` | none |
| [`2026-04-09-manager-bootstrap-unification-plan.md`](./2026-04-09-manager-bootstrap-unification-plan.md) | Manager Bootstrap Unification Plan | `proposed` | none |
| [`2026-04-09-manager-lifecycle-command-consolidation-plan.md`](./2026-04-09-manager-lifecycle-command-consolidation-plan.md) | Manager Lifecycle Command Consolidation Plan | `proposed` | none |
| [`2026-04-09-weft-serve-supervised-manager-plan.md`](./2026-04-09-weft-serve-supervised-manager-plan.md) | `weft manager serve` Supervised Manager Plan | `proposed` | none |
| [`2026-04-13-cli-surface-doc-and-help-alignment-plan.md`](./2026-04-13-cli-surface-doc-and-help-alignment-plan.md) | CLI Surface Doc And Help Alignment Plan | `proposed` | none |
| [`2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](./2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md) | Delegated Agent Authority Boundary Cleanup Plan | `proposed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md) | Delegated Agent Runtime Phase 2 Implementation Plan | `proposed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md) | Delegated Agent Runtime Phase 3 Implementation Plan | `proposed` | none |
| [`2026-04-13-detached-manager-bootstrap-hardening-plan.md`](./2026-04-13-detached-manager-bootstrap-hardening-plan.md) | Detached Manager Bootstrap Hardening Plan | `proposed` | none |
| [`2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md`](./2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md) | Manager Bootstrap Readiness And Cleanup Test Plan | `proposed` | none |
| [`2026-04-13-pipeline-first-class-runtime-implementation-plan.md`](./2026-04-13-pipeline-first-class-runtime-implementation-plan.md) | First-Class Linear Pipeline Runtime Implementation Plan | `proposed` | none |
| [`2026-04-13-pipeline-spec-expansion-plan.md`](./2026-04-13-pipeline-spec-expansion-plan.md) | Pipeline Spec Expansion Plan | `proposed` | none |
| [`2026-04-13-result-stream-implementation-plan.md`](./2026-04-13-result-stream-implementation-plan.md) | Result Stream Implementation Plan | `proposed` | none |
| [`2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md`](./2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md) | Runner Monitor, Result Waiter, and Liveness Fixes Plan | `proposed` | none |
| [`2026-04-14-agent-runtime-package-refactor-plan.md`](./2026-04-14-agent-runtime-package-refactor-plan.md) | Agent Runtime Package Refactor Plan | `proposed` | none |
| [`2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](./2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md) | Builtin Contract And Doc Drift Reduction Plan | `proposed` | none |
| [`2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md) | Builtin TaskSpecs and Explicit Spec Resolution Plan | `proposed` | none |
| [`2026-04-14-claude-host-credentials-and-descriptor-hardening-plan.md`](./2026-04-14-claude-host-credentials-and-descriptor-hardening-plan.md) | Claude Host Credentials Helper and Claude Descriptor Hardening Plan | `proposed` | none |
| [`2026-04-14-config-precedence-and-parsing-alignment-plan.md`](./2026-04-14-config-precedence-and-parsing-alignment-plan.md) | Config Precedence and Parsing Alignment Plan | `proposed` | none |
| [`2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](./2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md) | Docker Agent Images and One-Shot Provider CLI Plan | `proposed` | none |
| [`2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](./2026-04-14-provider-cli-container-runtime-descriptor-plan.md) | Provider CLI Container Runtime Descriptor Plan | `proposed` | none |
| [`2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md) | Provider CLI Validation Boundary and Agent Settings Alignment Plan | `proposed` | none |
| [`2026-04-14-spawn-request-reconciliation-plan.md`](./2026-04-14-spawn-request-reconciliation-plan.md) | Spawn Request Reconciliation Plan | `proposed` | none |
| [`2026-04-14-system-builtins-command-plan.md`](./2026-04-14-system-builtins-command-plan.md) | System Builtins Command Plan | `proposed` | none |
| [`2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](./2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md) | Multi-Provider Docker `provider_cli` Expansion Plan | `proposed` | none |
| [`2026-04-15-parameterized-taskspec-materialization-plan.md`](./2026-04-15-parameterized-taskspec-materialization-plan.md) | Parameterized TaskSpec Materialization Plan | `proposed` | none |
| [`2026-04-15-spec-aware-run-help-plan.md`](./2026-04-15-spec-aware-run-help-plan.md) | Spec-Aware `weft run --spec ... --help` | `proposed` | none |
| [`2026-04-16-pipeline-autostart-extension-plan.md`](./2026-04-16-pipeline-autostart-extension-plan.md) | Pipeline Autostart Extension Plan | `completed` | none |
| [`README.md`](./README.md) | Plan Corpus | `proposed` | none |
| [`2026-04-06-agent-runtime-boundary-cleanup-plan.md`](./2026-04-06-agent-runtime-boundary-cleanup-plan.md) | Agent Runtime Boundary Cleanup Plan | `completed` | none |
| [`2026-04-06-agent-runtime-implementation-plan.md`](./2026-04-06-agent-runtime-implementation-plan.md) | Agent Runtime Implementation Plan | `completed` | ./2026-04-06-persistent-agent-runtime-implementation-plan.md (persistent agent work) |
| [`2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](./2026-04-16-autostart-hardening-and-contract-alignment-plan.md) | Autostart Hardening And Contract Alignment Plan | `completed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md) | Delegated Agent Runtime Phase 1 Implementation Plan | `completed` | none |
| [`2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](./2026-04-13-spec-corpus-current-vs-planned-split-plan.md) | Spec Corpus Current-vs-Planned Split Plan | `completed` | none |
| [`2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](./2026-04-15-spec-run-input-adapter-and-declared-args-plan.md) | Spec Run-Input Adapter And Declared Args Plan | `completed` | none |
| [`2026-04-08-release-gate-failure-audit-log.md`](./2026-04-08-release-gate-failure-audit-log.md) | Release-Gate Failure Audit Log | `audit-log` | none |
| [`2026-04-08-task-lifecycle-stop-drain-audit-log.md`](./2026-04-08-task-lifecycle-stop-drain-audit-log.md) | Task Lifecycle Stop/Drain Audit Log | `audit-log` | none |
| [`2026-04-08-release-gate-failure-findings.md`](./2026-04-08-release-gate-failure-findings.md) | Release-Gate Failure Audit Findings | `findings` | none |
| [`2026-04-08-task-lifecycle-stop-drain-findings.md`](./2026-04-08-task-lifecycle-stop-drain-findings.md) | Task Lifecycle Stop/Drain Audit Findings | `findings` | none |

## Related Documents

- [`../specifications/README.md`](../specifications/README.md)
- [`../specifications/09-Implementation_Plan.md`](../specifications/09-Implementation_Plan.md)
- [`2026-04-15-maintainability-and-boundary-remediation-plan.md`](./2026-04-15-maintainability-and-boundary-remediation-plan.md)
