# Plan Corpus

This directory holds implementation plans for behavior that is currently available in Weft or for repo tooling that still exists.
Specs in `docs/specifications/` remain the source of truth for behavior.

There are currently 56 tracked plan files in this directory.

## Curation Policy

- Keep plans that explain shipped behavior, current public surfaces, current extensions, or current release/test tooling.
- Remove plans that were superseded, exploratory only, audit-only, roadmap-only, process-only, or not implemented as written.
- Do not use this directory as a backlog. New unimplemented work should live here only while it is the active implementation slice, then be completed or removed during the next curation pass.
- Specs stay normative. Plans are historical execution records for how shipped behavior got there.

## Status Taxonomy

- `completed`: the planned slice landed and still describes current behavior or tooling.

## Index

| File | Title | Status | Superseded by |
| --- | --- | --- | --- |
| [`2026-04-27-built-in-run-input-adapters-plan.md`](./2026-04-27-built-in-run-input-adapters-plan.md) | Built-In Run Input Adapters Plan | `completed` | none |
| [`2026-04-24-runtime-handle-authority-migration-plan.md`](./2026-04-24-runtime-handle-authority-migration-plan.md) | Runtime Handle Authority Migration Plan | `completed` | none |
| [`2026-04-24-manager-status-container-pid-liveness-plan.md`](./2026-04-24-manager-status-container-pid-liveness-plan.md) | Manager Status Container PID Liveness Plan | `completed` | none |
| [`2026-04-21-weft-client-and-django-first-class-hardening-plan.md`](./2026-04-21-weft-client-and-django-first-class-hardening-plan.md) | Weft Client And Django First-Class Hardening Plan | `completed` | none |
| [`2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md`](./2026-04-21-run-boundary-dispatch-fence-control-contract-plan.md) | Run Boundary, Dispatch Fence, And Control Contract Plan | `completed` | none |
| [`2026-04-21-client-follow-hardening-plan.md`](./2026-04-21-client-follow-hardening-plan.md) | Client Follow Hardening Plan | `completed` | none |
| [`2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md`](./2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md) | Weft Client Pythonic Surface And CLI Path Unification Plan | `completed` | none |
| [`2026-04-17-heartbeat-service-plan.md`](./2026-04-17-heartbeat-service-plan.md) | Heartbeat Service Plan | `completed` | none |
| [`2026-04-17-canonical-owner-fence-plan.md`](./2026-04-17-canonical-owner-fence-plan.md) | Shared Canonical Owner Fence Plan | `completed` | none |
| [`2026-04-16-spec-corpus-and-readme-reality-alignment-plan.md`](./2026-04-16-spec-corpus-and-readme-reality-alignment-plan.md) | Spec Corpus And README Reality Alignment | `completed` | none |
| [`2026-04-16-runtime-endpoint-registry-boundary-plan.md`](./2026-04-16-runtime-endpoint-registry-boundary-plan.md) | Runtime Endpoint Registry Boundary Plan | `completed` | none |
| [`2026-04-16-pipeline-autostart-extension-plan.md`](./2026-04-16-pipeline-autostart-extension-plan.md) | Pipeline Autostart Extension Plan | `completed` | none |
| [`2026-04-16-docker-builtins-windows-guard-plan.md`](./2026-04-16-docker-builtins-windows-guard-plan.md) | Docker Builtins Windows Guard Plan | `completed` | none |
| [`2026-04-16-configurable-weft-directory-name-plan.md`](./2026-04-16-configurable-weft-directory-name-plan.md) | Configurable Weft Directory Name Plan | `completed` | none |
| [`2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](./2026-04-16-autostart-hardening-and-contract-alignment-plan.md) | Autostart Hardening And Contract Alignment Plan | `completed` | none |
| [`2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](./2026-04-15-spec-run-input-adapter-and-declared-args-plan.md) | Spec Run-Input Adapter And Declared Args Plan | `completed` | none |
| [`2026-04-15-spec-aware-run-help-plan.md`](./2026-04-15-spec-aware-run-help-plan.md) | Spec-Aware `weft run --spec ... --help` | `completed` | none |
| [`2026-04-15-parameterized-taskspec-materialization-plan.md`](./2026-04-15-parameterized-taskspec-materialization-plan.md) | Parameterized TaskSpec Materialization Plan | `completed` | none |
| [`2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](./2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md) | Multi-Provider Docker `provider_cli` Expansion Plan | `completed` | none |
| [`2026-04-14-system-builtins-command-plan.md`](./2026-04-14-system-builtins-command-plan.md) | System Builtins Command Plan | `completed` | none |
| [`2026-04-14-spawn-request-reconciliation-plan.md`](./2026-04-14-spawn-request-reconciliation-plan.md) | Spawn Request Reconciliation Plan | `completed` | none |
| [`2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md) | Provider CLI Validation Boundary and Agent Settings Alignment Plan | `completed` | none |
| [`2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](./2026-04-14-provider-cli-container-runtime-descriptor-plan.md) | Provider CLI Container Runtime Descriptor Plan | `completed` | none |
| [`2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](./2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md) | Docker Agent Images and One-Shot Provider CLI Plan | `completed` | none |
| [`2026-04-14-config-precedence-and-parsing-alignment-plan.md`](./2026-04-14-config-precedence-and-parsing-alignment-plan.md) | Config Precedence and Parsing Alignment Plan | `completed` | none |
| [`2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md) | Builtin TaskSpecs and Explicit Spec Resolution Plan | `completed` | none |
| [`2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](./2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md) | Builtin Contract And Doc Drift Reduction Plan | `completed` | none |
| [`2026-04-14-agent-runtime-package-refactor-plan.md`](./2026-04-14-agent-runtime-package-refactor-plan.md) | Agent Runtime Package Refactor Plan | `completed` | none |
| [`2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](./2026-04-13-spec-corpus-current-vs-planned-split-plan.md) | Spec Corpus Current-vs-Planned Split Plan | `completed` | none |
| [`2026-04-13-result-stream-implementation-plan.md`](./2026-04-13-result-stream-implementation-plan.md) | Result Stream Implementation Plan | `completed` | none |
| [`2026-04-13-pipeline-spec-expansion-plan.md`](./2026-04-13-pipeline-spec-expansion-plan.md) | Pipeline Spec Expansion Plan | `completed` | none |
| [`2026-04-13-pipeline-first-class-runtime-implementation-plan.md`](./2026-04-13-pipeline-first-class-runtime-implementation-plan.md) | First-Class Linear Pipeline Runtime Implementation Plan | `completed` | none |
| [`2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md`](./2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md) | Manager Bootstrap Readiness And Cleanup Test Plan | `completed` | none |
| [`2026-04-13-detached-manager-bootstrap-hardening-plan.md`](./2026-04-13-detached-manager-bootstrap-hardening-plan.md) | Detached Manager Bootstrap Hardening Plan | `completed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md) | Delegated Agent Runtime Phase 3 Implementation Plan | `completed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md) | Delegated Agent Runtime Phase 2 Implementation Plan | `completed` | none |
| [`2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md) | Delegated Agent Runtime Phase 1 Implementation Plan | `completed` | none |
| [`2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](./2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md) | Delegated Agent Authority Boundary Cleanup Plan | `completed` | none |
| [`2026-04-13-cli-surface-doc-and-help-alignment-plan.md`](./2026-04-13-cli-surface-doc-and-help-alignment-plan.md) | CLI Surface Doc And Help Alignment Plan | `completed` | none |
| [`2026-04-09-weft-serve-supervised-manager-plan.md`](./2026-04-09-weft-serve-supervised-manager-plan.md) | `weft manager serve` Supervised Manager Plan | `completed` | none |
| [`2026-04-09-manager-lifecycle-command-consolidation-plan.md`](./2026-04-09-manager-lifecycle-command-consolidation-plan.md) | Manager Lifecycle Command Consolidation Plan | `completed` | none |
| [`2026-04-09-manager-bootstrap-unification-plan.md`](./2026-04-09-manager-bootstrap-unification-plan.md) | Manager Bootstrap Unification Plan | `completed` | none |
| [`2026-04-07-active-control-main-thread-plan.md`](./2026-04-07-active-control-main-thread-plan.md) | Active Control Main-Thread Ownership Plan | `completed` | none |
| [`2026-04-06-weft-backend-neutrality-plan.md`](./2026-04-06-weft-backend-neutrality-plan.md) | Weft Backend Neutrality Plan | `completed` | none |
| [`2026-04-06-taskspec-clean-design-plan.md`](./2026-04-06-taskspec-clean-design-plan.md) | TaskSpec Clean Design Plan | `completed` | none |
| [`2026-04-06-simplebroker-backend-generalization-plan.md`](./2026-04-06-simplebroker-backend-generalization-plan.md) | SimpleBroker Backend Generalization Plan | `completed` | none |
| [`2026-04-06-runner-extension-point-plan.md`](./2026-04-06-runner-extension-point-plan.md) | Runner Extension Point Plan | `completed` | none |
| [`2026-04-06-release-helper-unpublished-version-reuse-plan.md`](./2026-04-06-release-helper-unpublished-version-reuse-plan.md) | Release Helper Unpublished Version Reuse Plan | `completed` | none |
| [`2026-04-06-release-helper-retag-plan.md`](./2026-04-06-release-helper-retag-plan.md) | Release Helper Retag Plan | `completed` | none |
| [`2026-04-06-release-helper-plan.md`](./2026-04-06-release-helper-plan.md) | Release Helper Plan | `completed` | none |
| [`2026-04-06-release-gated-tag-workflow-plan.md`](./2026-04-06-release-gated-tag-workflow-plan.md) | Release-Gated Tag Workflow Plan | `completed` | none |
| [`2026-04-06-postgres-extra-and-runtime-hint-plan.md`](./2026-04-06-postgres-extra-and-runtime-hint-plan.md) | Postgres Extra And Runtime Hint Plan | `completed` | none |
| [`2026-04-06-postgres-backend-audit-and-shared-test-surface-plan.md`](./2026-04-06-postgres-backend-audit-and-shared-test-surface-plan.md) | Postgres Backend Audit and Shared Test Surface Plan | `completed` | none |
| [`2026-04-06-piped-input-support-plan.md`](./2026-04-06-piped-input-support-plan.md) | Piped Input Support Plan | `completed` | none |
| [`2026-04-06-persistent-agent-runtime-implementation-plan.md`](./2026-04-06-persistent-agent-runtime-implementation-plan.md) | Persistent Agent Runtime Implementation Plan | `completed` | none |
| [`2026-04-06-agent-runtime-boundary-cleanup-plan.md`](./2026-04-06-agent-runtime-boundary-cleanup-plan.md) | Agent Runtime Boundary Cleanup Plan | `completed` | none |

## Related Documents

- [`../specifications/README.md`](../specifications/README.md)
- [`../specifications/09-Implementation_Plan.md`](../specifications/09-Implementation_Plan.md)
