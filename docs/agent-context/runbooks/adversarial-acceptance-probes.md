# Adversarial Acceptance Probes

This runbook defines a small, portable probe kit: the **default invariant
floors for agent-built developer tools, unless the governing spec explicitly
says otherwise**. The categories are portable; the specific behaviors are
defaults, not universals — some tools legitimately fail fast on one bad file,
and some warn on unknown config for forward compatibility. When a governing
spec overrides a floor, the probe tests the spec's stated behavior instead;
what is never optional is that the behavior is *stated and probed* rather
than left to whatever the implementation happened to do. The probes are
deliberately independent of spec version, and they exist because
implementations reliably pass their own test suites while failing exactly
these cases — the probes check what the author's imagination did not.

Owner: whoever integrates an implementation. Boundary: black-box only — run
the shipped entry point; never call internals. Verification: every probe
asserts an exit-code class and "no traceback on stderr" at minimum. Required
action: an implementation that fails any probe is not integration-ready,
regardless of its own suite passing.

When a repository's spec defines a contractual probe list (a verification
section naming required probes and their home, e.g. `tests/acceptance/`),
that list governs; this runbook is the generic pattern to derive it from.

## The Invariant Floors

1. **No traceback ever reaches the user.** Every failure path prints a
   one-line diagnostic naming the offending input where known. A traceback is
   a bug by definition, whatever caused it.
2. **Exit codes tell the truth, in classes.** "Problem found in the target"
   and "problem with the invocation or the tool" are different exit codes and
   must never blur. A crash that exits with the "findings exist" code lies to
   CI twice.
3. **One bad input never aborts the batch.** A single unreadable, non-UTF-8,
   or malformed file inside a scanned tree degrades to a per-file diagnostic;
   the rest of the output is still produced. Whole-run aborts are reserved
   for an unusable target, not one bad file inside it.
4. **The advertised default invocation works on the tool's own repository.**
   A tool whose README smoke command fails on its own repo ships a
   credibility defect, not a quirk.
5. **Declared contracts fire.** Every enumerable element the tool documents
   (issue codes, exit codes, config keys, flags) has at least one test that
   proves it fires or applies. Unreachable contract code is a deficiency even
   when nothing crashes.

## The Probe Checklist

Adapt the specifics; keep the categories.

- **Hostile encoding**: a non-UTF-8 or permission-denied file in the scanned
  tree → per-file diagnostic, run continues, full output produced.
- **Grammar mimicry**: content that *looks like* the tool's grammar in a
  context where it is inert — code examples inside fenced blocks (backtick
  AND tilde), quoted samples, string literals → no phantom records, no
  attribution hijacking. This is the single most reliable differentiator:
  parsers that pass everything else fail here.
- **Degenerate structured input**: input that parses but is missing required
  keys (`{"summary": {}}` where counts are expected) → clean invocation-error
  exit, not a KeyError.
- **Malformed structured input**: truncated JSON/JSONL, wrong top-level type
  → clean invocation-error exit naming file and line.
- **Unwritable output**: `--output` into a nonexistent or read-only directory
  → invocation-error exit, no partial file, no traceback.
- **Nonexistent target**: a missing target and a missing configured sub-path
  inside a real target are different failure classes — pin both, with the
  split the governing spec defines. (Backstitch's split, as an example:
  missing `--repo-root` → invocation-error exit; missing configured scan
  root inside a real repo → reported finding plus normal output. Another
  tool may legitimately define both as invocation errors — what the probe
  enforces is that the two cases are *deliberately* classified, not
  accidentally identical or accidentally different.)
- **Config honesty**: an unknown/typo'd config key → loud failure by default,
  never a silent no-op; every behavior-affecting key demonstrably changes
  output versus the no-config baseline.
- **Untrusted model/subprocess output** (when applicable): malformed output
  from a model or child process is contained per work item, never trusted for
  identifiers, and never aborts the batch.
- **Concurrency determinism** (when applicable): concurrent execution
  produces byte-identical output to serial execution.
- **Self-application**: run the tool on its own repository with its committed
  configuration and the documented default command; require the documented
  success state.

## Assertion Style

Probes pin structured fields exactly (codes, exit classes, paths) and assert
message text by substring only — never verbatim, and never by parsing values
back out of a message (see testing-patterns Pattern 8). Probes must stay
cheap and few: this is a surgical acceptance gate, not a second test suite.
When a probe fails in the wild, the failing case graduates into the tool's
own regression suite and the probe stays as the class-level guard.

## Why This Exists

In a four-way same-baseline implementation bake-off (2026-07-01, see
`docs/lessons.md`), all four implementations passed their own suites, lint,
and strict typing; every one of them failed at least one probe in this kit,
and no two failed the same ones. The probes found in one afternoon what
roughly 500 collective tests had not: the failures live precisely where the
author's assumptions end, which is why they must be authored from outside the
implementation.

## Runtime Adaptation (weft)

Weft is a task runtime, not a file-scanning tool. The floors translate:

- One bad task — or one malformed TaskSpec — never kills the manager or the
  batch. The failure degrades to a per-task diagnostic (task-log event, task
  exit status); the runtime keeps serving every other task.
- Output and exit honesty extends to task exit statuses and manager logs: a
  task failure and a runtime failure are different classes and must never
  blur — not in CLI exit codes (0/1/2/124), task terminal states, or log
  wording.
- The untrusted-subprocess-output and concurrency-determinism categories are
  central for weft, not "when applicable": every task is a subprocess whose
  output is untrusted, and concurrent managers, consumers, and watchers are
  the normal operating mode, not an edge case.
- The advertised-default-invocation floor applies to weft's own CLI on its
  own repository: the README smoke commands (`weft run`, `weft status`,
  `weft result`) must produce their documented success state here.
