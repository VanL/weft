# Agent Runtime Planned

This companion document tracks deferred agent-runtime surfaces that correspond
to [13-Agent_Runtime.md](13-Agent_Runtime.md). It contains only work that is
still intended but not part of the current canonical runtime contract.

## 1. Deferred Public Surfaces [AR-A1]

The current runtime keeps the public queue surface simple. Deferred public
extensions would need their own contract if they are ever introduced:

- approval policies
- transcript persistence
- semantic event streaming on the public outbox
- public `agent_result` wrappers

## 2. Deferred Runtime Behavior [AR-A2]

The current runtime already supports the shipped agent flow. Deferred behavior
would extend that contract only if new requirements justify it:

- restart-durable conversation state
- additional adapter kinds beyond the current `llm` path
- richer caller-visible execution metadata

## 3. Deferred Boundary Changes [AR-A3]

The current private/public split is intentional. Any future change here would
need to preserve that split while documenting the new boundary explicitly:

- new private session message types
- expanded persistent-session orchestration
- new result-shaping rules that remain internal until intentionally exposed

## Backlink

Canonical agent runtime behavior lives in
[13-Agent_Runtime.md](13-Agent_Runtime.md).
