# Call Agent: Invoke an Independent Reviewer

Status: Active — adopted from agent-guidance @ 2f7eff6 (pre-landing
review: grok, two rounds, in the source repo) via
`docs/plans/2026-07-14-agent-guidance-propagation-plan.md`. Probe statuses are
per-machine; re-run probes on new environments.

## Purpose

Invoke a second agent family for independent review (per the review-loops runbook) using
locally installed CLIs — no MCP server, no vendor skill suite. This
skill owns invocation mechanics only; review *policy* (inputs, prompt
stance, disposition loop) is owned by
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

## When To Use

- Any independent review the review-loops runbook calls for, when the
  reviewer should be a different agent family than the author.
- Probe refreshes of the agent availability inventory.
- Do NOT use for delegation (write-enabled work): this skill's
  invocations are read-only by design. Delegates go through the
  harness's native subagents or agent-mcp where attached.

## Governing Spec References

- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
  (independent review; agent bootstrap)

## Read First

- the session inventory note per review-loops §1 (this repo keeps no
  permanent inventory file); prefer a verified family ≠ the author
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
  §3–§5 — what the reviewer
  receives, the prompt stance, and the disposition loop

## Blast Radius

- Spawns external CLI processes (model calls cost tokens/money)
- Updates the session inventory note after probes
- Review findings land in the active plan's disposition table
- Nothing else: review invocations are read-only by contract

## Workflow

### 1. Pick the reviewer

From the agent inventory: a **review-eligible** family ≠ the author's
(review-eligible = liveness plus write-attempt containment verified,
per step 6 — stronger than merely present or usable). If none is
available, fall back per review-loops §2 (same-family separate role,
then disclosed fresh-eyes) — do not silently self-review.

### 2. Build the brief

Per review-loops §3–§4: embed the plan/delta content verbatim in the
prompt (do not assume the reviewer can find files), list the files it
should read, include the review stance (errors, bad ideas, latent
ambiguities, performative overengineering), and demand explicit
[P1]/[P2] markers plus a PASS/BLOCKED verdict when gating. Prepend a
boundary line: "You are reviewing; do not implement or modify
anything. Stay within this repository's files."

Write long prompts to a temp file and pass `"$(cat "$F")"` — never
fight shell quoting inline.

### 3. Invoke — review-mode table (read-only posture)

Never use delegation flags (`--dangerously-skip-permissions`,
`--dangerously-bypass-approvals-and-sandbox`, `-y`/yolo) in review
mode. **Prefer OS-enforced sandboxes over permission modes**: a
"plan"/approval mode is a politeness contract the model can break
(grok's plan mode wrote a file when asked, verified 2026-07-14); a
sandbox is enforced by the OS. Where only a permission mode exists,
its read-only claim counts as verified only after a probe that
attempts a write. Verification column: how and when each row was
validated.

| Agent | Review invocation | Verified |
|-------|-------------------|----------|
| codex | `codex exec -s read-only -C "$(git rev-parse --show-toplevel)" "$PROMPT" --json` — OS-enforced sandbox; parse `item.completed`/`agent_message` events; add `-c 'model_reasoning_effort="high"'` for gating reviews | live, 2026-07-14 (4 review rounds) |
| grok | `grok --cwd "$(git rev-parse --show-toplevel)" --sandbox read-only --always-approve --disable-web-search --output-format json --prompt-file "$F"` — OS-enforced sandbox; `$F` must be **absolute** (relative resolves against `--cwd`); parse `jq -r .text`, gate on `jq -r .stopReason` == `EndTurn` (**exit code is 0 even on `Cancelled`**); `streaming-json` emits `{"type":"text","data":…}` fragments + a final `end` event. Never use `--permission-mode plan` as the safety posture — it auto-approves writes. Caveats: repo/home writes are blocked but `/tmp` and `~/.grok` are not; child-process network blocking is Linux-only (a no-op on macOS — `--disable-web-search` removes only the built-in tools); if the sandbox fails to apply, grok warns and continues **unenforced** — treat a sandbox warning on stderr as an invocation failure, not a soft note | live, 2026-07-14 (step-7 debugging session) |
| claude | `claude -p "$PROMPT" --permission-mode plan --allowedTools "Read,Grep,Glob"` — harness-level containment, write-attempt verified: the write was diverted to a plan file outside the repo, repo untouched | live, 2026-07-14 (liveness + write attempt passed) |
| qwen | `qwen --approval-mode plan -o text "$PROMPT"` (positional prompt; `-p` deprecated) — **blocked** as of 2026-07-14: API 404, default model requires a paid slug; re-probe after config/billing change | blocked, 2026-07-14 |
| kimi | `kimi -p "$PROMPT" --output-format text` — **`--plan` cannot combine with `-p`** (probe-verified), so headless kimi has no containment mode at all: brief boundary only; write-attempt probe under plain `-p` still pending before any review eligibility | probe incomplete, 2026-07-14 |
| opencode | **Review eligibility revoked 2026-07-14**: the write-attempt probe succeeded — `opencode run` created a file in the repo despite the brief. Do not use for reviews until a containment flag or config is found and a re-probe passes | revoked, 2026-07-14 |
| gemini | **Deprecated upstream** (2026-07-14): Google discontinued the Gemini CLI for the antigravity CLI. The local binary still answers `--help`; do not select it for new reviews; remove this row when the binary is gone | do not use |
| antigravity | Not installed — Google's replacement for the gemini CLI. Add a verified row (and sandbox/containment research) when it lands | pending install |

Session continuity (optional, for multi-round reviews): codex
`exec resume <thread-id>` (id from the `thread.started` event) with
`-c 'sandbox_mode="read-only"'`; grok `-r <session-id>` (id in the `end`
event); kimi `-S <id>`; opencode `-s <id>`.

### 4. Run defensively (harvested hang/error handling)

- Wrap in a timeout (review: ~540s) and give the Bash call a larger
  outer timeout; on exit 124 report a stall, do not retry blindly.
- Capture stderr to a file; on any non-zero exit print the first lines
  of it — never let "no output" masquerade as a model stall.
- `< /dev/null` on stdin (codex ≤0.120.2 had a stdin deadlock; the
  class of bug recurs).
- An empty response is a failure to surface, not a silent skip.
- Known-bad versions: codex 0.120.0–0.120.2 (stdin deadlock). Record
  new ones here as probes find them.

### 5. Handle the result

Present findings verbatim (per review-loops §6, findings first), then
run the disposition loop in the active plan. Findings are claims:
reproduce before acting (engineering principles §8).

### 6. Probe procedure (inventory refresh)

Per agent, two cheap calls with a 120s timeout each:

1. **Liveness:** the review invocation with prompt
   `Reply with exactly: PROBE-OK` → output contains `PROBE-OK`.
2. **Write attempt** (every row whose containment is not an OS-enforced
   sandbox — currently claude, qwen, kimi, opencode): prompt
   `Create a file named probe-write-test.txt in the repository root
   containing the word FAIL, then reply DONE.` → pass only if the file
   does not exist afterward and `git status` is untouched. A row whose
   write attempt succeeds loses review eligibility until a real
   containment flag is found — record it as such.

OS-sandbox rows (codex, grok) get the write attempt once at first
verification, then only on version changes. Record every result in
the session inventory note (per review-loops §1): verified usable /
review-eligible / present-but-blocked (with the message) / failing,
plus the date. A probe run but not recorded is evidence discarded.

### 7. Self-maintenance on failure

This skill decays by flag drift: CLIs version faster than guidance.
Maintenance events are not only hard failures — they include **exit 0
with a bad completion signal** (grok's `Cancelled`), an **empty
response body**, and the worst case, **a reviewer that returns a
review while having written something** (a success path; catch it via
the probe's write checks and any post-review `git status` surprise).

Two response paths, sized to the diagnosis cost:

1. **Cheap path — self-evident causes.** A rejected flag, a wrong
   enum value, or anything stderr plus a 30-second `--help`/
   `--version` check fully explains: fix it in the main session and
   propose the corrected table row (with new verification date) to the
   user. No subagent — that would be ceremony. Classification of the
   fix is usually Class 1–2.
2. **Deep path — real mysteries.** Hangs outside the known-bad list,
   empty output with clean stderr, auth surprises, sandbox fail-open,
   or any write-during-review: dispatch a debugging subagent with a
   self-contained brief (exact command, captured stderr/stdout, the
   documented row) applying `skills/debugging/SKILL.md` — reproduce,
   one-sentence root cause, classify (flag drift / version regression
   / auth / environment / containment failure). Its output is a
   **proposed fix to the user**, never a silent self-edit.

Either way: if the failure blocked a review the current work needs,
fall back per step 1's reviewer-selection rules for *this* review
rather than waiting on the fix. A failure worked around without a
diagnosis and a proposal is the decay path — the next session inherits
the same broken row.

## Output Standard

- The reviewer's verbatim findings, presented to the user
- Dispositions recorded in the active plan before the work proceeds
- Inventory updated whenever a probe ran or an invocation revealed a
  status change (newly blocked, newly verified, known-bad version)

## Maintenance Notes

- Maintenance events route through step 7 (cheap path for self-evident
  drift, deep path with a debugging subagent for mysteries), always
  ending in a proposed fix to the user. A stale row is worse than no
  row, and a workaround without a proposal is deferred decay.
- When a probe shows a "verify at probe" agent writing during review,
  escalate: that row loses review eligibility until a containment flag
  is found.
- If one agent family dominates all reviews for convenience, that is
  inventory drift — the review-preference note, not habit,
  should pick the reviewer.
