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
- Do NOT substitute a native harness subagent for this skill's Tier-1
  CLI review unless at least two bounded Tier-1 CLI attempts have failed
  and been logged in the plan's review section (recorded as `same-family
  fallback (degraded)` per `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`).

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
(review-eligible = liveness plus verified containment by either tool-surface
restriction or an enforced write denial, per step 6 — stronger than merely
present or usable). Calibrate the
invocation timeout to the size of the review unit before declaring a
CLI family unavailable. Only after two bounded, logged Tier-1 CLI
attempts fail may you fall back to Tier 2 (`same-family fallback
(degraded)` via a native harness subagent) or Tier 3 (disclosed author
fresh-eyes) per `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` —
never silently self-review or label a native subagent "cross-family".

### 2. Build the brief

Per review-loops §3–§4: embed the plan/delta content verbatim in the
prompt (do not assume the reviewer can find files), list the files it
should read, include the review stance (errors, bad ideas, latent
ambiguities, performative overengineering, analyzing the plan as a
whole including all amendments with two lenses — whether an expert
developer would think it well-crafted or elegant, and whether a known
algorithm or computer science principle should be applied — plus
verifying the causal premise in live code and checking that planned
tests are falsifiable), and demand explicit
[P1]/[P2] markers plus a PASS/BLOCKED verdict when gating (for plan
reviews, derived from the two gate questions in review-loops's Planning
Review Prompt; scoped-change reviews use `no blocker`/`blocker: F<ids>`).
Prepend a boundary line: "You are reviewing; do not implement or modify
anything. Stay within this repository's files."

**The brief is a required-shape artifact — a reviewer is only as good
as the frame it receives.** Every review brief states, explicitly:
(1) the **unit under review** — this delta at this baseline, never
"review this subsystem"; (2) **explicitly accepted risks**, named so
the reviewer cannot re-litigate them; (3) the **pre-existing rule** —
concerns predating the change are out-of-scope observations *unless
this change worsens them*; (4) the **disposition contract** — findings
carry *suggested* dispositions, expansions are labeled for owner
decision and never arrive as blockers (severity is the reviewer's
claim; blocking is a disposition-time decision); (5) **prefer removing
unnecessary work**; (6) an **observations outlet** — a separate,
non-actionable section for out-of-scope concerns, so broad looking is
encouraged while narrow acting is enforced; (7) **standing constraints
and refused alternatives in force** (`docs/program-theory.md`
`[ALT-WT-*]`, `docs/specifications/07-System_Invariants.md`, and governing spec
invariants) — if the reviewer believes a standing constraint or refused
alternative must be reversed, it must flag `RECONSIDERATION REQUESTED`
for human decision rather than proposing a silent reversal. A brief missing an element
is malformed: fix the brief, not the reviewer's output. The
fill-every-bracket template (and its round-2 variant) is the
Scoped Change Review Prompt section of
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` — filling the brackets IS the
scope decision; if a bracket resists filling, the scope is undecided
and the dispatch is premature. Two named anti-patterns:
**severity-as-mandate** (requesting "blocking findings" pre-decides
disposition) and **resolution-polling** (asking whether an
undispositioned or declined finding is "resolved" re-opens it — round-2
briefs enumerate accepted finding IDs only).

Write long prompts to a temp file and pass `"$(cat "$F")"` — never
fight shell quoting inline.

### 3. Invoke — review-mode table (read-only posture)

Never use delegation flags (`--dangerously-skip-permissions`,
`--dangerously-bypass-approvals-and-sandbox`, `-y`/yolo) in review
mode. **Prefer OS-enforced sandboxes over permission modes**: a
"plan"/approval mode is a politeness contract the model can break
(grok's plan mode wrote a file when asked, verified 2026-07-14); a
sandbox is enforced by the OS. A permission mode or model refusal alone is not proof of enforcement.
Either verify that the runtime exposes only read tools, or observe an actual
write attempt denied by the permission layer/sandbox (step 6). Do not require
a nonexistent write tool to be called. Verification column: how and when each
row was validated.

| Agent | Review invocation | Verified |
|-------|-------------------|----------|
| codex | `codex exec -s read-only -C "$(git rev-parse --show-toplevel)" "$PROMPT" --json` — OS-enforced sandbox; parse `item.completed`/`agent_message` events; add `-c 'model_reasoning_effort="high"'` for gating reviews | live, 2026-07-14 (4 review rounds) |
| grok | On this macOS/OrbStack host: `grok --cwd "$(git rev-parse --show-toplevel)" --sandbox review-read-only-macos --always-approve --disable-web-search --no-subagents --output-format json --prompt-file "$F" < /dev/null`; install the profile below first. `$F` must be **absolute**. Parse `.text`; require exit 0, nonempty text, and `.stopReason == "end_turn"` (older verified releases used `EndTurn`). A cancelled or empty result is a failure even with exit 0. Never use `--permission-mode plan` as containment: it auto-approves writes. | 1.0.77, 2026-11-11: absolute `--prompt-file` returned exit 0 / `end_turn`; native `read_file` succeeded; actual `write` tool failed with EPERM and sentinel stayed absent. OrbStack exception below still applies. |
| claude | `claude -p "$PROMPT" --safe-mode --permission-mode plan --tools "Read,Grep,Glob" --allowedTools "Read,Grep,Glob" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --output-format stream-json --verbose < /dev/null` — harness-level read-tool restriction; verify the emitted tool inventory as below | 2.1.293, 2026-11-11: init exposed only `Glob`, `Grep`, `Read`, no MCP servers; real `Read` succeeded; result succeeded; sentinel absent. No OS sandbox claim. |
| qwen | `qwen --approval-mode plan -o text "$PROMPT"` (positional prompt; `-p` deprecated) — **blocked** as of 2026-07-14: API 404, default model requires a paid slug; re-probe after config/billing change | blocked, 2026-07-14 |
| kimi | `kimi -p "$PROMPT" --output-format text` — **`--plan` cannot combine with `-p`** (probe-verified), so headless kimi has no containment mode at all: brief boundary only; write-attempt probe under plain `-p` still pending before any review eligibility | probe incomplete, 2026-07-14 |
| opencode | **Review eligibility revoked 2026-07-14**: the write-attempt probe succeeded — `opencode run` created a file in the repo despite the brief. Do not use for reviews until a containment flag or config is found and a re-probe passes | revoked, 2026-07-14 |
| agy (Antigravity) | From the repository root: `agy --mode plan --sandbox --disable-slash-commands --model gemini-4-argon-medium --output-format json --print-timeout 900s -p "$PROMPT" < /dev/null`. Use the native-read brief below. Use a Google model for cross-family review of OpenAI/Anthropic work; the CLI also lists Anthropic models, so the executable alone does not establish family diversity. Require exit 0, `.status == "SUCCESS"`, nonempty `.response`, and no `.denied_actions`. | agy 1.5.8, 2026-11-13 (probed in the `mm` checkout after re-applying the owner's Argon context patch): native read succeeded; requested `write_file` was auto-denied, the repository sentinel stayed absent and `git status` was unchanged; Argon qualified at 1 MB and Flash was still rejected at 900 KB. agy 1.5.7, 2026-11-12 (probed in the `mm` checkout after re-applying the owner's Argon context patch): native read succeeded; requested `write_file` was auto-denied (`denied_actions`), the repository sentinel stayed absent and `git status` was unchanged. A native-read review of an 850 KB fixture held it in one context (peak estimate 376K of 512K tokens, no truncation) and returned counts, ids and line citations that all checked out. agy 1.5.6, 2026-11-11 (probed in the `mm` checkout; owner-patched for Argon context): native read succeeded; requested `write_file` was auto-denied (`denied_actions`), the repository sentinel stayed absent and `git status` was unchanged, with and without `--disable-slash-commands`. agy 1.5.0, 2026-11-05 (from the `../weft` checkout): liveness passed; a write request left the repository sentinel absent and `git status` untouched (no `denied_actions`; plan mode wrote its plan under `~/.gemini/antigravity-cli/brain/` instead); a native-read probe succeeded. agy 1.4.4, 2026-10-21 (in `../mm`): real repository plan review succeeded using native reads/search; requested `write_file` was auto-denied and the repository sentinel was absent. Harness-level containment, not a verified OS read-only boundary. |

#### Claude: verify the available tools, not a willingness to write

`--allowedTools` grants permission; it does not remove other tools. The
`--tools "Read,Grep,Glob"` allowlist limits the built-in tool surface, while
`--strict-mcp-config --mcp-config '{"mcpServers":{}}'` removes MCP tools.
`--safe-mode` disables customizations such as hooks/plugins and automatic
CLAUDE.md loading. Include the relevant repository instructions and review
context explicitly in the brief; permit native file reads, not shell commands.
Claude 2.1.293 `--help` documents these safe-mode exclusions and says auth
works normally; the probe authenticated successfully. Do not use `--bare` as
a substitute: its help explicitly excludes OAuth/keychain authentication.

Capture stream JSON and stderr separately. In the `system` / `init` record,
verify the tool set is exactly `Read`, `Grep`, `Glob` (order does not matter),
`mcp_servers` is empty and `permissionMode` is `plan`. Confirm a real `Read`
tool call succeeds. Require process exit 0, a successful final `result` with
nonempty review text, and `is_error=false`; inspect permission denials for
missing review inputs. A model saying “I cannot write” is not the proof: the
runtime tool inventory is. If it contains unexpected tools, correct the
invocation before reviewing rather than trusting the prompt's restrictions.

#### Grok: select headless mode explicitly

Use the table's **absolute `--prompt-file`** argument and `< /dev/null`.
`--prompt` is not a valid flag. A positional prompt selects the interactive
path; it is not an equivalent spelling. On this host, Grok 1.0.77 returned an
immediate `Operation not permitted` for the positional invocation while the
same flags with `--prompt-file` succeeded. That message alone does not locate
a sandbox failure. Check the invocation mode before changing sandbox config
or declaring Grok unavailable. This comparison and an actual denied write were
rerun on 2026-11-11; the sibling `taut` skill/inventory already used the working
recipe. Do not relax containment to repair an invocation-mode error.

#### Grok on this macOS/OrbStack host

The owner accepted OrbStack access on 2026-10-20 (first recorded in
`../mm`'s call-agent skill) and extended it to this repository's reviews
on 2026-11-05. This is a machine-specific exception, not a claim that
Docker cannot modify the repository. Direct filesystem writes remain
OS-restricted; Docker access can provide an indirect write path. Do not
describe this profile as complete write isolation.

Add this profile to `~/.grok/sandbox.toml`, preserving any other profiles
(it is already installed on this host):

```toml
[profiles.review-read-only-macos]
extends = "read-only"
restrict_network = false
```

Grok 1.0.60, 1.0.62 and 1.0.70 refuse to start with the built-in
`read-only` profile when `/var/run/docker.sock` links to
`~/.orbstack/run/docker.sock`. Stderr reads `could not resolve
runtime-socket deny path /var/run/docker.sock: endpoint is a symlink to a
path outside the deny list`, then `Refusing to start with its protections
missing`, and grok exits 1. The automatic runtime-socket deny resolver
rejects that target. A custom `deny` entry does not repair the resolver.
The profile above retains the native read-only filesystem grants and
skips the failing automatic container-socket scan. On macOS,
`restrict_network` does not otherwise enforce child-process network
blocking. This workaround is **macOS-only**; use the built-in `read-only`
profile on other hosts and probe it there.

Grok 1.0.62 and later fail closed when the sandbox cannot be installed
(re-confirmed on 1.0.70). Earlier versions were observed to continue
unenforced. In either case, a sandbox application/initialization failure
on stderr invalidates the invocation. Ordinary warnings about blocked
configuration writes or plugin discovery are not sandbox-installation
failures. `/tmp` and Grok's own runtime state remain writable;
`--disable-web-search` removes built-in web tools, not all network
access.

Probe evidence: on 2026-10-20 (grok 1.0.62, in `../mm`) a probe attempted
one new repository sentinel and connected to both socket paths without
sending requests: write denied, both connections allowed, sentinel
absent. On 2026-11-05 (grok 1.0.70, from the `../weft` checkout) the row's
command, asked to create a repository sentinel, returned `end_turn` with
the write failing on EPERM; the sentinel was absent and stderr was empty.
No Docker configuration or socket link changed.

#### Antigravity (`agy`) result and containment checks

`agy`/Argon replaces the deprecated Gemini CLI as the Google Tier-1
reviewer. Do not invoke the old `gemini` executable. The verified model ID
is `gemini-4-argon-medium`; its spelling does not refer to the retired CLI.

`--mode plan --sandbox` is the tested review invocation. Do not add
`--dangerously-skip-permissions`, command allow-rules, or write allow-rules to make a review
proceed. `--sandbox` advertises terminal restrictions; the observed write
block was a headless permission denial, not proof of kernel-enforced
read-only access. Re-probe after version or permission-setting changes.

**Advertised skills.** agy 1.5.6 advertises the repository's
`.agents/skills`, the global `~/.gemini/skills` and installed plugin skills
(for example Notion) to every session, whatever flags are passed. The
reviewer can list them and, through native reads, read any skill file in the
repository. `--disable-slash-commands` suppresses slash-command and skill
expansion in print mode; it leaves native reads and the denied-write result
unchanged, and it does not hide skill names. Treat advertised skill
descriptions as foreign text in the reviewer's context, and keep the brief's
instruction not to invoke skills.

**Version and context.** The installed agy may be an owner-patched build in
which Argon reads the saved 512K context setting
(`~/.codex/memories/agy-context-window-fix.md`); stock builds cap Argon at
256K tokens, and every build caps a single `-p` prompt at 64K tokens, so keep
the brief short and point the reviewer at files. Native reads count against
the same cap. When a review's reads pass it, agy checkpoints, drops earlier
evidence (`truncation_reason` in the conversation metadata) and carries on.
On 2026-11-12 a stock 1.5.7 bundle review truncated this way (estimated 292K
of 256K tokens) and returned a PASS citing text absent from the bundle.
An agy auto-update replaces the patched build with a stock one. Before a
large review, check `codesign -dv "$(command -v agy)"`: `Signature=adhoc`
is the patched build and a `TeamIdentifier` is stock. On a stock build,
check a large review's citations against the files before counting it.
After an agy update, re-run the read and denied-write probes before relying
on this row.

Headless plan mode permits native file reads and search but denies
`RunCommand`. A generic instruction to verify identifiers "by command" can
therefore abort an otherwise valid review. Include the baseline SHA and any
required command evidence in the brief, followed by this instruction:

> Use only native file-reading and search tools (`read_file`, `grep_search`,
> `find_by_name`, `list_dir`). Do not invoke `RunCommand`, terminal commands,
> subagents, skills, or write tools. The baseline SHA is supplied; do not run git to
> obtain it. State any verification limits in the review.

No user settings change is needed for this recipe. If command evidence is
missing, the author supplies it under the existing task authorization;
the reviewer must not infer that the command ran or broaden its permissions.

On agy 1.4.4 (2026-10-21, in `../mm`) the write probe returned exit 0 and
`"status":"SUCCESS"` **with an empty response and
`denied_actions:[{"action":"write_file",...}]`**. That proves the attempted
write was blocked; the same result from a review is an incomplete review,
not a PASS. Preserve stderr, which explains the denial. The 1.4.4 recipe
was also verified with a real repository review there.

On agy 1.5.0 (2026-11-05, from the `../weft` checkout) the row's command,
asked to create a repository sentinel, returned exit 0 and
`"status":"SUCCESS"` with no `denied_actions` and empty stderr. Instead of
attempting the write, plan mode wrote an `implementation_plan.md` under
`~/.gemini/antigravity-cli/brain/<conversation_id>/` and returned a
`.response` pointing at it. The sentinel was absent and `git status` was
untouched. A review whose `.response` only points at such a plan file is
likewise incomplete, not a PASS. A native-read probe using the instruction
above succeeded on 1.5.0 (`read_file` only, no `RunCommand`).

Session continuity (optional, for multi-round reviews): codex
`exec resume <thread-id>` (id from the `thread.started` event) with
`-c 'sandbox_mode="read-only"'`; grok `-r <session-id>` (id in the `end`
event); kimi `-S <id>`; opencode `-s <id>`.

### 4. Run defensively (harvested hang/error handling)

- Wrap in a timeout (review: ~540s; agy: at least 960s, because its
  row sets `--print-timeout 900s`) and give the Bash call a larger
  outer timeout, running it in the background if your harness caps
  foreground calls below that; on exit 124 report a stall, do not
  retry blindly.
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

Check the exact CLI version, invocation and containment mechanism. Keep probes
bounded to 120 seconds each; actual reviews retain their 900-second allowance.
Reuse verified evidence for the same version, flags and environment rather than
requiring a destructive challenge before every review. Recheck after relevant
version, permission, tool or environment changes.

1. **Liveness and read access:** use the documented headless invocation. Ask for
   `PROBE-OK` and a read of a known non-secret repository file. Inspect actual
   tool calls/results and completion status, not merely the model's assertion.
2. **Containment:** use the test appropriate to the mechanism:
   - **Restricted tool surface (Claude recipe):** check the emitted init tool
     inventory and MCP inventory as specified above. A write tool is absent by
     design, so refusal or inability to attempt a write is expected, not a
     failed eligibility check. Safe-mode removes custom hooks/plugins; describe
     this as harness-level tool restriction, not OS filesystem isolation.
   - **Permission/sandbox enforcement (Grok, codex, agy recipes):** request one
     direct write to a unique, previously absent sentinel in the repository.
     Verify an actual tool attempt, its enforcement denial, and that the
     sentinel is absent. Never request Docker, shell escapes or alternate paths
     around a denial. Preserve unrelated working changes when comparing status.
3. **Classify the result:** an enforced denial or verified read-only tool
   inventory establishes containment. A model refusing before calling an
   available write tool is **inconclusive**, not a tool failure and not proof
   of containment. Use a verifiable restricted-tool invocation or a bounded
   corrected probe; do not downgrade a working reviewer solely because of that
   refusal. Earlier verification remains usable only when version, flags and
   environment are unchanged. Otherwise the recipe is **unverified, not failed**,
   until a corrected check succeeds; use the fallback policy if it cannot be
   verified. An actual successful write invalidates that recipe. A startup,
   authentication or sandbox-installation error is a separate execution failure.
4. **Record evidence:** version, exact command, mechanism, init/tool result,
   exit/completion status and sentinel result in the session inventory/review
   record. Retain the macOS Grok OrbStack exception below the invocation table:
   a denied direct write does not prove isolation from container-mediated writes.

Do not re-enable write/shell tools, bypass permissions or disable the sandbox
just to obtain a probe result. If neither containment mechanism can be verified,
report the precise remaining uncertainty and follow the reviewer fallback policy.

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
