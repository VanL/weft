# Agy Provider Replacement

Status: completed
Source specs: docs/specifications/13-Agent_Runtime.md [AR-2.1], [AR-5], [AR-7]; docs/specifications/10B-Builtin_TaskSpecs.md
Superseded by: none

Class: 4. This replaces one built-in delegated CLI execution surface, including
authority mapping, persistent-session continuation, release smoke coverage, and
runner capability declarations. The change is intentionally breaking: Gemini is
deprecated in favor of Agy and no compatibility alias remains.

## Goal

Remove the `gemini` provider and replace it with the host-backed `agy` provider.
Use Agy's structured print output to return plain text and to capture the exact
conversation ID for persistent continuation. Make the release live-provider gate
discover and exercise `agy`, not Gemini CLI.

## Contract

- The stable provider name and default executable are both `agy`.
- One-shot execution uses `agy --output-format json --print <prompt>`.
- Bounded or read-only execution adds `--mode plan`.
- Explicit models use `--model <model>`.
- Persistent execution captures `conversation_id` from the first JSON response
  and passes `--conversation <id>` on later turns. It never uses `--continue` or
  ambient latest-conversation state.
- Successful JSON requires `status="SUCCESS"`, a non-empty string `response`,
  and, for a first persistent turn, a non-empty `conversation_id`.
- `gemini` is removed from the registry, fixtures, builtins, specs, runtime
  descriptors, Docker image recipes, and live-provider discovery. No alias or
  backward-compatibility path is retained.
- Agy is host-backed only in this slice. Docker and Microsandbox capability
  surfaces must reject or omit it until a deterministic installation and auth
  recipe exists for those runners.

## Implementation And Verification

1. Add `AgyProvider` and provider-result session metadata; teach the session
   backend to retain the parsed ID before the next turn.
2. Replace Gemini fixture coverage with Agy argv, JSON parsing, bounded-mode,
   failure, and exact-conversation continuation coverage.
3. Remove Gemini-specific runtime-home code and descriptor. Update provider
   lists, builtins, specs, docs, and extension capability tests to the new
   host-only boundary.
4. Run focused provider, TaskSpec, builtin, Docker, and Microsandbox tests;
   Ruff, format, and mypy; then run the real `agy` live smoke and the complete
   release workflow.
5. Obtain an independent cross-family review before completion. Resolve any
   correctness finding before the release commit.

## Review Outcome

Two bounded Tier-1 cross-family attempts were unavailable: Claude Code could
not refresh its OAuth session, and Qwen's configured free model returned an
availability error. The permitted Tier-2 review found that Microsandbox still
accepted host-only Agy. The implementation now rejects Agy at Microsandbox
option validation, has a firing regression test, and states the boundary in
[AR-7]. The review's focused second pass returned PASS. The real Agy one-shot
and persistent release smokes both passed.

