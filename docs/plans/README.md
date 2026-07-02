# Plan Corpus

This directory holds implementation plans for behavior that is currently available in Weft or for repo tooling that still exists.
Specs in `docs/specifications/` remain the source of truth for behavior.

There are currently 148 plan files in this directory.

## Curation Policy

- Keep plans that explain shipped behavior, current public surfaces, current extensions, or current release/test tooling.
- Remove plans that were superseded, exploratory only, audit-only, roadmap-only, process-only, or not implemented as written.
- Do not use this directory as a backlog. New unimplemented work should live here only while it is the active implementation slice, then be completed or removed during the next curation pass.
- Specs stay normative. Plans are historical execution records for how shipped behavior got there.

## Status Taxonomy

- `completed`: the planned slice landed and still describes current behavior or tooling.
- `draft`: the plan is an active implementation slice or meta-plan that has
  not landed yet, or a retained historical draft whose `Superseded by` column
  points to the successor plan.

## Index

| File | Title | Status | Superseded by |
| --- | --- | --- | --- |
| [`2026-07-02-runtime-correctness-and-retention-remediation-plan.md`](./2026-07-02-runtime-correctness-and-retention-remediation-plan.md) | Runtime Correctness and Retention Remediation Plan | `draft` | none |
| [`2026-06-29-manager-task-spawned-retention-policy-plan.md`](./2026-06-29-manager-task-spawned-retention-policy-plan.md) | Manager Task-Spawned Retention Policy Plan | `completed` | none |
| [`2026-06-20-weft-django-terminal-status-monitor-store-plan.md`](./2026-06-20-weft-django-terminal-status-monitor-store-plan.md) | Weft Django Terminal Status Monitor Store Plan | `completed` | none |
| [`2026-06-18-hypothesis-property-testing-plan.md`](./2026-06-18-hypothesis-property-testing-plan.md) | Hypothesis Property-Based Testing Plan | `completed` | none |
| [`2026-06-17-microsandbox-runner-plan.md`](./2026-06-17-microsandbox-runner-plan.md) | Microsandbox Runner Implementation Plan | `completed` | none |
| [`2026-06-11-simplebroker-dump-load-adoption-plan.md`](./2026-06-11-simplebroker-dump-load-adoption-plan.md) | SimpleBroker Dump Load Adoption Plan | `completed` | none |
| [`2026-06-11-service-disposition-window-and-retirement-pacing-plan.md`](./2026-06-11-service-disposition-window-and-retirement-pacing-plan.md) | Service Disposition Window And Retirement Pacing Plan | `completed` | none |
| [`2026-06-10-self-healing-runtime-maintenance-plan.md`](./2026-06-10-self-healing-runtime-maintenance-plan.md) | Self-Healing Runtime Maintenance Plan | `completed` | none |
| [`2026-06-10-include-claimed-peek-migration-plan.md`](./2026-06-10-include-claimed-peek-migration-plan.md) | Include-Claimed Peek Surface — Implementation Plan | `completed` | none |
| [`2026-06-10-simplebroker-sidecar-migration-plan.md`](./2026-06-10-simplebroker-sidecar-migration-plan.md) | Sidecar Sessions & Public Watcher Surface — Implementation Plan | `completed` | none |
| [`2026-06-09-evaluation-findings-remediation-plan.md`](./2026-06-09-evaluation-findings-remediation-plan.md) | Evaluation Findings Remediation Plan | `completed` | none |
| [`2026-06-01-critical-review-remediation-plan.md`](./2026-06-01-critical-review-remediation-plan.md) | Critical Review Remediation Plan | `completed` | none |
| [`2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`](./2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md) | TaskMonitor Orphan Log And Status Reconciliation Plan | `completed` | none |
| [`2026-05-30-cleanup-progress-fifo-boundary-plan.md`](./2026-05-30-cleanup-progress-fifo-boundary-plan.md) | Cleanup Progress FIFO Boundary Plan | `completed` | none |
| [`2026-05-30-task-monitor-external-log-health-plan.md`](./2026-05-30-task-monitor-external-log-health-plan.md) | TaskMonitor External Log Health Plan | `completed` | none |
| [`2026-05-30-task-monitor-mode-and-rotating-log-plan.md`](./2026-05-30-task-monitor-mode-and-rotating-log-plan.md) | TaskMonitor Mode And Rotating Log Plan | `completed` | none |
| [`2026-05-29-task-monitor-general-lifetime-reporting-plan.md`](./2026-05-29-task-monitor-general-lifetime-reporting-plan.md) | TaskMonitor General Lifetime Reporting Plan | `completed` | none |
| [`2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`](./2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md) | TaskMonitor Config And Reactor Cache Cleanup Plan | `completed` | none |
| [`2026-05-29-reliability-and-doc-fixes-plan.md`](./2026-05-29-reliability-and-doc-fixes-plan.md) | Reliability And Doc Fixes Plan | `completed` | none |
| [`2026-05-28-docker-container-profiles-plan.md`](./2026-05-28-docker-container-profiles-plan.md) | Docker Container Profiles Plan | `completed` | none |
| [`2026-05-28-documentation-clarity-and-plan-curation-plan.md`](./2026-05-28-documentation-clarity-and-plan-curation-plan.md) | Documentation Clarity And Plan Curation Plan | `completed` | none |
| [`2026-05-28-stale-service-owner-runtime-cleanup-plan.md`](./2026-05-28-stale-service-owner-runtime-cleanup-plan.md) | Stale Service Owner Runtime Cleanup Plan | `completed` | none |
| [`2026-05-28-codebase-excellence-cleanup-plan.md`](./2026-05-28-codebase-excellence-cleanup-plan.md) | Codebase Excellence Cleanup Plan | `completed` | none |
| [`2026-05-27-service-collation-reporting-plan.md`](./2026-05-27-service-collation-reporting-plan.md) | Service Collation Reporting Plan | `completed` | none |
| [`2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`](./2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md) | Monitor Five Cleanup Policy Consolidation Plan | `completed` | none |
| [`2026-05-26-service-task-worker-api-plan.md`](./2026-05-26-service-task-worker-api-plan.md) | Service Task Worker API Plan | `completed` | none |
| [`2026-05-25-monitor-dead-task-catchup-convergence-plan.md`](./2026-05-25-monitor-dead-task-catchup-convergence-plan.md) | Monitor Dead-Task Catch-Up Convergence Plan | `completed` | none |
| [`2026-05-24-monitor-policy-progress-contract-plan.md`](./2026-05-24-monitor-policy-progress-contract-plan.md) | Monitor Policy Progress Contract Plan | `completed` | none |
| [`2026-05-23-monitor-cleanup-policy-convergence-plan.md`](./2026-05-23-monitor-cleanup-policy-convergence-plan.md) | Monitor Cleanup Policy Convergence Plan | `completed` | none |
| [`2026-05-23-monitor-cleanup-executor-plan.md`](./2026-05-23-monitor-cleanup-executor-plan.md) | Monitor Cleanup Executor Plan | `completed` | none |
| [`2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`](./2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md) | Monitor Policy Modules And Dead-Task Cleanup Plan | `completed` | none |
| [`2026-05-20-service-task-shared-reactor-extraction-plan.md`](./2026-05-20-service-task-shared-reactor-extraction-plan.md) | Service Task Shared Reactor Extraction Plan | `completed` | none |
| [`2026-05-20-simplebroker-api-adoption-plan.md`](./2026-05-20-simplebroker-api-adoption-plan.md) | SimpleBroker API Adoption Plan | `completed` | none |
| [`2026-05-20-monitor-collation-table-retirement-plan.md`](./2026-05-20-monitor-collation-table-retirement-plan.md) | Monitor Collation Table Retirement Plan | `completed` | none |
| [`2026-05-20-monitor-reactor-worker-refactor-plan.md`](./2026-05-20-monitor-reactor-worker-refactor-plan.md) | Monitor Reactor Worker Refactor Plan | `completed` | none |
| [`2026-05-20-monitor-fair-cleanup-scheduling-plan.md`](./2026-05-20-monitor-fair-cleanup-scheduling-plan.md) | Monitor Fair Cleanup Scheduling Plan | `completed` | none |
| [`2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](./2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md) | Monitor Terminal Retirement And Runtime Queue Cleanup Plan | `completed` | none |
| [`2026-05-19-task-monitor-control-cleanup-worker-plan.md`](./2026-05-19-task-monitor-control-cleanup-worker-plan.md) | Task Monitor Control Cleanup Worker Plan | `completed` | none |
| [`2026-05-19-task-monitor-bounded-control-cleanup-plan.md`](./2026-05-19-task-monitor-bounded-control-cleanup-plan.md) | Task Monitor Bounded Control Cleanup Plan | `completed` | none |
| [`2026-05-19-monitor-loop-batched-cleanup-plan.md`](./2026-05-19-monitor-loop-batched-cleanup-plan.md) | Monitor Loop Batched Cleanup Plan | `completed` | none |
| [`2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`](./2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md) | Monitor Table Driven Retained Log Cleanup Plan | `completed` | none |
| [`2026-05-18-reactive-task-loop-hot-probe-plan.md`](./2026-05-18-reactive-task-loop-hot-probe-plan.md) | Reactive Task Loop Hot-Probe Plan | `completed` | none |
| [`2026-05-18-monitor-cleanup-reserved-hot-path-plan.md`](./2026-05-18-monitor-cleanup-reserved-hot-path-plan.md) | Monitor Cleanup Reserved Hot Path Plan | `completed` | none |
| [`2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](./2026-05-16-task-log-external-logging-and-retention-policy-plan.md) | Task Log External Logging And Retention Policy Plan | `completed` | none |
| [`2026-05-16-monitor-store-hardening-and-layering-plan.md`](./2026-05-16-monitor-store-hardening-and-layering-plan.md) | Monitor Store Hardening And Layering Plan | `completed` | none |
| [`2026-05-16-monitor-durable-collation-store-plan.md`](./2026-05-16-monitor-durable-collation-store-plan.md) | Monitor Durable Collation Store Plan | `completed` | none |
| [`2026-05-15-swappable-task-log-family-scanner-plan.md`](./2026-05-15-swappable-task-log-family-scanner-plan.md) | Swappable Task Log Family Scanner Plan | `completed` | none |
| [`2026-05-15-manager-reactor-hot-loop-follow-up-plan.md`](./2026-05-15-manager-reactor-hot-loop-follow-up-plan.md) | Manager Reactor Hot-Loop Follow-Up Plan | `completed` | none |
| [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](./2026-05-15-task-reactor-and-evidence-worker-plan.md) | Task Reactor And Evidence Worker Plan | `completed` | none |
| [`2026-05-15-manager-hot-loop-reduction-plan.md`](./2026-05-15-manager-hot-loop-reduction-plan.md) | Manager Hot-Loop Reduction Plan | `draft` | [`2026-05-15-task-reactor-and-evidence-worker-plan.md`](./2026-05-15-task-reactor-and-evidence-worker-plan.md) |
| [`2026-05-14-manager-list-diagnostics-plan.md`](./2026-05-14-manager-list-diagnostics-plan.md) | Manager Liveness And List Diagnostics Plan | `completed` | none |
| [`2026-05-13-task-monitor-pong-policy-stats-plan.md`](./2026-05-13-task-monitor-pong-policy-stats-plan.md) | Task Monitor PONG Policy Stats Plan | `completed` | none |
| [`2026-05-13-service-convergence-throttle-plan.md`](./2026-05-13-service-convergence-throttle-plan.md) | Service Convergence Throttle Plan | `draft` | [`2026-05-15-manager-hot-loop-reduction-plan.md`](./2026-05-15-manager-hot-loop-reduction-plan.md) |
| [`2026-05-13-early-env-file-bootstrap-plan.md`](./2026-05-13-early-env-file-bootstrap-plan.md) | Early Env File Bootstrap Plan | `completed` | none |
| [`2026-05-13-internal-state-machine-helper-plan.md`](./2026-05-13-internal-state-machine-helper-plan.md) | Internal State Machine Helper Plan | `completed` | none |
| [`2026-05-13-manager-liveness-and-leadership-robustness-plan.md`](./2026-05-13-manager-liveness-and-leadership-robustness-plan.md) | Manager Liveness And Leadership Robustness Plan | `completed` | none |
| [`2026-05-13-manager-replace-start-serve-plan.md`](./2026-05-13-manager-replace-start-serve-plan.md) | Manager Replace Start And Serve Plan | `completed` | none |
| [`2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`](./2026-05-12-task-monitor-cleanup-composition-refactor-plan.md) | Task Monitor Cleanup Composition Refactor Plan | `completed` | none |
| [`2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`](./2026-05-12-bounded-task-monitor-cleanup-policy-plan.md) | Bounded Task Monitor Cleanup Policy Plan | `completed` | none |
| [`2026-05-11-canonical-service-reducer-fix-plan.md`](./2026-05-11-canonical-service-reducer-fix-plan.md) | Canonical Service Reducer Fix Plan | `completed` | none |
| [`2026-05-11-service-convergence-and-manager-registry-bounding-plan.md`](./2026-05-11-service-convergence-and-manager-registry-bounding-plan.md) | Service Convergence And Manager Registry Bounding Plan | `completed` | none |
| [`2026-05-11-internal-service-observability-plan.md`](./2026-05-11-internal-service-observability-plan.md) | Internal Service Observability Plan | `completed` | none |
| [`2026-05-11-manager-serve-operational-log-plan.md`](./2026-05-11-manager-serve-operational-log-plan.md) | Manager Serve Operational Log Plan | `completed` | none |
| [`2026-05-11-manager-work-stealing-dispatch-plan.md`](./2026-05-11-manager-work-stealing-dispatch-plan.md) | Manager Work-Stealing Dispatch Plan | `completed` | none |
| [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md) | Manager Service Authority Boundary Hardening Plan | `completed` | none |
| [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md) | Control And Service Convergence State Machine Plan | `completed` | none |
| [`2026-05-09-service-liveness-and-health-convergence-plan.md`](./2026-05-09-service-liveness-and-health-convergence-plan.md) | Service Liveness And Health Convergence Plan | `completed` | none |
| [`2026-05-09-prune-path-unification-plan.md`](./2026-05-09-prune-path-unification-plan.md) | Prune Path Unification Plan | `completed` | none |
| [`2026-05-09-runtime-liveness-probe-registry-plan.md`](./2026-05-09-runtime-liveness-probe-registry-plan.md) | Runtime Liveness Probe Registry Plan | `completed` | none |
| [`2026-05-09-managed-service-restart-clock-hardening-plan.md`](./2026-05-09-managed-service-restart-clock-hardening-plan.md) | Managed Service Restart Clock Hardening Plan | `completed` | none |
| [`2026-05-09-internal-spawn-priority-queue-plan.md`](./2026-05-09-internal-spawn-priority-queue-plan.md) | Internal Spawn Priority Queue Plan | `completed` | none |
| [`2026-05-09-manager-stop-timeout-hardening-plan.md`](./2026-05-09-manager-stop-timeout-hardening-plan.md) | Manager Stop Timeout Hardening Plan | `completed` | none |
| [`2026-05-08-agent-session-and-task-startup-observability-plan.md`](./2026-05-08-agent-session-and-task-startup-observability-plan.md) | Agent Session And Task Startup Observability Plan | `completed` | none |
| [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md) | Deterministic Manager Service Reconciler Plan | `completed` | none |
| [`2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](./2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md) | Phase 7 Manager Service Reconciler Cleanup Plan | `draft` | [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md) |
| [`2026-05-08-planning-guidance-zero-context-hardening-plan.md`](./2026-05-08-planning-guidance-zero-context-hardening-plan.md) | Planning Guidance Zero-Context Hardening Plan | `completed` | none |
| [`2026-05-08-manager-owned-internal-service-supervision-plan.md`](./2026-05-08-manager-owned-internal-service-supervision-plan.md) | Manager-Owned Internal Service Supervision Plan | `draft` | [`2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](./2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md) |
| [`2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](./2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md) | Phase 7 Task Monitor Supervision And Cleanup Plan | `completed` | none |
| [`2026-05-07-task-monitor-ping-logs-breaking-cleanup-plan.md`](./2026-05-07-task-monitor-ping-logs-breaking-cleanup-plan.md) | Task Monitor, Ping Flag, And Logs Directory Breaking Cleanup Plan | `completed` | none |
| [`2026-05-07-task-local-reaper-retention-policy-plan.md`](./2026-05-07-task-local-reaper-retention-policy-plan.md) | Task Local Reaper And Retention Policy Plan | `completed` | none |
| [`2026-05-07-runtime-state-pruning-plan.md`](./2026-05-07-runtime-state-pruning-plan.md) | Runtime State Pruning Plan | `completed` | none |
| [`2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md) | Lifecycle Monitor Archive Sink Plan | `completed` | none |
| [`2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](./2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md) | Result Evidence And Superseded Manager Reconciliation Plan | `completed` | none |
| [`2026-05-07-manager-selection-ping-pong-liveness-plan.md`](./2026-05-07-manager-selection-ping-pong-liveness-plan.md) | Manager Selection PING/PONG Liveness Plan | `completed` | none |
| [`2026-05-07-extended-ping-pong-state-probe-plan.md`](./2026-05-07-extended-ping-pong-state-probe-plan.md) | Extended PING/PONG State Probe Plan | `completed` | none |
| [`2026-05-06-terminal-publication-hardening-plan.md`](./2026-05-06-terminal-publication-hardening-plan.md) | Terminal Publication And Wrapper-Loss Hardening Plan | `completed` | none |
| [`2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md) | Task Evidence Reconciliation Model Plan | `completed` | none |
| [`2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md) | Status Coherence And Stale PID Liveness Plan | `completed` | none |
| [`2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md) | Lifecycle Reconciliation Architecture Plan | `completed` | none |
| [`2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md) | SimpleBroker Multi-Queue Waiter Integration | `completed` | none |
| [`2026-04-30-task-log-cursor-high-water-mark-plan.md`](./2026-04-30-task-log-cursor-high-water-mark-plan.md) | Task Log Cursor High-Water Mark Plan | `completed` | none |
| [`2026-04-30-known-tid-terminal-snapshot-api-plan.md`](./2026-04-30-known-tid-terminal-snapshot-api-plan.md) | Known-TID Terminal Snapshot API Plan | `completed` | none |
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
